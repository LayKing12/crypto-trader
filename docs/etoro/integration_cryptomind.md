# Brancher l'Agent Portfolio eToro dans CryptoMind

Ce document explique comment intégrer le paquet `etoro/` dans le bot CryptoMind (FastAPI sur Railway).
Le paquet est autonome : il n'importe rien de CryptoMind. C'est CryptoMind qui fournit les signaux
(via `indicator_engine`) et qui héberge la boucle de l'agent.

## Vue d'ensemble

```
indicator_engine (CryptoMind)        etoro/ (ce paquet)
  {symbol: market_score}   --->   signal_provider -> PortfolioAgent.run_forever
                                        |  sync_positions()   (SL/TP touchés -> RiskGuard.on_position_closed)
                                        |  run_once(signals)  (rankings/news -> quote -> OrderRequest
                                        |                      -> RiskGuard.check_open -> open_position)
                                        v
                                  StateStore (JSON) + Notifier (SMS Twilio)
FastAPI  <-- etoro.api.router : POST /etoro/kill, POST /etoro/resume, GET /etoro/status, GET /etoro/health
```

## 1. Copier le dossier `etoro/`

Copier `etoro/` à la racine du dépôt CryptoMind (au même niveau que `execution_service.py` et
`indicator_engine.py`) et ajouter les dépendances de `requirements.txt` (`httpx`, `pydantic>=2`,
`pydantic-settings`, `fastapi`, `twilio`, `feedparser`). Ne pas copier `tests/` en production.

## 2. Construire les composants au démarrage

Tout est injecté dans `PortfolioAgent` ; l'ordre de construction est : settings -> service -> store
-> guard -> notifier -> agent.

```python
# cryptomind/etoro_bootstrap.py
from etoro.agent import PortfolioAgent
from etoro.config import get_settings
from etoro.etoro_service import EtoroService
from etoro.notifier import Notifier
from etoro.risk_guard import RiskGuard
from etoro.state_store import StateStore


async def build_etoro_agent() -> tuple[PortfolioAgent, EtoroService]:
    settings = get_settings()                       # lit les variables d'environnement Railway
    service = EtoroService(settings)                # client HTTP eToro (demo ou real selon ETORO_TRADING_MODE)
    store = StateStore(settings.etoro_state_path)   # JSON atomique, volume Railway monté sur /data
    store.load()
    guard = RiskGuard(settings, store)              # garde-fous : score min, SL/TP, cooldown, breaker...
    notifier = Notifier(settings)                   # SMS Twilio, no-op si non configuré
    agent = PortfolioAgent(settings, service, guard, store, notifier)
    await agent.load_universe()                     # résout ETORO_UNIVERSE -> instrument_id eToro
    return agent, service
```

## 3. Fournir un `signal_provider`

Un `SignalProvider` est simplement `async def () -> list[Signal]`. Le plus simple est de réutiliser
`default_signal_provider_from_scores`, qui transforme le dict `{symbol: market_score}` produit par
`indicator_engine` en signaux **BUY** (voir « Pourquoi BUY seulement » plus bas).

```python
# cryptomind/etoro_signals.py
from etoro.agent import PortfolioAgent, default_signal_provider_from_scores
from etoro.models import Signal

import indicator_engine  # module existant de CryptoMind


def make_signal_provider(agent: PortfolioAgent):
    async def provider() -> list[Signal]:
        # Adapter cette ligne à la vraie API d'indicator_engine : on veut {symbol: score 0-100}
        scores: dict[str, float] = await indicator_engine.compute_scores(agent.settings.universe)
        return await default_signal_provider_from_scores(scores, agent)

    return provider
```

Si `indicator_engine` est synchrone, l'appeler via `asyncio.to_thread(...)`. Si vous voulez un mapping
sur mesure (par exemple ajouter un `sentiment` déjà calculé côté CryptoMind), construisez les `Signal`
vous-même :

```python
from datetime import UTC, datetime
from etoro.models import Side, Signal

def to_signal(agent, symbol: str, score: float, sentiment: float | None = None) -> Signal | None:
    inst = agent.instruments_by_symbol.get(symbol.upper())
    if inst is None:
        return None
    return Signal(instrument_id=inst.instrument_id, symbol=inst.symbol, side=Side.BUY,
                  market_score=score, sentiment=sentiment, generated_at=datetime.now(UTC))
```

Le seuil de score (`ETORO_MIN_SCORE`, 70 par défaut) est appliqué par `RiskGuard` (`score_below_min`) :
inutile de filtrer en amont, mais rien n'empêche de ne remonter que les scores >= 70 pour économiser des
appels de quote.

### Pourquoi BUY seulement en v1

`market_score` mesure la force haussière d'un actif ; il n'y a pas de score symétrique de faiblesse.
Le short sur eToro passe par des CFD (risque de perte illimitée, frais overnight, disponibilité variable
selon l'instrument). `default_signal_provider_from_scores` ne produit donc que des `Side.BUY`. Le reste de
l'agent (`build_order`, `RiskGuard`) gère déjà `Side.SELL` (entry = bid, SL au-dessus, TP en dessous) :
activer le short en v2 ne demande qu'un provider différent.

## 4. Lancer la boucle

### Option A : dans le lifespan FastAPI de CryptoMind (même service Railway)

```python
# cryptomind/main.py
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from etoro.api import router as etoro_router
from cryptomind.etoro_bootstrap import build_etoro_agent
from cryptomind.etoro_signals import make_signal_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent, service = await build_etoro_agent()
    app.state.etoro_agent = agent          # utile pour /etoro/status et le kill switch
    task = asyncio.create_task(agent.run_forever(make_signal_provider(agent), interval_s=60))
    try:
        yield
    finally:
        task.cancel()                      # run_forever absorbe CancelledError et s'arrête proprement
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await agent.aclose()
        await service.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(etoro_router)           # étape 5
```

`run_forever` capture toutes les exceptions d'un cycle (log + reprise au cycle suivant) : un incident
eToro ou un bug du provider ne fait pas tomber l'API CryptoMind.

### Option B : worker Railway séparé

Ajouter un second service Railway sur le même dépôt avec la commande de démarrage :

```
python -m etoro.agent
```

`etoro.agent.main()` construit les composants comme en étape 2 et lance `run_forever` avec un provider
**factice qui ne renvoie aucun signal** : ce worker ne trade pas, il ne fait que synchroniser les positions
(détection des SL/TP touchés, breaker, SMS) et envoyer le résumé quotidien. Pour trader depuis un worker,
créer un petit script qui reprend `main()` en remplaçant `_noop_signal_provider` par
`make_signal_provider(agent)`.

Dans les deux cas, monter un volume Railway sur `/data` (ou changer `ETORO_STATE_PATH`) : le `StateStore`
y persiste positions, cooldowns, PnL du jour, breaker et kill switch. Sans volume, un redéploiement perd
cet état.

## 5. Monter le router FastAPI

```python
from etoro.api import router as etoro_router
app.include_router(etoro_router)   # /etoro/kill, /etoro/resume, /etoro/status, /etoro/health
```

Le kill switch (`POST /etoro/kill` avec l'en-tête `X-Kill-Token`) met `store.kill_switch=True` : au cycle
suivant, `run_once` refuse tous les signaux sans appeler l'API eToro (`RiskDecision(reason="kill_switch")`).
`POST /etoro/resume` réactive. Si CryptoMind ne veut pas monter le router, `etoro.api.create_app()` fournit
une application autonome.

## 6. Variables d'environnement Railway

| Variable | Obligatoire | Défaut | Rôle |
|---|---|---|---|
| `ETORO_API_KEY` | oui | — | clé API eToro |
| `ETORO_USER_KEY` | selon eToro | — | clé utilisateur |
| `ETORO_PORTFOLIO_ID` | non | — | portfolio cible |
| `ETORO_TRADING_MODE` | non | `demo` | `demo` ou `real` |
| `ETORO_CREDENTIALS_ROTATED` | en `real` | `false` | doit être `true` en mode real, sinon trading bloqué |
| `ETORO_AGENT_ENABLED` | non | `true` | `false` = kill switch permanent |
| `ETORO_KILL_SWITCH_TOKEN` | oui si router monté | — | secret de `X-Kill-Token` |
| `ETORO_UNIVERSE` | non | `AAPL,MSFT,NVDA,AMZN,GOOGL,SPY,XAUUSD` | symboles autorisés |
| `ETORO_MAX_OPEN_POSITIONS` | non | `3` | max 5 |
| `ETORO_COOLDOWN_HOURS` | non | `4` | délai entre deux trades sur un instrument |
| `ETORO_DAILY_LOSS_LIMIT_PCT` | non | `3` | perte jour max avant breaker |
| `ETORO_BREAKER_PAUSE_HOURS` | non | `24` | durée de pause du breaker |
| `ETORO_MIN_SCORE` | non | `70` | seuil de `market_score` |
| `ETORO_SL_PCT` / `ETORO_TP_PCT` | non | `2` / `4` | stop loss / take profit en % du prix d'entrée |
| `ETORO_POSITION_SIZE_PCT` | non | `10` | taille d'une position en % de l'equity (max 25) |
| `ETORO_USE_RANKINGS` | non | `true` | confirmation via Rankings eToro |
| `ETORO_RANKINGS_MIN_CONFIRMATION` | non | `0.3` | ratio min de top traders exposés |
| `ETORO_USE_NEWS` | non | `true` | filtre sentiment news |
| `NEWS_API_KEY` | non | — | NewsAPI (fallback RSS sinon) |
| `ETORO_NEWS_MIN_SENTIMENT` | non | `-0.5` | sentiment min (-1..1) |
| `ETORO_STATE_PATH` | non | `/data/etoro_state.json` | fichier d'état (volume Railway) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `TWILIO_TO` | non | — | SMS ; no-op si absents |

Démarrer en `ETORO_TRADING_MODE=demo` et vérifier `GET /etoro/status` pendant quelques jours avant de passer en
`real` (qui exige en plus `ETORO_CREDENTIALS_ROTATED=true`).

## Ce que fait un cycle (`run_forever`, toutes les `interval_s` secondes)

1. `sync_positions()` : compare les positions connues du store avec `service.get_open_positions()`.
   Toute position disparue (SL/TP touché, fermeture manuelle) devient une `ClosedPosition`, passe par
   `RiskGuard.on_position_closed` (mise à jour du PnL jour, breaker éventuel), est retirée du store et
   notifiée par SMS. Si le breaker se déclenche, un SMS `send_breaker_tripped(pct)` part aussi.
2. `signals = await signal_provider()`.
3. `run_once(signals)` : refus global immédiat si kill switch ou breaker (aucun appel API) ; reset du PnL
   jour au changement de date UTC ; puis pour chaque signal par score décroissant : enrichissement
   rankings/news (imports paresseux, jamais bloquant), quote, `OrderRequest` (SL/TP arrondis à 4 décimales,
   `amount = equity * ETORO_POSITION_SIZE_PCT / 100`, levier 1), `RiskGuard.check_open`, ouverture,
   `store.record_open` + `save`, SMS. Une erreur sur un signal donne `execution_error` et n'arrête pas les
   autres. Une seule ouverture par instrument et par cycle.
4. Résumé quotidien (`send_daily_summary`) au premier cycle de chaque nouveau jour UTC.

## Exemple complet minimal

```python
import asyncio, contextlib
from contextlib import asynccontextmanager
from fastapi import FastAPI

from etoro.agent import PortfolioAgent, default_signal_provider_from_scores
from etoro.api import router as etoro_router
from etoro.config import get_settings
from etoro.etoro_service import EtoroService
from etoro.notifier import Notifier
from etoro.risk_guard import RiskGuard
from etoro.state_store import StateStore
import indicator_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    service = EtoroService(settings)
    store = StateStore(settings.etoro_state_path); store.load()
    agent = PortfolioAgent(settings, service, RiskGuard(settings, store), store, Notifier(settings))
    await agent.load_universe()

    async def provider():
        scores = await asyncio.to_thread(indicator_engine.compute_scores, settings.universe)
        return await default_signal_provider_from_scores(scores, agent)

    task = asyncio.create_task(agent.run_forever(provider, interval_s=60))
    app.state.etoro_agent = agent
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await agent.aclose(); await service.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(etoro_router)
```
