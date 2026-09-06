# CryptoMind — module eToro (actions + or)

Extension de CryptoMind (bot de trading Python déployé sur Railway, déjà actif sur Kraken) vers eToro :
actions US, ETF et or (XAU/USD). Le module est autonome (aucun import de CryptoMind) et expose des
interfaces que CryptoMind branche : il reçoit des `Signal` (score de marché produit par `indicator_engine`)
et gère tout le reste (confirmation, risque, exécution, état, alertes).

Il pilote un **Agent Portfolio eToro dédié**, jamais le compte principal. Démarrage en **démo** ;
passage à **1000 € réels** uniquement après validation de la [checklist démo → réel](docs/checklist_demo_vers_reel.md).

## Objectif

- Répliquer côté eToro la discipline acquise côté Kraken après l'incident d'avril (1599 trades en une journée).
- Ne prendre que des positions **peu nombreuses, protégées (SL/TP obligatoires) et confirmées** par des signaux externes.
- Pouvoir tout arrêter en une requête HTTP (kill switch) et repartir de zéro chaque jour (état persistant).
- Voir et piloter le tout depuis un **tableau de bord Netlify** : https://cryptomind-etoro.netlify.app (le moteur Python reste sur Railway avec CryptoMind, Netlify n'exécute pas de Python en continu).

## Architecture

```
etoro/
  config.py         Settings pydantic (env), verrou mode real, kill switch
  models.py         Types partagés : Signal, OrderRequest, Position, RiskDecision, DailyStats...
  etoro_service.py  Client HTTP eToro (quotes, instruments, positions, equity) — retry x3 sur 5xx/429
  rankings.py       API Rankings eToro -> ratio de confirmation 0..1
  news_feed.py      Sentiment news (eToro si dispo, sinon NewsAPI, sinon RSS Yahoo) -> -1..1
  risk_guard.py     Garde-fous : kill switch, breaker, score, SL/TP, univers, max positions, cooldown
  state_store.py    État JSON atomique (positions, cooldowns, PnL jour, breaker, kill switch)
  notifier.py       message Telegram (no-op si non configuré)
  agent.py          PortfolioAgent : boucle de décision run_once / sync_positions / run_forever
  api.py            FastAPI : /etoro/kill, /etoro/resume, /etoro/status, /etoro/positions, /etoro/health
dashboard/          Site Netlify (page + fonctions TS) qui relaie vers l'API ci-dessus
```

Flux d'un signal :

```
Signal (market_score, CryptoMind)
   │
   ├─► rankings.get_confirmation()   ratio traders top N exposés   (< 0.3  => skip)
   ├─► news_feed.get_sentiment()     score lexical news            (< -0.5 => skip)
   │
   ▼
PortfolioAgent.run_once()
   quote -> OrderRequest (entry, SL = entry ∓ sl_pct, TP = entry ± tp_pct, amount = equity × size_pct)
   │
   ▼
RiskGuard.check_open()  ──refus──►  RiskDecision(allowed=False, reason="...")
   │ ok
   ▼
EtoroService.open_position()  ──►  StateStore.save()  ──►  Notifier (SMS)
   │
   ▼ (boucle)
PortfolioAgent.sync_positions()  détecte SL/TP touchés -> RiskGuard.on_position_closed() -> PnL jour / breaker
```

## Garde-fous

Évalués dans cet ordre par `RiskGuard.check_open`, chacun avec un `reason` stable :

| Ordre | Règle | `reason` |
|---|---|---|
| 1 | Kill switch actif (`ETORO_AGENT_ENABLED=false` ou `POST /etoro/kill`) | `kill_switch` |
| 2 | Circuit breaker : perte jour > `ETORO_DAILY_LOSS_LIMIT_PCT` (3 %) => pause `ETORO_BREAKER_PAUSE_HOURS` (24 h) | `breaker_active` |
| 3 | Mode `real` sans `ETORO_CREDENTIALS_ROTATED=true` | `real_mode_without_rotation` |
| 4 | Score de marché < `ETORO_MIN_SCORE` (70) | `score_below_min` |
| 5 | Stop loss ou take profit absent | `missing_sl_tp` |
| 6 | SL/TP du mauvais côté du prix d'entrée | `invalid_sl_tp` |
| 7 | Instrument hors `ETORO_UNIVERSE` | `instrument_not_in_universe` |
| 8 | `ETORO_MAX_OPEN_POSITIONS` (3) atteint | `max_positions_reached` |
| 9 | Position déjà ouverte sur l'instrument | `position_already_open` |
| 10 | Cooldown `ETORO_COOLDOWN_HOURS` (4 h) depuis le dernier trade sur l'instrument | `cooldown_active` |

Plus : levier plafonné (`ETORO_MAX_LEVERAGE`, 1 par défaut, 5 max), taille de position plafonnée à 25 % de l'equity,
confirmations Rankings et sentiment news activables/désactivables.

## Hébergement (gratuit)

```
Navigateur ──► Netlify (page + fonctions, keepalive 10 min) ──► Render Free (FastAPI + agent) ──► eToro
                                                                     │
                                                                     └──► Supabase `cryptomind` (état + journal)
```

Railway a été abandonné (essai expiré). Le moteur tourne sur Render avec `ETORO_RUN_AGENT=true`,
l'état est persisté dans Supabase, l'interface reste sur Netlify. Voir docs/hosting_render.md.

## Univers restreint

Vérifié le 2026-09-06 via le connecteur eToro : sur ce compte (Belgique), l'or CFD (GOLD, id 18) et tous
les ETF américains (SPY, GLD, IAU, VOO, IVV, QQQ, GDX) sont **non ouvrables**. L'exposition à l'or passe
donc par deux minières aurifères, Newmont (NEM) et Agnico Eagle (AEM), toutes deux ouvrables.


Par défaut : **AAPL, MSFT, NVDA, AMZN, GOOGL, SPY, XAUUSD**.

Règle : on n'élargit l'univers (`ETORO_UNIVERSE`) **qu'après plusieurs semaines stables en démo**
(aucun breaker déclenché, win rate et drawdown conformes à la checklist). Un instrument à la fois.

## Installation locale

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows  (source .venv/bin/activate sur Linux/macOS)
pip install -r requirements.txt
cp .env.example .env          # puis remplir les valeurs — le fichier .env n'est jamais commité
python -m pytest
```

## Lancement

API (kill switch, status, health) :

```bash
uvicorn etoro.api:create_app --factory --host 0.0.0.0 --port 8000
```

Worker (boucle de décision). CryptoMind fournit le `signal_provider` (une coroutine qui renvoie `list[Signal]`
à partir d'`indicator_engine`) et branche le module ainsi :

```python
import asyncio
from etoro.config import get_settings
from etoro.etoro_service import EtoroService
from etoro.state_store import StateStore
from etoro.risk_guard import RiskGuard
from etoro.notifier import Notifier
from etoro.agent import PortfolioAgent

async def main():
    s = get_settings()
    store = StateStore(s.etoro_state_path); store.load()
    service = EtoroService(s)
    agent = PortfolioAgent(s, service, RiskGuard(s, store), store, Notifier(s))
    await agent.run_forever(signal_provider=cryptomind_signals, interval_s=60)

asyncio.run(main())
```

Si CryptoMind monte directement `etoro.api.router` sur son app FastAPI, le service `web` autonome
(`Procfile`) n'est pas nécessaire.

Endpoints :

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/etoro/kill` | Coupe le trading (header `X-Kill-Token`), notifie par SMS |
| `POST` | `/etoro/resume` | Réactive (même header) |
| `GET` | `/etoro/status` | Snapshot de l'état + breaker + kill switch + mode |
| `GET` | `/etoro/positions` | Positions ouvertes lues chez eToro, enrichies du symbole (si `EtoroService` injecté) |
| `GET` | `/etoro/health` | Healthcheck Railway |

## Variables d'environnement

Toutes lues par `etoro/config.py` (fichier `.env` en local, onglet Variables sur Railway).

| Variable | Défaut | Description |
|---|---|---|
| `ETORO_API_KEY` | *(vide)* | Clé API eToro. Obligatoire. |
| `ETORO_USER_KEY` | *(vide)* | User key eToro (en-tête `x-user-key`). |
| `ETORO_PORTFOLIO_ID` | *(vide)* | Identifiant de l'Agent Portfolio dédié. |
| `ETORO_TRADING_MODE` | `demo` | `demo` ou `real` (`ETORO_MODE` reste accepté comme ancien nom). |
| `ETORO_CREDENTIALS_ROTATED` | `false` | Verrou : `real` refuse de trader tant que ce n'est pas `true`. |
| `ETORO_BASE_URL_DEMO` | `https://public-api.etoro.com` | URL de base API en démo. |
| `ETORO_BASE_URL_REAL` | `https://public-api.etoro.com` | URL de base API en réel. |
| `ETORO_TIMEOUT_S` | `10.0` | Timeout HTTP (secondes). |
| `ETORO_AGENT_ENABLED` | `true` | `false` = kill switch permanent via la config. |
| `ETORO_KILL_SWITCH_TOKEN` | *(vide)* | Token attendu dans `X-Kill-Token` pour `/etoro/kill` et `/etoro/resume`. |
| `ETORO_MAX_OPEN_POSITIONS` | `3` | Positions simultanées max (1–5). |
| `ETORO_COOLDOWN_HOURS` | `4.0` | Délai minimal entre deux trades sur un même instrument. |
| `ETORO_DAILY_LOSS_LIMIT_PCT` | `3.0` | Perte journalière (% de l'equity de début de journée) qui déclenche le breaker. |
| `ETORO_BREAKER_PAUSE_HOURS` | `24.0` | Durée de pause après déclenchement du breaker. |
| `ETORO_MIN_SCORE` | `70.0` | Score de marché minimal pour exécuter (0–100). |
| `ETORO_SL_PCT` | `2.0` | Stop loss en % du prix d'entrée. |
| `ETORO_TP_PCT` | `4.0` | Take profit en % du prix d'entrée. |
| `ETORO_POSITION_SIZE_PCT` | `10.0` | Taille de position en % de l'equity (max 25). |
| `ETORO_MAX_LEVERAGE` | `1` | Levier maximal (1–5). |
| `ETORO_UNIVERSE` | `AAPL,MSFT,NVDA,AMZN,GOOGL,META,NEM,AEM` | Symboles autorisés, séparés par des virgules. |
| `ETORO_USE_RANKINGS` | `true` | Active la confirmation par l'API Rankings. |
| `ETORO_RANKINGS_TOP_N` | `20` | Nombre de meilleurs traders examinés (1–100). |
| `ETORO_RANKINGS_MAX_DD_PCT` | `15.0` | Drawdown max toléré pour retenir un trader. |
| `ETORO_RANKINGS_MIN_PROFITABLE_MONTHS_PCT` | `60.0` | Part minimale de mois profitables. |
| `ETORO_RANKINGS_MIN_CONFIRMATION` | `0.3` | Ratio de confirmation en dessous duquel le signal est ignoré (0–1). |
| `ETORO_USE_NEWS` | `true` | Active le filtre de sentiment news. |
| `NEWS_API_KEY` | *(vide)* | Clé NewsAPI (fallback si eToro n'expose pas de news). |
| `ETORO_NEWS_MIN_SENTIMENT` | `-0.5` | Sentiment en dessous duquel le signal est ignoré (−1–1). |
| `ETORO_STATE_PATH` | `/data/etoro_state.json` | Fichier d'état JSON (volume persistant sur Railway). |
| `TELEGRAM_ENABLED` | `true` | Active les notifications Telegram. |
| `TELEGRAM_BOT_TOKEN` | *(vide)* | Jeton du bot (@BotFather). |
| `TELEGRAM_CHAT_ID_TRADING` | *(vide)* | Chat des événements de trading. |
| `TELEGRAM_CHAT_ID_WATCH` | *(vide)* | Chat des alertes de veille (optionnel ici). |

Sans `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID_TRADING`, le `Notifier` est un no-op qui se contente de logger. Mise en place : notifications/README.md.

## Sécurité

- **Aucune clé dans le code.** Tout passe par les variables d'environnement.
- `.env` (et `.env.*`) sont dans `.gitignore` ; seul `.env.example` est versionné, sans valeur réelle.
- **Mode `real` verrouillé** : `Settings.real_mode_locked` est vrai tant que `ETORO_CREDENTIALS_ROTATED` n'est pas
  `true`. `RiskGuard` refuse alors tout ordre (`real_mode_without_rotation`). Ce verrou impose l'étape bloquante
  de la checklist : révoquer les identifiants complets utilisés en démo et poser une clé API scopée à l'Agent Portfolio.
- L'agent ne trade que sur l'Agent Portfolio (`ETORO_PORTFOLIO_ID`), jamais sur le compte principal.
- Les clés sont collées par l'utilisateur directement dans Railway ; elles ne transitent jamais par un assistant IA,
  un chat ou un fichier partagé.

## Kill switch

Trois niveaux, du plus rapide au plus définitif :

1. **HTTP** — `POST /etoro/kill` avec l'en-tête `X-Kill-Token: <ETORO_KILL_SWITCH_TOKEN>`. Persisté dans
   `StateStore` (survit aux redémarrages), SMS envoyé. Retour : `POST /etoro/resume`.
2. **Config** — `ETORO_AGENT_ENABLED=false` sur Railway, puis redéploiement.
3. **eToro** — révoquer la clé API depuis le portail développeurs.

```bash
curl -X POST https://<service>.up.railway.app/etoro/kill -H "X-Kill-Token: $ETORO_KILL_SWITCH_TOKEN"
curl https://<service>.up.railway.app/etoro/status
```

Le kill switch bloque les **ouvertures** ; les positions déjà ouvertes restent protégées par leurs SL/TP côté eToro.

## Documentation

- [Contrat d'interfaces](CONTRACT.md) — source de vérité des modules.
- [Hébergement Render](docs/hosting_render.md) — moteur gratuit, Blueprint, variables, keepalive.
- [Supabase](docs/supabase_setup.md) — état persistant du bot et requêtes utiles.
- [Dépôt GitHub](docs/github_setup.md) — création du dépôt et push.
- [Déploiement Railway](docs/railway_setup.md) — OBSOLÈTE, conservé pour référence.
- [Tableau de bord Netlify](docs/netlify_dashboard.md) — site cryptomind-etoro.netlify.app, variables Netlify, redéploiement.
- [Checklist démo → réel](docs/checklist_demo_vers_reel.md) — critères de sortie de démo et étape bloquante.
- `docs/etoro_api.md` — en-têtes et endpoints eToro vérifiés.
- `docs/news_sources.md` — sources de news retenues.
