"""
Rapport markdown du laboratoire momentum.

Fichier généré : research/results/<date>_<actif>.md
Contenu : tableau rendement / CAGR / drawdown max / Sharpe / trades / win rate pour
momentum, baseline et buy & hold sur in-sample, out-of-sample et chaque régime ;
config retenue par le grid search ; verdict explicite « ROBUSTE » / « NON ROBUSTE ».
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Any, Sequence

RESULTS_DIR = Path(__file__).resolve().parent / "results"

STRATEGY_LABELS = {"momentum": "Momentum", "baseline": "Baseline (RSI/EMA/MACD)", "buy_hold": "Buy & hold"}
WINDOW_KIND_LABELS = {"in_sample": "In-sample", "out_of_sample": "Out-of-sample", "regime": "Régime"}


def _fmt(value: float, suffix: str = "", digits: int = 2) -> str:
    return f"{value:+.{digits}f}{suffix}"


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "actif"


def window_label(w: Any, rows: Sequence[dict]) -> str:
    d0, d1 = rows[w.start]["date"], rows[w.end - 1]["date"]
    kind = WINDOW_KIND_LABELS.get(w.kind, w.kind)
    if w.kind == "regime":
        regime = {"bull": "haussier", "bear": "baissier"}.get(w.regime, w.regime)
        return f"{kind} {regime} `{w.name}` ({d0} → {d1}, {w.n_days} j)"
    return f"{kind} ({d0} → {d1}, {w.n_days} j)"


def results_table(windows: Sequence[Any], comparison: dict[str, dict[str, Any]], rows: Sequence[dict]) -> str:
    lines = [
        "| Fenêtre | Stratégie | Rendement | CAGR | Drawdown max | Sharpe | Trades | Win rate | Exposition |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for w in windows:
        for strat in ("momentum", "baseline", "buy_hold"):
            r = comparison[w.name][strat]
            lines.append(
                f"| {window_label(w, rows)} | {STRATEGY_LABELS[strat]} | {_fmt(r.total_return_pct, ' %')} | "
                f"{_fmt(r.cagr_pct, ' %')} | {r.max_drawdown_pct:.2f} % | {r.sharpe:.2f} | {r.n_trades} | "
                f"{r.win_rate_pct:.1f} % | {r.exposure_pct:.0f} % |"
            )
    return "\n".join(lines)


def grid_table(grid_result: Any, top: int = 10) -> str:
    entries = sorted(grid_result.entries, key=lambda e: e.in_sample, reverse=True)[:top]
    lines = [
        f"| Config momentum | {grid_result.metric} in-sample | {grid_result.metric} out-of-sample | Trades IS / OOS |",
        "|---|---:|---:|---:|",
    ]
    for e in entries:
        lines.append(
            f"| {e.config.label()} | {e.in_sample:.3f} | {e.out_of_sample:.3f} | "
            f"{e.in_sample_result.n_trades} / {e.out_of_sample_result.n_trades} |"
        )
    lines.append(
        f"| **Baseline** ({grid_result.baseline_config.label()}) | {grid_result.baseline_in_sample:.3f} | "
        f"{grid_result.baseline_out_of_sample:.3f} | — |"
    )
    return "\n".join(lines)


def robustness_table(verdict: Any) -> str:
    lines = [
        f"| Fenêtre | Type | Momentum ({verdict.metric}) | Baseline ({verdict.metric}) | Momentum bat le baseline ? |",
        "|---|---|---:|---:|:---:|",
    ]
    for c in verdict.checks:
        required = c.kind in ("out_of_sample", "regime")
        mark = ("OUI" if c.passed else "NON") if required else ("oui" if c.passed else "non") + " (indicatif)"
        lines.append(f"| `{c.window}` | {WINDOW_KIND_LABELS.get(c.kind, c.kind)} | {c.momentum:.3f} | {c.baseline:.3f} | {mark} |")
    return "\n".join(lines)


def render_report(
    asset: str,
    source: str,
    rows: Sequence[dict],
    windows: Sequence[Any],
    comparison: dict[str, dict[str, Any]],
    grid_result: Any,
    verdict: Any,
    momentum_config: Any = None,
    generated_on: str | None = None,
) -> str:
    today = generated_on or _date.today().isoformat()
    retained = grid_result.selected_config
    retained_kind = "**momentum**" if grid_result.selected_is_momentum else "**baseline** (aucune config momentum retenue)"
    lines = [
        f"# Momentum Lab — {asset}",
        "",
        f"- Généré le : {today}",
        f"- Source : {source}",
        f"- Période : {rows[0]['date']} → {rows[-1]['date']} ({len(rows)} bougies journalières)",
        f"- Split chronologique : {grid_result.split_at} barres in-sample / {len(rows) - grid_result.split_at} barres out-of-sample",
        f"- Frais par ordre : {retained.fee_pct:g} %",
        f"- Métrique de sélection / verdict : `{verdict.metric}`",
        "",
        f"## Verdict : {verdict.label}",
        "",
        (
            "Le momentum bat le baseline out-of-sample **et** sur chaque régime."
            if verdict.robust
            else "Le momentum ne bat PAS le baseline sur au moins une fenêtre requise (out-of-sample ou un régime)."
        ),
        "",
        robustness_table(verdict),
        "",
        "## Config retenue",
        "",
        f"- Config retenue : {retained_kind} — `{retained.label()}`",
        f"- Raison : {grid_result.reason}",
        f"- Meilleure candidate momentum in-sample : `{grid_result.momentum_config.label()}` "
        f"({grid_result.metric} IS {grid_result.candidate_in_sample:.3f} / OOS {grid_result.candidate_out_of_sample:.3f})",
        f"- Baseline : `{grid_result.baseline_config.label()}` "
        f"({grid_result.metric} IS {grid_result.baseline_in_sample:.3f} / OOS {grid_result.baseline_out_of_sample:.3f})",
    ]
    if momentum_config is not None:
        lines += [
            f"- Score momentum : ROC {list(momentum_config.roc_periods)} j, force relative {momentum_config.rs_period} j, "
            f"SMA {list(momentum_config.sma_periods)}, ATR {momentum_config.atr_period} j "
            f"(poids ROC {momentum_config.w_roc:g} / RS {momentum_config.w_rs:g} / SMA {momentum_config.w_sma:g} / ATR {momentum_config.w_atr:g})",
        ]
    lines += [
        "",
        "## Résultats par fenêtre (mêmes fenêtres pour les 3 stratégies)",
        "",
        results_table(windows, comparison, rows),
        "",
        "## Grid search (top 10 in-sample)",
        "",
        "La candidate est la meilleure config in-sample ; elle n'est retenue que si elle bat aussi le baseline out-of-sample.",
        "",
        grid_table(grid_result),
        "",
        "## Limites",
        "",
        "- Backtest journalier long-only, exécution à l'ouverture du lendemain, stop ATR fixe, capital 100 % engagé.",
        "- Frais fixes par ordre ; ni slippage, ni financement, ni spread variable.",
        "- Le baseline reproduit `market_score` avec les composantes externes (whales, sentiment, OI, funding, fear & greed) neutres à 50.",
        "- Une seule série historique : aucun résultat ici n'est une garantie de performance future.",
        "",
    ]
    return "\n".join(lines)


def write_report(
    asset: str,
    source: str,
    rows: Sequence[dict],
    windows: Sequence[Any],
    comparison: dict[str, dict[str, Any]],
    grid_result: Any,
    verdict: Any,
    momentum_config: Any = None,
    out_dir: str | Path | None = None,
    generated_on: str | None = None,
) -> Path:
    """Écrit research/results/<date>_<actif>.md (ou out_dir/<date>_<actif>.md) et renvoie le chemin."""
    today = generated_on or _date.today().isoformat()
    directory = Path(out_dir) if out_dir else RESULTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{today}_{_safe_name(asset)}.md"
    content = render_report(asset, source, rows, windows, comparison, grid_result, verdict, momentum_config, today)
    path.write_text(content, encoding="utf-8")
    return path


def render_summary(result: Any) -> str:
    """Résumé console (CLI) d'un `LabResult`."""
    lines = [
        f"Momentum Lab - {result.asset} ({result.source}) : {result.n_bars} bougies, {result.first_date} -> {result.last_date}",
        f"Verdict : {result.verdict.label}  [métrique {result.verdict.metric}]",
        f"Config retenue : {'momentum' if result.grid.selected_is_momentum else 'baseline'} - {result.grid.selected_config.label()}",
        "",
    ]
    for w in result.windows:
        res = result.comparison[w.name]
        parts = [
            f"{STRATEGY_LABELS[s]}: {res[s].total_return_pct:+.1f} % / DD {res[s].max_drawdown_pct:.1f} % / Sharpe {res[s].sharpe:.2f} / {res[s].n_trades} trades"
            for s in ("momentum", "baseline", "buy_hold")
        ]
        lines.append(f"[{w.name}] " + " | ".join(parts))
    return "\n".join(lines)
