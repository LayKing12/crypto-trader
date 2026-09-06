"""
Sources de données OHLC journalières pour le laboratoire momentum.

Trois sources :
- Kraken REST public  (`load_kraken_ohlc`)   — ~720 bougies journalières max, sans clé API ;
- CoinGecko public    (`load_coingecko_ohlc`) — prix journaliers sur toute la profondeur (days=max) ;
- CSV figé            (`load_csv`)            — rejeu hors ligne / tests.

Format de retour commun : liste de dicts triés par date croissante :
    {"date": "YYYY-MM-DD", "open": float, "high": float, "low": float, "close": float, "volume": float}

Un cache disque JSON (research/data_cache/, gitignoré) évite de re-télécharger.
Aucune clé API n'est jamais lue : seules les routes publiques sont utilisées.
Les tests n'appellent jamais ce module en réseau (séries synthétiques uniquement).
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"

CACHE_DIR = Path(__file__).resolve().parent / "data_cache"
DEFAULT_CACHE_MAX_AGE_HOURS = 24.0
HTTP_TIMEOUT_SECONDS = 30.0

Row = dict[str, Any]

# ── Helpers de structure ─────────────────────────────────────────────────────


def make_row(date: str, open_: float, high: float, low: float, close: float, volume: float = 0.0) -> Row:
    return {
        "date": date,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


def validate_rows(rows: list[Row]) -> list[Row]:
    """Vérifie les colonnes, supprime les doublons de date et trie par date croissante."""
    seen: dict[str, Row] = {}
    for r in rows:
        for key in ("date", "open", "high", "low", "close"):
            if key not in r:
                raise ValueError(f"Ligne OHLC invalide, colonne manquante '{key}': {r}")
        if r["low"] > r["high"]:
            raise ValueError(f"Ligne OHLC incohérente (low > high) à la date {r['date']}")
        seen[str(r["date"])] = make_row(
            str(r["date"]), r["open"], r["high"], r["low"], r["close"], r.get("volume", 0.0)
        )
    return [seen[d] for d in sorted(seen)]


def to_columns(rows: list[Row]) -> dict[str, list]:
    """Éclate les lignes en colonnes (listes de floats) prêtes pour les indicateurs."""
    return {
        "dates": [r["date"] for r in rows],
        "opens": [r["open"] for r in rows],
        "highs": [r["high"] for r in rows],
        "lows": [r["low"] for r in rows],
        "closes": [r["close"] for r in rows],
        "volumes": [r["volume"] for r in rows],
    }


def align_on_dates(rows: list[Row], benchmark_rows: list[Row]) -> tuple[list[Row], list[Row]]:
    """Garde uniquement les dates communes aux deux séries (ordre chronologique conservé)."""
    bench_by_date = {r["date"]: r for r in benchmark_rows}
    a: list[Row] = []
    b: list[Row] = []
    for r in rows:
        br = bench_by_date.get(r["date"])
        if br is not None:
            a.append(r)
            b.append(br)
    return a, b


# ── Cache disque JSON ────────────────────────────────────────────────────────


def _cache_path(key: str, cache_dir: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
    return (cache_dir or CACHE_DIR) / f"{safe}.json"


def cache_read(key: str, max_age_hours: float = DEFAULT_CACHE_MAX_AGE_HOURS,
               cache_dir: Path | None = None) -> list[Row] | None:
    path = _cache_path(key, cache_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fetched_at = float(payload.get("fetched_at", 0))
    if max_age_hours is not None and (time.time() - fetched_at) > max_age_hours * 3600:
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None
    return validate_rows(rows)


def cache_write(key: str, rows: list[Row], cache_dir: Path | None = None) -> Path:
    path = _cache_path(key, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"key": key, "fetched_at": time.time(), "rows": rows}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── CSV ──────────────────────────────────────────────────────────────────────


def load_csv(path: str | Path) -> list[Row]:
    """
    Charge un CSV figé. Colonnes attendues (insensibles à la casse) :
    date, open, high, low, close[, volume]. Les dates sont conservées telles quelles
    (ISO « YYYY-MM-DD » recommandé) ; un timestamp epoch (s ou ms) est converti.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV introuvable : {path}")
    rows: list[Row] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV vide : {path}")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        for needed in ("date", "open", "high", "low", "close"):
            if needed not in fields:
                raise ValueError(f"Colonne '{needed}' absente du CSV {path} (colonnes : {reader.fieldnames})")
        for raw in reader:
            date = _normalise_date(raw[fields["date"]])
            volume = raw[fields["volume"]] if "volume" in fields and raw[fields["volume"]] not in (None, "") else 0.0
            rows.append(make_row(date, raw[fields["open"]], raw[fields["high"]], raw[fields["low"]],
                                 raw[fields["close"]], volume))
    return validate_rows(rows)


def save_csv(rows: Iterable[Row], path: str | Path) -> Path:
    """Fige une série (utile pour rejouer un backtest à l'identique plus tard)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in writer.fieldnames})
    return path


def _normalise_date(value: str) -> str:
    value = str(value).strip()
    if value.isdigit():
        ts = int(value)
        if ts > 10_000_000_000:  # millisecondes
            ts //= 1000
        return _epoch_to_date(ts)
    return value[:10]


def _epoch_to_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# ── Kraken (public) ──────────────────────────────────────────────────────────


def parse_kraken_ohlc(payload: dict[str, Any]) -> list[Row]:
    """Convertit la réponse JSON de /0/public/OHLC (interval=1440) en lignes OHLC."""
    if payload.get("error"):
        raise RuntimeError(f"Erreur API Kraken : {payload['error']}")
    result = payload.get("result") or {}
    candles = None
    for key, value in result.items():
        if key != "last" and isinstance(value, list):
            candles = value
            break
    if candles is None:
        raise RuntimeError("Réponse Kraken sans bougies OHLC")
    rows = []
    for c in candles:
        # [time, open, high, low, close, vwap, volume, count]
        rows.append(make_row(_epoch_to_date(float(c[0])), c[1], c[2], c[3], c[4], c[6]))
    return validate_rows(rows)


def load_kraken_ohlc(pair: str = "XBTUSD", since_days: int = 720, use_cache: bool = True,
                     cache_max_age_hours: float = DEFAULT_CACHE_MAX_AGE_HOURS) -> list[Row]:
    """
    OHLC journalier via l'API publique Kraken (aucune clé). Kraken renvoie au plus
    ~720 bougies par appel : `since_days` > 720 est donc plafonné de fait.
    """
    key = f"kraken_{pair}_1440"
    if use_cache:
        cached = cache_read(key, cache_max_age_hours)
        if cached:
            return _last_n_days(cached, since_days)
    import httpx  # import paresseux : le chemin hors ligne n'a pas besoin de httpx

    since = int(time.time() - since_days * 86400)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = client.get(KRAKEN_OHLC_URL, params={"pair": pair, "interval": 1440, "since": since})
        resp.raise_for_status()
        rows = parse_kraken_ohlc(resp.json())
    # La dernière bougie Kraken est celle du jour en cours (incomplète) : on l'écarte.
    if rows and rows[-1]["date"] == _epoch_to_date(time.time()):
        rows = rows[:-1]
    if use_cache:
        cache_write(key, rows)
    return _last_n_days(rows, since_days)


# ── CoinGecko (public) ───────────────────────────────────────────────────────


def parse_coingecko_market_chart(payload: dict[str, Any]) -> list[Row]:
    """
    CoinGecko /market_chart ne donne qu'un prix par jour (pas de vrai OHLC).
    On reconstruit : open = clôture de la veille, close = prix du jour,
    high = max(open, close), low = min(open, close). L'ATR est donc une
    approximation prudente (amplitude intrajournalière ignorée).
    """
    prices = payload.get("prices") or []
    volumes = {int(v[0] // 1000 // 86400): float(v[1]) for v in payload.get("total_volumes") or []}
    daily: dict[str, tuple[int, float]] = {}
    for ts_ms, price in prices:
        day = int(ts_ms // 1000 // 86400)
        daily[_epoch_to_date(day * 86400)] = (day, float(price))
    rows: list[Row] = []
    prev_close: float | None = None
    for date in sorted(daily):
        day, close = daily[date]
        open_ = prev_close if prev_close is not None else close
        rows.append(make_row(date, open_, max(open_, close), min(open_, close), close, volumes.get(day, 0.0)))
        prev_close = close
    return validate_rows(rows)


def load_coingecko_ohlc(coin_id: str = "bitcoin", days: str | int = "max", use_cache: bool = True,
                        cache_max_age_hours: float = DEFAULT_CACHE_MAX_AGE_HOURS) -> list[Row]:
    """Prix journaliers via l'API publique CoinGecko (profondeur maximale avec days=max)."""
    key = f"coingecko_{coin_id}_{days}"
    if use_cache:
        cached = cache_read(key, cache_max_age_hours)
        if cached:
            return cached
    import httpx

    url = COINGECKO_MARKET_CHART_URL.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "days": str(days)}
    if str(days) != "max" and int(days) > 90:
        params["interval"] = "daily"
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, headers={"accept": "application/json"}) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        rows = parse_coingecko_market_chart(resp.json())
    # Dernier point = prix courant (journée incomplète) : écarté.
    if rows and rows[-1]["date"] == _epoch_to_date(time.time()):
        rows = rows[:-1]
    if use_cache:
        cache_write(key, rows)
    return rows


def _last_n_days(rows: list[Row], n: int) -> list[Row]:
    return rows[-n:] if n and n > 0 else rows


# ── Séries synthétiques (tests, démos hors ligne) ────────────────────────────


def synthetic_rows(n: int = 400, start_price: float = 100.0, drift: float = 0.001,
                   volatility: float = 0.02, seed: int = 42, start_date: str = "2020-01-01") -> list[Row]:
    """
    Marche aléatoire géométrique déterministe (générateur `random` seedé, pas de réseau).
    `drift` peut être une liste par jour pour composer plusieurs régimes.
    """
    import random
    from datetime import date as _date, timedelta

    rng = random.Random(seed)
    d0 = _date.fromisoformat(start_date)
    rows: list[Row] = []
    price = start_price
    for i in range(n):
        mu = drift[i] if isinstance(drift, (list, tuple)) else drift
        ret = mu + rng.gauss(0.0, volatility)
        open_ = price
        close = max(0.01, price * (1.0 + ret))
        wick = abs(rng.gauss(0.0, volatility / 2)) * price
        high = max(open_, close) + wick
        low = max(0.01, min(open_, close) - wick)
        rows.append(make_row((d0 + timedelta(days=i)).isoformat(), open_, high, low, close, 1000.0 + rng.random() * 500))
        price = close
    return rows
