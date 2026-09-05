# CryptoMind — module eToro : contrat d'interfaces (source de vérité pour tous les agents)

Contexte : CryptoMind est un bot de trading Python déployé sur Railway. Il possède déjà côté Kraken
`execution_service.py` (auth, cours, ordres) et `indicator_engine.py` (score de marché `market_score`, 0-100,
seuil d'exécution >= 70). Le code CryptoMind n'est PAS disponible localement : ce paquet est autonome et
n'importe rien de CryptoMind. Il expose des interfaces que CryptoMind branchera.

Langage : Python 3.11+. Dépendances autorisées : `httpx`, `pydantic>=2`, `pydantic-settings`, `fastapi`, `twilio`,
`pytest`, `pytest-asyncio`, `respx` (tests HTTP), `feedparser` (RSS). Rien d'autre sans raison forte.
Docstrings courtes. Aucun secret en dur. Tout vient de variables d'environnement (voir `etoro/config.py`).
Chaque agent ne touche QUE les fichiers qui lui sont assignés. `models.py` et `config.py` sont déjà écrits : ne pas les modifier,
mais signaler dans le rapport final si un champ manque.

## Arborescence
```
cryptomind-etoro/
  etoro/
    __init__.py
    config.py            # Settings pydantic, lecture env, KILL SWITCH
    models.py            # Types partagés
    etoro_service.py     # Client API eToro (miroir de execution_service.py)
    risk_guard.py        # Garde-fous de risque
    state_store.py       # Persistance état (positions, cooldowns, PnL jour, breaker) — JSON fichier
    rankings.py          # API Rankings eToro -> signal de confirmation
    news_feed.py         # News/sentiment : eToro si dispo sinon fallback RSS/NewsAPI -> score sentiment
    notifier.py          # Twilio SMS
    agent.py             # Agent Portfolio : boucle de décision
    api.py               # Router FastAPI : kill switch, status, health
  tests/
  docs/
  requirements.txt
  README.md
```

## `etoro/models.py` (déjà écrit — lire le fichier)
Side, Instrument, Quote, OrderRequest, Position, ClosedPosition, Signal, RiskDecision, DailyStats.

## `etoro/config.py` (déjà écrit — lire le fichier)
`Settings` + `get_settings()`.

## `etoro/etoro_service.py`
```python
class EtoroApiError(Exception): status_code: int | None ; payload: Any
class EtoroService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None): ...
    async def get_instruments(self, symbols: list[str]) -> list[Instrument]
    async def get_quote(self, instrument_id: int) -> Quote
    async def get_quotes(self, instrument_ids: list[int]) -> list[Quote]
    async def get_open_positions(self) -> list[Position]
    async def open_position(self, order: OrderRequest) -> Position   # refuse (ValueError) si SL/TP absents
    async def close_position(self, position_id: str) -> ClosedPosition
    async def get_account_balance(self) -> float   # equity du portfolio
    async def health(self) -> bool
    async def aclose(self)
```
Retry x3 avec backoff sur 5xx/429. Base URL demo vs real selon `settings.etoro_mode`.
En-têtes d'auth selon la doc publique eToro (builders.etoro.com) : `x-api-key`, `x-user-key`, `x-request-id`
(à vérifier et documenter dans `docs/etoro_api.md`).

## `etoro/state_store.py`
```python
class StateStore:
    # JSON atomique sur disque (write tmp + os.replace). Path = settings.etoro_state_path.
    open_positions_by_instrument: dict[int, str]   # instrument_id -> position_id
    last_trade_at: dict[int, datetime]
    daily_pnl: float
    daily_pnl_date: date | None
    equity_start_of_day: float
    breaker_until: datetime | None
    kill_switch: bool
    kill_switch_actor: str | None
    def __init__(self, path: str) ; def load(self) -> None ; def save(self) -> None
    def reset_day(self, equity: float, today: date) -> None
    def snapshot(self) -> dict   # sérialisable, pour /status
```

## `etoro/risk_guard.py`
```python
class RiskGuard:
    def __init__(self, settings: Settings, store: StateStore): ...
    def check_open(self, signal: Signal, order: OrderRequest, open_positions: list[Position],
                   equity: float, now: datetime | None = None) -> RiskDecision
    def on_position_closed(self, closed: ClosedPosition, now: datetime | None = None) -> RiskDecision
        # met à jour store.daily_pnl ; si perte jour > daily_loss_limit_pct % de store.equity_start_of_day
        # -> store.breaker_until = now + breaker_pause_hours ; retourne RiskDecision(allowed=False, reason="breaker_tripped") dans ce cas
    def is_breaker_active(self, now: datetime | None = None) -> bool
    def kill_switch_active(self) -> bool  # settings.etoro_agent_enabled is False OR store.kill_switch
```
Règles de `check_open`, dans cet ordre, chacune avec un `reason` stable en snake_case :
kill_switch -> breaker_active -> real_mode_without_rotation -> score_below_min -> missing_sl_tp -> invalid_sl_tp
(SL/TP du mauvais côté du prix d'ouverture estimé = order.entry_rate) -> instrument_not_in_universe -> max_positions_reached
-> position_already_open -> cooldown_active -> `allowed=True, reason="ok"`.
Utiliser des datetimes timezone-aware UTC partout.

## `etoro/rankings.py`
```python
async def get_confirmation(instrument_id: int, settings: Settings, client: httpx.AsyncClient) -> float | None
```
Interroge l'API Rankings eToro (période 12 mois), filtre traders : max drawdown <= 15 %, >= 12 mois d'historique,
profitable months >= 60 %, puis regarde parmi les N meilleurs (N = settings.rankings_top_n) combien sont exposés sur
l'instrument -> ratio 0..1. Retourne None si indisponible (jamais d'exception vers l'appelant).

## `etoro/news_feed.py`
```python
async def get_sentiment(symbol: str, settings: Settings, client: httpx.AsyncClient) -> float | None   # -1..1
```
Essaie eToro d'abord SI un endpoint news/sentiment public existe (documenter le résultat de la vérification dans
`docs/news_sources.md`), sinon NewsAPI (`settings.news_api_key`) puis flux RSS Yahoo Finance. Scoring lexical simple.

## `etoro/notifier.py`
```python
class Notifier:
    def __init__(self, settings: Settings, client=None)  # client Twilio injectable
    async def send_position_opened(self, pos: Position, instrument: Instrument | None = None) -> None
    async def send_position_closed(self, closed: ClosedPosition) -> None
    async def send_daily_summary(self, stats: DailyStats) -> None
    async def send_breaker_tripped(self, daily_pnl_pct: float) -> None
    async def send_kill_switch(self, actor: str, enabled: bool) -> None
```
Twilio SMS. No-op + log si Twilio non configuré. Ne jamais lever vers l'appelant (log l'erreur).

## `etoro/agent.py`
```python
SignalProvider = Callable[[], Awaitable[list[Signal]]]
class PortfolioAgent:
    def __init__(self, settings, service: EtoroService, guard: RiskGuard, store: StateStore, notifier: Notifier)
    async def run_once(self, signals: list[Signal]) -> list[RiskDecision]
    async def sync_positions(self) -> list[ClosedPosition]   # détecte les fermetures (SL/TP touchés)
    async def run_forever(self, signal_provider: SignalProvider, interval_s: int = 60) -> None
```
`run_once` : pour chaque signal, récupère le quote, construit `OrderRequest` (entry_rate = ask si BUY / bid si SELL,
SL = entry * (1 -/+ sl_pct), TP = entry * (1 +/- tp_pct), amount = equity * position_size_pct), passe par
`guard.check_open`, exécute, met à jour `store`, notifie. Enrichit optionnellement le signal avec rankings/news si
`settings.use_rankings_confirmation` / `settings.use_news_sentiment` (confirmation < 0.3 ou sentiment < -0.5 => skip).

## `etoro/api.py`
```python
router = APIRouter(prefix="/etoro", tags=["etoro"])
# POST /kill    (header X-Kill-Token == settings.etoro_kill_switch_token) -> store.kill_switch=True, notifie
# POST /resume  (même header) -> store.kill_switch=False
# GET  /status  -> store.snapshot() + breaker + kill switch + mode
# GET  /health
def create_app() -> FastAPI   # app autonome pour Railway si CryptoMind ne monte pas le router
```

## Journal des décisions de l'agent (ajout du 2026-09-05)

### `etoro/models.py` — `DecisionRecord` (déjà ajouté)
```python
class DecisionRecord(BaseModel):
    id: str                      # uuid4
    at: datetime                 # UTC
    kind: Literal["signal", "open", "close", "skip", "breaker", "kill_switch", "error", "sync"]
    symbol: str | None = None
    instrument_id: int | None = None
    side: Side | None = None
    market_score: float | None = None
    rankings_confirmation: float | None = None
    sentiment: float | None = None
    reason: str                  # snake_case (RiskDecision.reason, "rankings_not_confirmed", "opened", ...)
    action: str                  # texte court FR : "Ouverture BUY AAPL 100 USD @ 190.12", "Aucune action", ...
    position_id: str | None = None
    amount: float | None = None
    entry_rate: float | None = None
    stop_loss_rate: float | None = None
    take_profit_rate: float | None = None
    realized_pnl: float | None = None
    mode: str = "demo"
```

### `etoro/decision_log.py`
```python
class DecisionLog:
    def __init__(self, path: str, max_memory: int = 500)   # JSONL append-only + ring buffer mémoire
    def record(self, rec: DecisionRecord) -> DecisionRecord
    def recent(self, limit: int = 100, kind: str | None = None, symbol: str | None = None) -> list[DecisionRecord]  # plus récent en premier
    def load_tail(self) -> None   # recharge les max_memory dernières lignes au démarrage
```
Path = `settings.etoro_decisions_path` (défaut `/data/etoro_decisions.jsonl`). Jamais d'exception vers l'appelant.

### Hooks dans `etoro/agent.py`
`PortfolioAgent.__init__(..., decision_log: DecisionLog | None = None)`. Un `DecisionRecord` par : signal reçu (kind=signal),
refus (kind=skip, reason = RiskDecision.reason ou rankings_not_confirmed / negative_sentiment / execution_error),
ouverture (kind=open), fermeture détectée (kind=close, realized_pnl), breaker (kind=breaker), cycle refusé globalement
(kind=kill_switch ou breaker).

### `etoro/api.py`
`GET /etoro/decisions?limit=100&kind=&symbol=` -> `{"decisions": [DecisionRecord...], "count": n}` (plus récent en premier).
`configure(..., decision_log=None)`. Autowire crée le DecisionLog.

### Frontend
`frontend/EtoroAgent.jsx` : composant React autonome (pas de lib UI, CSS inline ou classes génériques), props
`{ apiBase = "" , pollMs = 5000 }`, interroge `${apiBase}/etoro/status` et `${apiBase}/etoro/decisions?limit=100`,
affiche : bandeau (mode, agent actif/coupé, breaker, PnL jour), puis flux des décisions (heure, instrument, signal
score/confirmation/sentiment, raison, action) avec code couleur par kind. Filtre par kind et symbole. Même esprit
que le suivi du bot Kraken (Portfolio.jsx / Trade.jsx / Market.jsx de CryptoMind, non disponibles ici).
