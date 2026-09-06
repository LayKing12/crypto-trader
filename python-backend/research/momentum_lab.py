"""
Momentum Lab — indicateurs de momentum purs et score momentum 0..100.

Toutes les fonctions d'indicateurs travaillent sur des listes de floats et renvoient
une liste de même longueur, avec `None` pendant la période de chauffe (aucun
look-ahead : la valeur à l'indice i n'utilise que les indices <= i).

Le score « baseline » (RSI/EMA/MACD du moteur live) est reproduit en important
`app.services.indicator_engine.compute_indicators` et
`app.services.scoring_engine.compute_scores` en lecture seule.

CLI :
    python -m research.momentum_lab --pair XBTUSD --source kraken
    python -m research.momentum_lab --csv fichier.csv --asset MONACTIF

Ce module ne lit JAMAIS de clé API et n'envoie jamais d'ordre.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

Series = list  # list[float | None]


# ── Utilitaires ──────────────────────────────────────────────────────────────


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _check_period(n: int, name: str = "n") -> None:
    if not isinstance(n, int) or n <= 0:
        raise ValueError(f"{name} doit être un entier > 0 (reçu {n!r})")


# ── Indicateurs purs ─────────────────────────────────────────────────────────


def roc(closes: Sequence[float], n: int) -> Series:
    """Rate of Change sur n barres, en % : (close[i] - close[i-n]) / close[i-n] * 100."""
    _check_period(n)
    out: Series = [None] * len(closes)
    for i in range(n, len(closes)):
        prev = closes[i - n]
        if prev:
            out[i] = (closes[i] - prev) / prev * 100.0
    return out


def relative_strength(closes: Sequence[float], benchmark_closes: Sequence[float], n: int) -> Series:
    """
    Force relative sur n barres = ratio de performance actif / benchmark :
        (close[i] / close[i-n]) / (bench[i] / bench[i-n])
    1.0 = performance identique, > 1.0 = surperformance, < 1.0 = sous-performance.
    Crypto : benchmark = BTC ; eToro : benchmark = un indice de référence.
    """
    _check_period(n)
    if len(closes) != len(benchmark_closes):
        raise ValueError("relative_strength : les deux séries doivent avoir la même longueur (aligner les dates)")
    out: Series = [None] * len(closes)
    for i in range(n, len(closes)):
        c0, b0, b1 = closes[i - n], benchmark_closes[i - n], benchmark_closes[i]
        if c0 and b0 and b1:
            out[i] = (closes[i] / c0) / (b1 / b0)
    return out


def sma(closes: Sequence[float], n: int) -> Series:
    """Moyenne mobile simple sur n barres (None tant que n barres ne sont pas disponibles)."""
    _check_period(n)
    out: Series = [None] * len(closes)
    running = 0.0
    for i, c in enumerate(closes):
        running += c
        if i >= n:
            running -= closes[i - n]
        if i >= n - 1:
            out[i] = running / n
    return out


def sma_alignment(closes: Sequence[float], periods: Sequence[int] = (20, 50, 100, 200)) -> Series:
    """
    Alignement multi-horizons des moyennes mobiles, dans [0, 1] :
    fraction des relations `prix > SMA(p1) > SMA(p2) > ... > SMA(pk)` vérifiées
    (périodes triées croissantes). 1.0 = alignement haussier parfait, 0.0 = baissier parfait.
    None tant que la plus longue SMA n'est pas disponible.
    """
    ps = sorted(set(periods))
    if not ps:
        raise ValueError("sma_alignment : au moins une période requise")
    smas = [sma(closes, p) for p in ps]
    out: Series = [None] * len(closes)
    for i in range(len(closes)):
        if any(s[i] is None for s in smas):
            continue
        chain = [closes[i]] + [s[i] for s in smas]
        checks = sum(1 for a, b in zip(chain, chain[1:]) if a > b)
        out[i] = checks / (len(chain) - 1)
    return out


def sma_alignment_signal(value: float | None) -> str:
    """Traduction lisible d'une valeur d'alignement : bullish / bearish / mixed / unknown."""
    if value is None:
        return "unknown"
    if value >= 1.0:
        return "bullish"
    if value <= 0.0:
        return "bearish"
    return "mixed"


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    """True range de chaque barre à partir de la 2e (len - 1 valeurs), formule Wilder."""
    trs: list[float] = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_prev_close = abs(highs[i] - closes[i - 1])
        low_prev_close = abs(lows[i] - closes[i - 1])
        trs.append(max(high_low, high_prev_close, low_prev_close))
    return trs


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> Series:
    """
    ATR Wilder par barre. Formule identique à `app.utils.math_utils.calc_atr`
    (moyenne simple des n premiers TR, puis lissage (atr*(n-1) + tr) / n),
    recopiée volontairement pour ne pas coupler la recherche au moteur live.
    La dernière valeur (non arrondie) est égale à calc_atr(highs, lows, closes, n).
    """
    _check_period(n)
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("atr : highs, lows et closes doivent avoir la même longueur")
    out: Series = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    trs = true_range(highs, lows, closes)  # trs[j] = TR de la barre j+1
    value = sum(trs[:n]) / n
    out[n] = value
    for i in range(n + 1, len(closes)):
        value = (value * (n - 1) + trs[i - 1]) / n
        out[i] = value
    return out


def atr_last(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> float | None:
    """Équivalent exact (arrondi 4 dp) de `calc_atr` du moteur live, pour vérification croisée."""
    series = atr(highs, lows, closes, n)
    return None if series[-1] is None else round(series[-1], 4)


def atr_pct(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> Series:
    """ATR exprimé en % du prix de clôture."""
    return [None if a is None or not c else a / c * 100.0 for a, c in zip(atr(highs, lows, closes, n), closes)]


# ── Score momentum ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MomentumConfig:
    """Paramètres du score momentum (tous purs, aucune dépendance externe)."""
    roc_periods: tuple[int, ...] = (7, 14, 30)
    # Variation (%) qui sature la composante ROC à 0 ou 100 pour chaque horizon
    roc_scales: tuple[float, ...] = (10.0, 15.0, 25.0)
    rs_period: int = 30
    rs_scale: float = 20.0          # ±20 % de sur/sous-performance sature la force relative
    sma_periods: tuple[int, ...] = (20, 50, 100, 200)
    atr_period: int = 14
    atr_pct_ok: float = 4.0         # ATR% <= 4 : volatilité confortable (composante = 100)
    atr_pct_max: float = 10.0       # ATR% >= 10 : volatilité excessive (composante = 0)
    w_roc: float = 0.40
    w_rs: float = 0.20
    w_sma: float = 0.25
    w_atr: float = 0.15

    def __post_init__(self) -> None:
        if len(self.roc_periods) != len(self.roc_scales):
            raise ValueError("roc_periods et roc_scales doivent avoir la même longueur")
        if self.atr_pct_max <= self.atr_pct_ok:
            raise ValueError("atr_pct_max doit être > atr_pct_ok")
        if min(self.w_roc, self.w_rs, self.w_sma, self.w_atr) < 0:
            raise ValueError("les poids doivent être >= 0")

    @property
    def warmup(self) -> int:
        """Nombre de barres nécessaires avant la première valeur non nulle."""
        return max(max(self.sma_periods), max(self.roc_periods) + 1, self.rs_period + 1, self.atr_period + 1)


def _linear_score(value: float, scale: float) -> float:
    """Mappe une variation (en %) sur 0..100, 50 = neutre, ±scale = saturation."""
    return clamp(50.0 + 50.0 * value / scale)


def _atr_component(pct: float, ok: float, worst: float) -> float:
    if pct <= ok:
        return 100.0
    if pct >= worst:
        return 0.0
    return 100.0 * (1.0 - (pct - ok) / (worst - ok))


def momentum_components(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    benchmark_closes: Sequence[float] | None = None,
    config: MomentumConfig | None = None,
) -> dict[str, Series]:
    """
    Détail par barre des composantes du score momentum (chacune 0..100) :
    roc, rs, sma, atr et total. Sans benchmark, la composante rs vaut None et son
    poids est redistribué sur les autres composantes.
    """
    cfg = config or MomentumConfig()
    n = len(closes)
    rocs = [roc(closes, p) for p in cfg.roc_periods]
    rs = relative_strength(closes, benchmark_closes, cfg.rs_period) if benchmark_closes is not None else None
    align = sma_alignment(closes, cfg.sma_periods)
    vol = atr_pct(highs, lows, closes, cfg.atr_period)

    out = {k: [None] * n for k in ("roc", "rs", "sma", "atr", "total")}
    for i in range(n):
        if any(r[i] is None for r in rocs) or align[i] is None or vol[i] is None:
            continue
        if rs is not None and rs[i] is None:
            continue
        roc_score = sum(_linear_score(r[i], s) for r, s in zip(rocs, cfg.roc_scales)) / len(rocs)
        sma_score = align[i] * 100.0
        atr_score = _atr_component(vol[i], cfg.atr_pct_ok, cfg.atr_pct_max)
        parts = [(roc_score, cfg.w_roc), (sma_score, cfg.w_sma), (atr_score, cfg.w_atr)]
        if rs is not None:
            rs_score = _linear_score((rs[i] - 1.0) * 100.0, cfg.rs_scale)
            parts.append((rs_score, cfg.w_rs))
            out["rs"][i] = rs_score
        total_w = sum(w for _, w in parts)
        total = sum(v * w for v, w in parts) / total_w if total_w else 50.0
        out["roc"][i] = roc_score
        out["sma"][i] = sma_score
        out["atr"][i] = atr_score
        out["total"][i] = clamp(total)
    return out


def momentum_score(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    benchmark_closes: Sequence[float] | None = None,
    config: MomentumConfig | None = None,
) -> Series:
    """
    Score momentum 0..100 par barre :
    ROC multi-horizons (7/14/30 j) + force relative vs benchmark + alignement des
    SMA 20/50/100/200 + confirmation ATR (volatilité pas trop élevée).
    """
    return momentum_components(closes, highs, lows, benchmark_closes, config)["total"]


# ── Score baseline (moteur live, lecture seule) ──────────────────────────────


def _import_live_engines():
    try:
        from app.services.indicator_engine import compute_indicators
        from app.services.scoring_engine import compute_scores
    except Exception as exc:  # ImportError, ou dépendance de config absente
        raise RuntimeError(
            "baseline_score : impossible d'importer app.services.indicator_engine / scoring_engine "
            "(lancer depuis python-backend/ avec les dépendances du backend installées : "
            "numpy, structlog, pydantic-settings...). Erreur d'origine : "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    _quiet_live_logging()
    return compute_indicators, compute_scores


def _quiet_live_logging() -> None:
    """
    Hors de l'application, structlog n'est pas configuré et imprime chaque log debug du
    moteur live (une ligne par bougie). On filtre au niveau WARNING, uniquement si rien
    n'a déjà configuré structlog (l'app live garde sa propre configuration).
    """
    try:
        import logging
        import structlog
    except Exception:
        return
    if not structlog.is_configured():
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))


def baseline_score(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float] | None = None,
    symbol: str = "RESEARCH",
    window: int = 300,
    min_bars: int = 200,
) -> Series:
    """
    Reproduit le score live actuel (`market_score` : tendance EMA 20/50/200, RSI, volume,
    autres composantes neutres à 50) barre par barre, en ne donnant au moteur que
    les `window` dernières bougies jusqu'à l'indice courant (aucun look-ahead).
    None tant que `min_bars` bougies ne sont pas disponibles (défaut 200 = EMA200,
    même horizon de chauffe que le score momentum).
    """
    compute_indicators, compute_scores = _import_live_engines()
    n = len(closes)
    vols = list(volumes) if volumes is not None else [0.0] * n
    if not (len(highs) == len(lows) == len(vols) == n):
        raise ValueError("baseline_score : séries de longueurs différentes")
    out: Series = [None] * n
    for i in range(n):
        if i + 1 < min_bars:
            continue
        lo = max(0, i + 1 - window)
        ind = compute_indicators(
            symbol,
            list(closes[lo:i + 1]),
            list(highs[lo:i + 1]),
            list(lows[lo:i + 1]),
            vols[lo:i + 1],
        )
        out[i] = float(compute_scores(ind).market_score)
    return out


# ── Orchestration du laboratoire ─────────────────────────────────────────────


@dataclass
class LabResult:
    asset: str
    source: str
    n_bars: int
    first_date: str
    last_date: str
    grid: Any                      # backtest.GridSearchResult
    comparison: dict               # {fenêtre: {stratégie: BacktestResult}}
    windows: list                  # list[backtest.Window]
    verdict: Any                   # backtest.RobustnessVerdict
    report_path: Path | None
    momentum_scores: Series = field(default_factory=list, repr=False)
    baseline_scores: Series = field(default_factory=list, repr=False)


def run_lab(
    rows: list[dict],
    asset: str,
    benchmark_rows: list[dict] | None = None,
    source: str = "csv",
    fee_pct: float = 0.26,
    momentum_config: MomentumConfig | None = None,
    baseline_config: Any = None,
    grid: list | None = None,
    in_sample_ratio: float = 0.6,
    metric: str = "sharpe",
    out_dir: str | Path | None = None,
    write_report: bool = True,
    include_baseline: bool = True,
) -> LabResult:
    """
    Pipeline complet : scores -> grid search anti-overfitting -> comparaison momentum /
    baseline / buy & hold sur in-sample, out-of-sample et chaque régime -> rapport.
    """
    # Imports paresseux : backtest importe `sma` d'ici, on évite l'import circulaire.
    from research import backtest as bt
    from research import report as rp
    from research.data_sources import align_on_dates, to_columns, validate_rows

    rows = validate_rows(rows)
    bench_closes = None
    if benchmark_rows is not None:
        rows, benchmark_rows = align_on_dates(rows, validate_rows(benchmark_rows))
        bench_closes = to_columns(benchmark_rows)["closes"]
    cols = to_columns(rows)
    m_cfg = momentum_config or MomentumConfig()
    if len(rows) <= m_cfg.warmup + 10:
        raise ValueError(
            f"Série trop courte ({len(rows)} barres) : il faut plus de {m_cfg.warmup} barres "
            "pour la chauffe des indicateurs (SMA200)."
        )

    m_scores = momentum_score(cols["closes"], cols["highs"], cols["lows"], bench_closes, m_cfg)
    if include_baseline:
        b_scores = baseline_score(cols["closes"], cols["highs"], cols["lows"], cols["volumes"],
                                  symbol=asset, min_bars=m_cfg.warmup)
    else:
        b_scores = [None] * len(rows)
    atr_series = atr(cols["highs"], cols["lows"], cols["closes"], m_cfg.atr_period)

    base_cfg = baseline_config or bt.BacktestConfig.baseline(fee_pct=fee_pct)
    grid_result = bt.grid_search(
        rows, m_scores, b_scores, atr_series,
        grid=grid or bt.default_grid(fee_pct=fee_pct),
        baseline_config=base_cfg,
        in_sample_ratio=in_sample_ratio,
        metric=metric,
    )
    windows = bt.build_windows(rows, in_sample_ratio=in_sample_ratio)
    comparison = bt.compare_strategies(
        rows, m_scores, b_scores, atr_series,
        momentum_config=grid_result.momentum_config,
        baseline_config=base_cfg,
        windows=windows,
        fee_pct=fee_pct,
    )
    verdict = bt.robustness_verdict(comparison, windows, metric=metric)

    report_path = None
    if write_report:
        report_path = rp.write_report(
            asset=asset, source=source, rows=rows, windows=windows, comparison=comparison,
            grid_result=grid_result, verdict=verdict, momentum_config=m_cfg, out_dir=out_dir,
        )
    return LabResult(
        asset=asset, source=source, n_bars=len(rows), first_date=rows[0]["date"], last_date=rows[-1]["date"],
        grid=grid_result, comparison=comparison, windows=windows, verdict=verdict, report_path=report_path,
        momentum_scores=m_scores, baseline_scores=b_scores,
    )


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m research.momentum_lab",
        description="Laboratoire momentum : backtest pur, aucune clé API, aucun ordre.",
    )
    p.add_argument("--pair", default="XBTUSD", help="Paire Kraken (ex. XBTUSD, ETHUSD) ou id CoinGecko (ex. bitcoin)")
    p.add_argument("--source", choices=("kraken", "coingecko", "csv"), default="kraken")
    p.add_argument("--csv", help="Fichier CSV OHLC (date,open,high,low,close[,volume]) — mode hors ligne")
    p.add_argument("--asset", help="Nom de l'actif pour le rapport (défaut : --pair ou nom du CSV)")
    p.add_argument("--benchmark", help="Paire/id de référence pour la force relative (ex. XBTUSD, bitcoin)")
    p.add_argument("--benchmark-csv", help="CSV de la série de référence (mode hors ligne)")
    p.add_argument("--days", type=int, default=720, help="Profondeur en jours (Kraken max ~720 ; CoinGecko : 'max' si 0)")
    p.add_argument("--fee", type=float, default=0.26, help="Frais par ordre en %% (défaut 0.26 = Kraken taker)")
    p.add_argument("--in-sample-ratio", type=float, default=0.6)
    p.add_argument("--metric", default="sharpe", choices=("sharpe", "total_return_pct", "cagr_pct"))
    p.add_argument("--out-dir", help="Dossier des rapports (défaut research/results/)")
    p.add_argument("--no-cache", action="store_true", help="Ignore le cache disque data_cache/")
    p.add_argument("--no-baseline", action="store_true", help="N'importe pas le moteur live (baseline vide)")
    return p


def _load_series(source: str, pair: str, csv_path: str | None, days: int, use_cache: bool) -> list[dict]:
    from research import data_sources as ds

    if csv_path:
        return ds.load_csv(csv_path)
    if source == "csv":
        raise SystemExit("--source csv exige --csv <fichier>")
    if source == "kraken":
        return ds.load_kraken_ohlc(pair, since_days=days, use_cache=use_cache)
    return ds.load_coingecko_ohlc(pair, days="max" if not days else days, use_cache=use_cache)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # Console Windows en cp1252 : on évite les UnicodeEncodeError sur les accents du résumé.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    asset = args.asset or (Path(args.csv).stem if args.csv else args.pair)
    source = "csv" if args.csv else args.source
    rows = _load_series(source, args.pair, args.csv, args.days, not args.no_cache)
    bench_rows = None
    if args.benchmark_csv:
        bench_rows = _load_series("csv", "", args.benchmark_csv, args.days, not args.no_cache)
    elif args.benchmark:
        bench_rows = _load_series(args.source, args.benchmark, None, args.days, not args.no_cache)

    result = run_lab(
        rows, asset=asset, benchmark_rows=bench_rows, source=source, fee_pct=args.fee,
        in_sample_ratio=args.in_sample_ratio, metric=args.metric, out_dir=args.out_dir,
        include_baseline=not args.no_baseline,
    )
    from research.report import render_summary
    print(render_summary(result))
    if result.report_path:
        print(f"\nRapport : {result.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
