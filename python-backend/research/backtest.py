"""
Moteur de backtest long-only journalier + garde-fous anti-overfitting.

Règles du moteur (`run_backtest`) :
- signal lu à la clôture du jour i, exécuté à l'ouverture du jour i+1 (pas de look-ahead) ;
- entrée quand score >= entry_threshold, sortie quand score < exit_threshold ;
- stop de protection fixe à k x ATR sous le prix d'entrée, vérifié sur le plus bas du jour ;
- frais appliqués à chaque ordre (0,26 % Kraken taker par défaut) ;
- une seule position par actif, pas de levier, tout le capital engagé.

Anti-overfitting (non négociable) :
- `split_chronological` : découpe chronologique, jamais aléatoire ;
- `grid_search` : la config candidate (meilleure in-sample) n'est retenue QUE si elle
  bat aussi le baseline out-of-sample ; sinon on retourne la config baseline ;
- `regime_slices` : découpe en régimes haussier/baissier (pente de la SMA200) pour
  vérifier la robustesse sur chaque régime.

Les scores sont calculés une seule fois sur la série complète (chaque valeur ne
dépend que du passé) puis le moteur est rejoué sur des fenêtres d'indices : la
fenêtre out-of-sample ne perd donc pas ses 200 jours de chauffe.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from research.momentum_lab import sma

Row = dict[str, Any]
Series = list


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BacktestConfig:
    name: str = "momentum"
    entry_threshold: float = 60.0
    exit_threshold: float = 45.0
    atr_k: float | None = 3.0          # None = pas de stop ATR
    fee_pct: float = 0.26              # frais par ordre, en %
    periods_per_year: int = 365        # 365 crypto ; 252 pour des actions eToro

    def __post_init__(self) -> None:
        if self.exit_threshold > self.entry_threshold:
            raise ValueError("exit_threshold doit être <= entry_threshold")
        if self.fee_pct < 0:
            raise ValueError("fee_pct doit être >= 0")

    @classmethod
    def baseline(cls, fee_pct: float = 0.26) -> "BacktestConfig":
        """
        Config figée du score live (market_score avec composantes externes neutres à 50,
        donc borné ~40..72) : long quand le score >= 60, sortie sous 50.
        """
        return cls(name="baseline", entry_threshold=60.0, exit_threshold=50.0, atr_k=3.0, fee_pct=fee_pct)

    @classmethod
    def buy_and_hold(cls, fee_pct: float = 0.26) -> "BacktestConfig":
        return cls(name="buy_hold", entry_threshold=-math.inf, exit_threshold=-math.inf, atr_k=None, fee_pct=fee_pct)

    def label(self) -> str:
        stop = f"{self.atr_k:g}xATR" if self.atr_k is not None else "sans stop"
        return f"entrée>={self.entry_threshold:g} / sortie<{self.exit_threshold:g} / stop {stop}"


def default_grid(fee_pct: float = 0.26) -> list[BacktestConfig]:
    """Grille volontairement petite (36 configs) pour limiter la sur-optimisation."""
    grid = []
    for entry, exit_, k in itertools.product((55.0, 60.0, 65.0, 70.0), (40.0, 45.0, 50.0), (2.0, 3.0, 4.0)):
        grid.append(BacktestConfig(name="momentum", entry_threshold=entry, exit_threshold=exit_, atr_k=k, fee_pct=fee_pct))
    return grid


# ── Résultats ────────────────────────────────────────────────────────────────


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    pnl_pct: float          # net de frais, en %
    reason: str             # "signal" | "stop" | "end"


@dataclass
class BacktestResult:
    strategy: str
    start: int
    end: int
    n_days: int
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    n_trades: int
    win_rate_pct: float
    exposure_pct: float                   # % des jours en position
    equity_curve: list[float] = field(default_factory=list, repr=False)
    trades: list[Trade] = field(default_factory=list, repr=False)

    def metric(self, name: str) -> float:
        value = getattr(self, name)
        if not isinstance(value, (int, float)):
            raise ValueError(f"'{name}' n'est pas une métrique numérique")
        return float(value)


METRIC_NAMES = ("total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "n_trades", "win_rate_pct")


# ── Métriques ────────────────────────────────────────────────────────────────


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Drawdown maximal en % (valeur positive, 0 = aucun repli)."""
    peak = -math.inf
    worst = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
    return worst


def sharpe_ratio(equity_curve: Sequence[float], periods_per_year: int = 365) -> float:
    """Sharpe journalier annualisé, taux sans risque = 0 ; 0.0 si moins de 2 rendements ou écart-type nul."""
    rets = [(b / a - 1.0) for a, b in zip(equity_curve, equity_curve[1:]) if a > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return mean / std * math.sqrt(periods_per_year)


def cagr(equity_curve: Sequence[float], periods_per_year: int = 365) -> float:
    if len(equity_curve) < 2 or equity_curve[0] <= 0 or equity_curve[-1] <= 0:
        return 0.0
    n = len(equity_curve) - 1
    return ((equity_curve[-1] / equity_curve[0]) ** (periods_per_year / n) - 1.0) * 100.0


# ── Moteur ───────────────────────────────────────────────────────────────────


def run_backtest(
    rows: Sequence[Row],
    scores: Sequence[float | None],
    config: BacktestConfig,
    start: int = 0,
    end: int | None = None,
    atr_series: Sequence[float | None] | None = None,
    initial_capital: float = 1.0,
) -> BacktestResult:
    """Rejoue la stratégie sur rows[start:end] avec des scores précalculés (alignés sur rows)."""
    end = len(rows) if end is None else end
    if not (0 <= start < end <= len(rows)):
        raise ValueError(f"Fenêtre invalide [{start}, {end}) pour {len(rows)} barres")
    if len(scores) != len(rows):
        raise ValueError("scores doit être aligné sur rows (même longueur)")
    if atr_series is not None and len(atr_series) != len(rows):
        raise ValueError("atr_series doit être aligné sur rows")

    fee = config.fee_pct / 100.0
    cash = float(initial_capital)
    units = 0.0
    entry_price = 0.0
    entry_idx = -1
    stop_price: float | None = None
    pending: str | None = None
    equity: list[float] = []
    trades: list[Trade] = []
    days_in_position = 0

    def close_position(i: int, price: float, reason: str) -> None:
        nonlocal cash, units, stop_price
        cash = units * price * (1.0 - fee)
        pnl = (price * (1.0 - fee)) / (entry_price / (1.0 - fee)) - 1.0
        trades.append(Trade(entry_idx, i, entry_price, price, pnl * 100.0, reason))
        units = 0.0
        stop_price = None

    for i in range(start, end):
        bar = rows[i]
        # 1. ordre en attente exécuté à l'ouverture
        if pending == "buy" and units == 0.0 and bar["open"] > 0:
            entry_price = bar["open"]
            entry_idx = i
            units = cash * (1.0 - fee) / entry_price
            cash = 0.0
            prev_atr = atr_series[i - 1] if (atr_series is not None and i > 0) else None
            stop_price = entry_price - config.atr_k * prev_atr if (config.atr_k is not None and prev_atr) else None
        elif pending == "sell" and units > 0.0:
            close_position(i, bar["open"], "signal")
        pending = None

        # 2. stop ATR touché en séance (exécuté au stop, ou à l'ouverture si gap en dessous)
        if units > 0.0 and stop_price is not None and bar["low"] <= stop_price:
            close_position(i, min(bar["open"], stop_price), "stop")

        # 3. valorisation à la clôture
        if units > 0.0:
            days_in_position += 1
        equity.append(cash + units * bar["close"])

        # 4. signal lu à la clôture pour le lendemain
        s = scores[i]
        if s is None:
            continue
        if units == 0.0 and s >= config.entry_threshold:
            pending = "buy"
        elif units > 0.0 and s < config.exit_threshold:
            pending = "sell"

    # Clôture forcée de la position résiduelle à la dernière clôture (comptée comme trade)
    if units > 0.0:
        close_position(end - 1, rows[end - 1]["close"], "end")
        equity[-1] = cash

    n_days = end - start
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    return BacktestResult(
        strategy=config.name,
        start=start,
        end=end,
        n_days=n_days,
        total_return_pct=(equity[-1] / initial_capital - 1.0) * 100.0,
        cagr_pct=cagr([initial_capital] + equity, config.periods_per_year),
        max_drawdown_pct=max_drawdown([initial_capital] + equity),
        sharpe=sharpe_ratio([initial_capital] + equity, config.periods_per_year),
        n_trades=len(trades),
        win_rate_pct=(wins / len(trades) * 100.0) if trades else 0.0,
        exposure_pct=days_in_position / n_days * 100.0 if n_days else 0.0,
        equity_curve=equity,
        trades=trades,
    )


def buy_and_hold(rows: Sequence[Row], start: int = 0, end: int | None = None, fee_pct: float = 0.26) -> BacktestResult:
    """Achat à la première ouverture disponible, conservation jusqu'à la fin (mêmes frais)."""
    cfg = BacktestConfig.buy_and_hold(fee_pct)
    return run_backtest(rows, [100.0] * len(rows), cfg, start=start, end=end)


# ── Découpes ─────────────────────────────────────────────────────────────────


def split_index(n: int, in_sample_ratio: float = 0.6) -> int:
    if not 0.0 < in_sample_ratio < 1.0:
        raise ValueError("in_sample_ratio doit être dans ]0, 1[")
    return int(n * in_sample_ratio)


def split_chronological(data: Sequence, in_sample_ratio: float = 0.6) -> tuple[list, list]:
    """
    Découpe chronologique : les premiers `ratio` % forment l'in-sample, le reste l'out-of-sample.
    Jamais aléatoire, ordre strictement préservé. Fonctionne sur toute séquence (rows, scores...).
    """
    k = split_index(len(data), in_sample_ratio)
    return list(data[:k]), list(data[k:])


@dataclass(frozen=True)
class Window:
    name: str
    start: int
    end: int
    kind: str                 # "in_sample" | "out_of_sample" | "regime"
    regime: str = ""          # "bull" | "bear" pour kind == "regime"

    @property
    def n_days(self) -> int:
        return self.end - self.start


def regime_labels(rows: Sequence[Row], sma_period: int = 200, slope_lookback: int = 20) -> list[str | None]:
    """Régime par barre : 'bull' si SMA(sma_period) monte sur slope_lookback barres, sinon 'bear'."""
    closes = [r["close"] for r in rows]
    s = sma(closes, sma_period)
    labels: list[str | None] = [None] * len(rows)
    for i in range(len(rows)):
        if i - slope_lookback < 0 or s[i] is None or s[i - slope_lookback] is None:
            continue
        labels[i] = "bull" if s[i] > s[i - slope_lookback] else "bear"
    return labels


def regime_slices(
    rows: Sequence[Row],
    sma_period: int = 200,
    slope_lookback: int = 20,
    min_len: int = 30,
) -> list[Window]:
    """
    Découpe la série en tranches contiguës de régime haussier / baissier (pente SMA200).
    Les tranches plus courtes que `min_len` sont fusionnées avec la précédente.
    Garantit au moins 2 tranches : si un seul régime est détecté, la tranche est
    scindée en deux moitiés chronologiques (suffixes a/b) pour garder un test de stabilité.
    """
    labels = regime_labels(rows, sma_period, slope_lookback)
    runs: list[list] = []  # [regime, start, end]
    for i, lab in enumerate(labels):
        if lab is None:
            continue
        if runs and runs[-1][0] == lab and runs[-1][2] == i:
            runs[-1][2] = i + 1
        else:
            runs.append([lab, i, i + 1])
    if not runs:
        # Série trop courte pour la SMA demandée : deux moitiés chronologiques sans étiquette.
        half = len(rows) // 2
        if half == 0:
            raise ValueError("Série trop courte pour découper des régimes")
        return [Window("segment#1a", 0, half, "regime", "unknown"),
                Window("segment#1b", half, len(rows), "regime", "unknown")]

    merged: list[list] = []
    for run in runs:
        if merged and (run[2] - run[1]) < min_len:
            merged[-1][2] = run[2]
        elif merged and (merged[-1][2] - merged[-1][1]) < min_len:
            # la précédente était trop courte : on l'absorbe dans la nouvelle
            run[1] = merged[-1][1]
            merged[-1] = run
        else:
            merged.append(run)
    # Fusionne les voisins de même régime créés par les absorptions
    compact: list[list] = []
    for run in merged:
        if compact and compact[-1][0] == run[0]:
            compact[-1][2] = run[2]
        else:
            compact.append(run)

    if len(compact) == 1:
        regime, s, e = compact[0]
        mid = (s + e) // 2
        return [Window(f"{regime}#1a", s, mid, "regime", regime), Window(f"{regime}#1b", mid, e, "regime", regime)]

    counters = {"bull": 0, "bear": 0}
    windows = []
    for regime, s, e in compact:
        counters[regime] += 1
        windows.append(Window(f"{regime}#{counters[regime]}", s, e, "regime", regime))
    return windows


def build_windows(rows: Sequence[Row], in_sample_ratio: float = 0.6, **regime_kwargs) -> list[Window]:
    k = split_index(len(rows), in_sample_ratio)
    return [
        Window("in_sample", 0, k, "in_sample"),
        Window("out_of_sample", k, len(rows), "out_of_sample"),
        *regime_slices(rows, **regime_kwargs),
    ]


# ── Grid search anti-overfitting ─────────────────────────────────────────────


@dataclass
class GridEntry:
    config: BacktestConfig
    in_sample: float
    out_of_sample: float
    in_sample_result: BacktestResult = field(repr=False)
    out_of_sample_result: BacktestResult = field(repr=False)


@dataclass
class GridSearchResult:
    metric: str
    selected_config: BacktestConfig      # config retenue (momentum candidate OU baseline)
    selected_is_momentum: bool
    momentum_config: BacktestConfig      # meilleure candidate in-sample (utilisée pour le comparatif)
    baseline_config: BacktestConfig
    candidate_in_sample: float
    candidate_out_of_sample: float
    baseline_in_sample: float
    baseline_out_of_sample: float
    entries: list[GridEntry] = field(default_factory=list, repr=False)
    split_at: int = 0
    reason: str = ""


def grid_search(
    rows: Sequence[Row],
    momentum_scores: Sequence[float | None],
    baseline_scores: Sequence[float | None],
    atr_series: Sequence[float | None] | None,
    grid: Sequence[BacktestConfig],
    baseline_config: BacktestConfig | None = None,
    in_sample_ratio: float = 0.6,
    metric: str = "sharpe",
) -> GridSearchResult:
    """
    1. évalue chaque config de la grille sur l'in-sample ET l'out-of-sample ;
    2. candidate = meilleure config in-sample ;
    3. la candidate est retenue UNIQUEMENT si elle bat le baseline in-sample ET out-of-sample ;
       sinon la config retenue est le baseline (aucune sélection sur l'OOS seul, ce qui
       reviendrait à sur-optimiser sur la fenêtre de validation).
    """
    if not grid:
        raise ValueError("grid vide")
    if metric not in METRIC_NAMES:
        raise ValueError(f"metric inconnue '{metric}' (choix : {METRIC_NAMES})")
    base_cfg = baseline_config or BacktestConfig.baseline()
    k = split_index(len(rows), in_sample_ratio)
    n = len(rows)

    def evaluate(scores, cfg) -> tuple[BacktestResult, BacktestResult]:
        return (run_backtest(rows, scores, cfg, 0, k, atr_series), run_backtest(rows, scores, cfg, k, n, atr_series))

    base_is, base_oos = evaluate(baseline_scores, base_cfg)
    entries = []
    for cfg in grid:
        r_is, r_oos = evaluate(momentum_scores, cfg)
        entries.append(GridEntry(cfg, r_is.metric(metric), r_oos.metric(metric), r_is, r_oos))

    best = max(entries, key=lambda e: e.in_sample)  # premier en cas d'égalité (déterministe)
    b_is, b_oos = base_is.metric(metric), base_oos.metric(metric)
    beats_is = best.in_sample > b_is
    beats_oos = best.out_of_sample > b_oos
    if beats_is and beats_oos:
        selected, is_momentum = best.config, True
        reason = f"candidate meilleure que le baseline in-sample ({best.in_sample:.3f} > {b_is:.3f}) et out-of-sample ({best.out_of_sample:.3f} > {b_oos:.3f})"
    else:
        selected, is_momentum = base_cfg, False
        why = [] if beats_is else [f"in-sample ({best.in_sample:.3f} <= {b_is:.3f})"]
        if not beats_oos:
            why.append(f"out-of-sample ({best.out_of_sample:.3f} <= {b_oos:.3f})")
        reason = "baseline conservé : la candidate ne bat pas le baseline " + " ni ".join(why)
    return GridSearchResult(
        metric=metric, selected_config=selected, selected_is_momentum=is_momentum,
        momentum_config=best.config, baseline_config=base_cfg,
        candidate_in_sample=best.in_sample, candidate_out_of_sample=best.out_of_sample,
        baseline_in_sample=b_is, baseline_out_of_sample=b_oos,
        entries=entries, split_at=k, reason=reason,
    )


# ── Comparatif et verdict ────────────────────────────────────────────────────

STRATEGIES = ("momentum", "baseline", "buy_hold")


def compare_strategies(
    rows: Sequence[Row],
    momentum_scores: Sequence[float | None],
    baseline_scores: Sequence[float | None],
    atr_series: Sequence[float | None] | None,
    momentum_config: BacktestConfig,
    baseline_config: BacktestConfig,
    windows: Sequence[Window],
    fee_pct: float = 0.26,
) -> dict[str, dict[str, BacktestResult]]:
    """Momentum vs baseline vs buy & hold sur exactement les mêmes fenêtres."""
    m_cfg = replace(momentum_config, name="momentum")
    b_cfg = replace(baseline_config, name="baseline")
    out: dict[str, dict[str, BacktestResult]] = {}
    for w in windows:
        out[w.name] = {
            "momentum": run_backtest(rows, momentum_scores, m_cfg, w.start, w.end, atr_series),
            "baseline": run_backtest(rows, baseline_scores, b_cfg, w.start, w.end, atr_series),
            "buy_hold": buy_and_hold(rows, w.start, w.end, fee_pct),
        }
    return out


@dataclass
class RobustnessCheck:
    window: str
    kind: str
    momentum: float
    baseline: float
    passed: bool


@dataclass
class RobustnessVerdict:
    robust: bool
    metric: str
    checks: list[RobustnessCheck]

    @property
    def label(self) -> str:
        return "ROBUSTE" if self.robust else "NON ROBUSTE"


def robustness_verdict(
    comparison: dict[str, dict[str, BacktestResult]],
    windows: Sequence[Window],
    metric: str = "sharpe",
) -> RobustnessVerdict:
    """
    ROBUSTE uniquement si le momentum bat le baseline (métrique `metric`) sur
    l'out-of-sample ET sur CHAQUE régime. L'in-sample est rapporté à titre indicatif.
    """
    checks = []
    for w in windows:
        res = comparison[w.name]
        m, b = res["momentum"].metric(metric), res["baseline"].metric(metric)
        checks.append(RobustnessCheck(w.name, w.kind, m, b, m > b))
    required = [c for c in checks if c.kind in ("out_of_sample", "regime")]
    return RobustnessVerdict(robust=bool(required) and all(c.passed for c in required), metric=metric, checks=checks)
