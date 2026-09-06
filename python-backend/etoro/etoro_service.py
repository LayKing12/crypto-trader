"""Client asynchrone de l'API publique eToro (miroir de `execution_service.py` côté Kraken).

Sources : https://builders.etoro.com et https://api-portal.etoro.com (voir docs/etoro_api.md).
Règles : jamais de clé API dans les logs, un `x-request-id` (uuid4) par appel, retry x3 avec
backoff exponentiel sur 5xx / 429 / timeouts, SL et TP natifs obligatoires à l'ouverture.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .models import ClosedPosition, Instrument, OrderRequest, Position, Quote, Side
from .models import Candle

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chemins d'API. Les entrées marquées "À VÉRIFIER" n'ont pas pu être confirmées
# intégralement sur la doc publique ; ajuster ici sans toucher au reste du code.
# ---------------------------------------------------------------------------
ENDPOINTS: dict[str, str] = {
    # Market data (confirmé : api-portal.etoro.com, groupe Market Data)
    "search": "/api/v1/market-data/search",
    "instruments": "/api/v1/market-data/instruments",
    "rates": "/api/v1/market-data/instruments/rates",
    # Bougies OHLC (confirmé : api-portal "get-instrument-candle-history")
    "candles": "/api/v1/market-data/instruments/{instrument_id}/history/candles/{direction}/{interval}/{count}",
    # Portfolio / PnL (confirmé : builders.etoro.com "from-demo-to-production")
    "portfolio_demo": "/api/v1/trading/info/demo/portfolio",
    "portfolio_real": "/api/v1/trading/info/portfolio",
    "pnl_demo": "/api/v1/trading/info/demo/pnl",
    "pnl_real": "/api/v1/trading/info/real/pnl",
    # Ouverture marché par montant (confirmé : api-portal, groupe Trading - Demo / Real)
    "open_by_amount_demo": "/api/v1/trading/execution/demo/market-open-orders/by-amount",
    "open_by_amount_real": "/api/v1/trading/execution/market-open-orders/by-amount",
    # Fermeture par positionId (confirmé : api-portal "close-demo-position-by-units")
    "close_position_demo": "/api/v1/trading/execution/demo/market-close-orders/positions/{position_id}",
    "close_position_real": "/api/v1/trading/execution/market-close-orders/positions/{position_id}",
    # Historique des trades (À VÉRIFIER : PnL réalisé exact après fermeture, param minDate=YYYY-MM-DD)
    "trade_history": "/api/v1/trading/info/trade/history",
    # Soldes agrégés (confirmé pour le compte principal ; À VÉRIFIER pour le compte démo)
    "balances": "/api/v1/balances",
    # Agent Portfolios (confirmé : GET liste ; À VÉRIFIER : création / génération de token)
    "agent_portfolios": "/api/v1/agent-portfolios",
}

# Alias symbole CryptoMind -> `internalSymbolFull` eToro. À VÉRIFIER : l'or est coté
# "GOLD" sur eToro (pas "XAUUSD"), l'id historique connu est 14.
SYMBOL_ALIASES: dict[str, str] = {"XAUUSD": "GOLD", "XAU/USD": "GOLD"}

# instrumentTypeID eToro -> asset_class de models.Instrument. À VÉRIFIER via
# GET /api/v1/market-data/instrument-types.
INSTRUMENT_TYPE_TO_ASSET_CLASS: dict[int, str] = {
    1: "forex",
    2: "commodity",
    4: "index",
    5: "stock",
    6: "etf",
    10: "crypto",
}

SEARCH_FIELDS = "instrumentId,internalSymbolFull,displayname,instrumentTypeID"

# Statuts HTTP déclenchant un retry (429 + toute la famille 5xx).
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class EtoroApiError(Exception):
    """Erreur renvoyée par l'API eToro (ou échec réseau après retries)."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None,
                 request_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.request_id = request_id

    def __str__(self) -> str:  # jamais de secret ici
        base = super().__str__()
        return f"{base} (status={self.status_code}, request_id={self.request_id})"


def _get(d: dict[str, Any], *names: str, default: Any = None) -> Any:
    """Lit la première clé présente, insensible à la casse (eToro mélange ID/Id)."""
    if not isinstance(d, dict):
        return default
    lower = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = lower.get(n.lower())
        if v is not None:
            return v
    return default


def _parse_dt(value: Any) -> datetime:
    """ISO 8601 (suffixe Z accepté) -> datetime UTC aware ; défaut : maintenant."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Python < 3.11 n'aime pas plus de 6 décimales ; on tronque prudemment.
        if "." in s:
            head, _, tail = s.partition(".")
            frac = "".join(ch for ch in tail if ch.isdigit())
            rest = tail[len(frac):]
            s = f"{head}.{frac[:6]}{rest}"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _positive(value: Any) -> float | None:
    """Convertit un taux eToro en float, None si absent/0 (0 = 'pas de SL/TP' côté eToro)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


class EtoroService:
    """Client eToro : instruments, cours, positions, ouverture/fermeture, equity."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        max_attempts: int = 3,
        backoff_base_s: float = 0.5,
        position_poll_attempts: int = 5,
        position_poll_delay_s: float = 1.0,
    ):
        self._settings = settings
        self._base_url = settings.base_url.rstrip("/")
        self._demo = settings.etoro_mode != "real"
        self._timeout = httpx.Timeout(settings.etoro_timeout_s)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._max_attempts = max(1, max_attempts)
        self._backoff_base_s = max(0.0, backoff_base_s)
        self._position_poll_attempts = max(1, position_poll_attempts)
        self._position_poll_delay_s = max(0.0, position_poll_delay_s)

    # ------------------------------------------------------------------ utils
    @property
    def mode(self) -> str:
        return "demo" if self._demo else "real"

    def _path(self, key: str, **fmt: Any) -> str:
        """Résout une clé d'ENDPOINTS selon le mode (suffixe _demo/_real si présent)."""
        scoped = f"{key}_{self.mode}"
        path = ENDPOINTS.get(scoped, ENDPOINTS.get(key))
        if path is None:
            raise KeyError(f"endpoint inconnu : {key}")
        return path.format(**fmt) if fmt else path

    def _headers(self, request_id: str) -> dict[str, str]:
        headers = {
            "x-api-key": self._settings.etoro_api_key,
            "x-request-id": request_id,
            "accept": "application/json",
            "content-type": "application/json",
        }
        if self._settings.etoro_user_key:
            headers["x-user-key"] = self._settings.etoro_user_key
        return headers

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                       json: Any = None) -> Any:
        """Appel HTTP avec retry x`max_attempts` (backoff exponentiel) sur 5xx/429/timeouts."""
        url = f"{self._base_url}{path}"
        request_id = str(uuid.uuid4())
        last_error: EtoroApiError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await self._client.request(
                    method, url, params=params, json=json,
                    headers=self._headers(request_id), timeout=self._timeout,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = EtoroApiError(
                    f"{method} {path} : erreur réseau ({type(exc).__name__})",
                    status_code=None, payload=None, request_id=request_id,
                )
                log.warning("eToro %s %s tentative %d/%d : %s", method, path, attempt,
                            self._max_attempts, type(exc).__name__)
            else:
                if resp.status_code < 400:
                    return self._decode(resp)
                payload = self._decode(resp)
                if resp.status_code not in _RETRY_STATUSES:
                    raise EtoroApiError(
                        f"{method} {path} : HTTP {resp.status_code}",
                        status_code=resp.status_code, payload=payload, request_id=request_id,
                    )
                last_error = EtoroApiError(
                    f"{method} {path} : HTTP {resp.status_code} après {attempt} tentative(s)",
                    status_code=resp.status_code, payload=payload, request_id=request_id,
                )
                log.warning("eToro %s %s tentative %d/%d : HTTP %d (request_id=%s)", method, path,
                            attempt, self._max_attempts, resp.status_code, request_id)
                retry_after = resp.headers.get("Retry-After")
            if attempt < self._max_attempts:
                delay = self._backoff_base_s * (2 ** (attempt - 1))
                if last_error and last_error.status_code == 429:
                    try:
                        delay = max(delay, float(retry_after or 0))
                    except ValueError:
                        pass
                if delay > 0:
                    await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _decode(resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ------------------------------------------------------------- instruments
    async def get_instruments(self, symbols: list[str]) -> list[Instrument]:
        """Résout les ids eToro par symbole via /market-data/search (alias XAUUSD -> GOLD)."""
        results = await asyncio.gather(*(self._search_symbol(s) for s in symbols))
        return [inst for inst in results if inst is not None]

    async def _search_symbol(self, symbol: str) -> Instrument | None:
        wanted = symbol.strip().upper()
        etoro_symbol = SYMBOL_ALIASES.get(wanted, wanted)
        data = await self._request("GET", self._path("search"), params={
            "internalSymbolFull": etoro_symbol, "fields": SEARCH_FIELDS, "pageSize": 10,
        })
        items = _get(data, "items", "results", default=[]) if isinstance(data, dict) else data
        for item in items or []:
            if str(_get(item, "internalSymbolFull", "symbolFull", "symbol", default="")).upper() == etoro_symbol:
                return self._map_instrument(item, wanted)
        # Repli : premier résultat si l'API ignore le filtre exact
        if items:
            log.warning("eToro search %s : pas de correspondance exacte, premier résultat utilisé", wanted)
            return self._map_instrument(items[0], wanted)
        log.warning("eToro search %s : aucun instrument trouvé", wanted)
        return None

    @staticmethod
    def _map_instrument(item: dict[str, Any], symbol: str) -> Instrument:
        type_id = _get(item, "instrumentTypeID", "instrumentTypeId")
        try:
            asset_class = INSTRUMENT_TYPE_TO_ASSET_CLASS.get(int(type_id), "stock")
        except (TypeError, ValueError):
            asset_class = "stock"
        return Instrument(
            symbol=symbol,
            instrument_id=int(_get(item, "instrumentId", "instrumentID")),
            display_name=str(_get(item, "displayname", "displayName", default="")),
            asset_class=asset_class,
        )

    # ------------------------------------------------------------------ quotes
    async def get_quote(self, instrument_id: int) -> Quote:
        quotes = await self.get_quotes([instrument_id])
        for q in quotes:
            if q.instrument_id == instrument_id:
                return q
        raise EtoroApiError(f"aucun cours renvoyé pour l'instrument {instrument_id}", payload=quotes)

    async def get_quotes(self, instrument_ids: list[int]) -> list[Quote]:
        """GET /market-data/instruments/rates?instrumentIds=1,2 -> {"rates": [...]}."""
        if not instrument_ids:
            return []
        data = await self._request("GET", self._path("rates"), params={
            "instrumentIds": ",".join(str(i) for i in instrument_ids),
        })
        rates = _get(data, "rates", default=[]) if isinstance(data, dict) else data
        quotes: list[Quote] = []
        for r in rates or []:
            try:
                quotes.append(Quote(
                    instrument_id=int(_get(r, "instrumentID", "instrumentId")),
                    bid=float(_get(r, "bid")),
                    ask=float(_get(r, "ask")),
                    timestamp=_parse_dt(_get(r, "date", "timestamp", "lastUpdate")),
                ))
            except (TypeError, ValueError) as exc:
                log.warning("eToro rates : entrée ignorée (%s)", exc)
        return quotes

    # ----------------------------------------------------------------- candles
    async def get_candles(self, instrument_id: int, interval: str = "FifteenMinutes",
                          count: int = 250) -> list[Candle]:
        """GET /market-data/instruments/{id}/history/candles/asc/{interval}/{count}.

        interval : OneMinute, FiveMinutes, TenMinutes, FifteenMinutes, ThirtyMinutes,
        OneHour, FourHours, OneDay, OneWeek. count : 1..1000. Retourne du plus ancien au plus récent.
        """
        count = max(1, min(int(count), 1000))
        data = await self._request("GET", self._path(
            "candles", instrument_id=instrument_id, direction="asc", interval=interval, count=count,
        ))
        groups = _get(data, "candles", default=[]) if isinstance(data, dict) else data
        raw: list[Any] = []
        for g in groups or []:
            inner = _get(g, "candles", default=None) if isinstance(g, dict) else None
            raw.extend(inner if isinstance(inner, list) else [g])
        candles: list[Candle] = []
        for c in raw:
            try:
                candles.append(Candle(
                    instrument_id=int(_get(c, "instrumentID", "instrumentId", default=instrument_id)),
                    from_date=_parse_dt(_get(c, "fromDate", "date", "timestamp")),
                    open=float(_get(c, "open")), high=float(_get(c, "high")),
                    low=float(_get(c, "low")), close=float(_get(c, "close")),
                    volume=float(_get(c, "volume", default=0.0) or 0.0),
                ))
            except (TypeError, ValueError) as exc:
                log.warning("eToro candles : bougie ignorée (%s)", exc)
        candles.sort(key=lambda x: x.from_date)
        return candles

    # --------------------------------------------------------------- portfolio
    async def _fetch_portfolio(self) -> dict[str, Any]:
        data = await self._request("GET", self._path("portfolio"))
        if not isinstance(data, dict):
            raise EtoroApiError("portfolio : réponse inattendue", payload=data)
        return _get(data, "clientPortfolio", default=data)

    def _in_scope(self, raw: dict[str, Any]) -> bool:
        """Filtre optionnel sur l'Agent Portfolio (À VÉRIFIER : positions.mirrorID == portfolio id)."""
        pid = self._settings.etoro_portfolio_id
        if not pid or not pid.isdigit():
            return True
        mirror = _get(raw, "mirrorID", "mirrorId")
        return mirror is None or str(mirror) == pid

    def _map_position(self, raw: dict[str, Any]) -> Position:
        return Position(
            position_id=str(_get(raw, "positionID", "positionId")),
            instrument_id=int(_get(raw, "instrumentID", "instrumentId")),
            side=Side.BUY if bool(_get(raw, "isBuy", default=True)) else Side.SELL,
            amount=float(_get(raw, "amount", default=0.0)),
            open_rate=float(_get(raw, "openRate", default=0.0)),
            stop_loss_rate=_positive(_get(raw, "stopLossRate")),
            take_profit_rate=_positive(_get(raw, "takeProfitRate")),
            opened_at=_parse_dt(_get(raw, "openDateTime", "openDate")),
            unrealized_pnl=float(_get(raw, "unrealizedPnL", "profit", "netProfit", default=0.0) or 0.0),
        )

    async def get_open_positions(self) -> list[Position]:
        portfolio = await self._fetch_portfolio()
        positions: list[Position] = []
        for raw in _get(portfolio, "positions", default=[]) or []:
            if not self._in_scope(raw):
                continue
            try:
                positions.append(self._map_position(raw))
            except (TypeError, ValueError) as exc:
                log.warning("eToro portfolio : position ignorée (%s)", exc)
        return positions

    async def get_account_balance(self) -> float:
        """Equity = credit (cash) + montant investi + PnL latent. À VÉRIFIER : clé `equity` directe."""
        portfolio = await self._fetch_portfolio()
        for key in ("equity", "totalEquity", "realizedEquity"):
            direct = _get(portfolio, key)
            if direct is not None:
                return float(direct)
        credit = float(_get(portfolio, "credit", default=0.0) or 0.0)
        positions = _get(portfolio, "positions", default=[]) or []
        invested = sum(float(_get(p, "amount", default=0.0) or 0.0) for p in positions)
        unrealized = _get(portfolio, "unrealizedPnL")
        if unrealized is None:
            unrealized = sum(float(_get(p, "unrealizedPnL", "profit", default=0.0) or 0.0) for p in positions)
        return credit + invested + float(unrealized)

    # ------------------------------------------------------------------ orders
    async def open_position(self, order: OrderRequest) -> Position:
        """Ordre marché par montant avec SL/TP natifs (taux absolus). Refuse sans SL/TP."""
        if order.stop_loss_rate is None or order.take_profit_rate is None:
            raise ValueError("stop_loss_rate et take_profit_rate sont obligatoires")
        if order.stop_loss_rate <= 0 or order.take_profit_rate <= 0:
            raise ValueError("stop_loss_rate et take_profit_rate doivent être > 0")
        if self._settings.real_mode_locked:
            raise RuntimeError("mode real verrouillé : ETORO_CREDENTIALS_ROTATED=true requis")

        known_ids: set[str] = set()
        try:
            known_ids = {p.position_id for p in await self.get_open_positions()
                         if p.instrument_id == order.instrument_id}
        except EtoroApiError as exc:
            log.warning("eToro open_position : snapshot portfolio impossible (%s)", exc)

        body = {
            "InstrumentID": order.instrument_id,
            "IsBuy": order.side == Side.BUY,
            "Leverage": order.leverage,
            "Amount": order.amount,
            "StopLossRate": order.stop_loss_rate,
            "TakeProfitRate": order.take_profit_rate,
            "IsNoStopLoss": False,
            "IsNoTakeProfit": False,
            "IsTslEnabled": False,
        }
        data = await self._request("POST", self._path("open_by_amount"), json=body)
        order_raw = _get(data, "orderForOpen", "order", default=data) if isinstance(data, dict) else None
        if not isinstance(order_raw, dict):
            raise EtoroApiError("open_position : réponse inattendue", payload=data)

        echoed_sl = _positive(_get(order_raw, "stopLossRate"))
        echoed_tp = _positive(_get(order_raw, "takeProfitRate"))
        if echoed_sl is None or echoed_tp is None:
            raise EtoroApiError("open_position : l'API n'a pas confirmé le stop-loss / take-profit",
                                payload=data)
        for label, want, got in (("SL", order.stop_loss_rate, echoed_sl), ("TP", order.take_profit_rate, echoed_tp)):
            if abs(got - want) / want > 0.005:
                log.warning("eToro open_position : %s ajusté par l'API %.6f -> %.6f", label, want, got)

        order_id = _get(order_raw, "orderID", "orderId")
        log.info("eToro ordre accepté : order_id=%s instrument=%s side=%s amount=%.2f",
                 order_id, order.instrument_id, order.side.value, order.amount)

        # Exécution asynchrone : le positionID n'arrive que via le portfolio.
        position = await self._wait_for_position(order.instrument_id, known_ids)
        if position is not None:
            return position
        log.warning("eToro ordre %s : position pas encore visible, retour d'une position provisoire", order_id)
        return Position(
            position_id=f"order:{order_id}",
            instrument_id=order.instrument_id,
            side=order.side,
            amount=order.amount,
            open_rate=order.entry_rate,
            stop_loss_rate=echoed_sl,
            take_profit_rate=echoed_tp,
            opened_at=_parse_dt(_get(order_raw, "openDateTime")),
        )

    async def _wait_for_position(self, instrument_id: int, known_ids: set[str]) -> Position | None:
        for attempt in range(self._position_poll_attempts):
            if attempt and self._position_poll_delay_s:
                await asyncio.sleep(self._position_poll_delay_s)
            try:
                positions = await self.get_open_positions()
            except EtoroApiError as exc:
                log.warning("eToro poll position : %s", exc)
                continue
            fresh = [p for p in positions if p.instrument_id == instrument_id and p.position_id not in known_ids]
            if fresh:
                return max(fresh, key=lambda p: p.opened_at)
        return None

    async def close_position(self, position_id: str, instrument_id: int | None = None) -> ClosedPosition:
        """Fermeture totale par positionId (UnitsToDeduct=null). PnL = latent au moment de la demande."""
        if position_id.startswith("order:"):
            raise EtoroApiError(f"position provisoire {position_id} : resynchroniser le portfolio d'abord")
        if self._settings.real_mode_locked:
            raise RuntimeError("mode real verrouillé : ETORO_CREDENTIALS_ROTATED=true requis")
        current: Position | None = None
        for p in await self.get_open_positions():
            if p.position_id == position_id:
                current = p
                break
        if current is None and instrument_id is None:
            raise EtoroApiError(f"position {position_id} introuvable dans le portfolio")
        inst = instrument_id if instrument_id is not None else current.instrument_id  # type: ignore[union-attr]

        data = await self._request(
            "POST", self._path("close_position", position_id=position_id),
            json={"InstrumentID": inst, "UnitsToDeduct": None},
        )
        close_raw = _get(data, "orderForClose", default=data) if isinstance(data, dict) else {}
        log.info("eToro fermeture demandée : position=%s order_id=%s", position_id, _get(close_raw, "orderID"))
        return ClosedPosition(
            position_id=position_id,
            instrument_id=inst,
            realized_pnl=current.unrealized_pnl if current else 0.0,
            closed_at=_parse_dt(_get(close_raw, "lastUpdate", "openDateTime")),
        )

    async def get_closed_position(self, position_id: str, lookback_days: int = 7) -> ClosedPosition:
        """PnL réalisé d'une position fermée via l'historique des trades (À VÉRIFIER : schéma de réponse).

        Utilisé par `agent.sync_positions` (optionnel). Lève EtoroApiError si introuvable.
        """
        min_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
        data = await self._request("GET", self._path("trade_history"), params={"minDate": min_date})
        rows: Any = data
        if isinstance(data, dict):
            rows = _get(data, "trades", "items", "history", "closedPositions", default=[])
        for row in rows or []:
            if str(_get(row, "positionID", "positionId", default="")) != str(position_id):
                continue
            pnl = _get(row, "netProfit", "realizedPnL", "profit", "pnl", default=0.0)
            return ClosedPosition(
                position_id=str(position_id),
                instrument_id=int(_get(row, "instrumentID", "instrumentId", default=0)),
                realized_pnl=float(pnl or 0.0),
                closed_at=_parse_dt(_get(row, "closeDateTime", "closeDate", "closedAt", "lastUpdate")),
            )
        raise EtoroApiError(f"position {position_id} absente de l'historique depuis {min_date}", payload=data)

    # ------------------------------------------------------------------ health
    async def health(self) -> bool:
        try:
            await self._fetch_portfolio()
            return True
        except Exception as exc:  # noqa: BLE001 - health ne doit jamais lever
            log.warning("eToro health KO : %s", exc)
            return False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
