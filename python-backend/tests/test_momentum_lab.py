"""
Tests du laboratoire momentum — séries synthétiques, déterministes, AUCUN appel réseau.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from research import backtest as bt
from research import data_sources as ds
from research.momentum_lab import (
    MomentumConfig,
    atr,
    atr_last,
    baseline_score,
    momentum_components,
    momentum_score,
    relative_strength,
    roc,
    run_lab,
    sma,
    sma_alignment,
    sma_alignment_signal,
)


# ── Fixtures synthétiques ────────────────────────────────────────────────────


def linear_closes(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


def uptrend_rows(n: int = 500, seed: int = 7) -> list[dict]:
    """Marche aléatoire haussière bruitée mais déterministe."""
    return ds.synthetic_rows(n=n, drift=0.002, volatility=0.015, seed=seed)


def two_regime_rows(n_up: int = 400, n_down: int = 300, seed: int = 11) -> list[dict]:
    drift = [0.003] * n_up + [-0.003] * n_down
    return ds.synthetic_rows(n=n_up + n_down, drift=drift, volatility=0.012, seed=seed)


# ── Indicateurs ──────────────────────────────────────────────────────────────


class TestIndicators:
    def test_roc_known_series(self):
        closes = [100.0, 110.0, 121.0, 133.1]
        out = roc(closes, 1)
        assert out[0] is None
        assert out[1] == pytest.approx(10.0)
        assert out[3] == pytest.approx(10.0)
        assert roc(closes, 2)[2] == pytest.approx(21.0)
        assert roc(closes, 3)[3] == pytest.approx(33.1)
        assert len(roc(closes, 7)) == 4 and all(v is None for v in roc(closes, 7))

    def test_roc_invalid_period(self):
        with pytest.raises(ValueError):
            roc([1.0, 2.0], 0)

    def test_relative_strength_known_series(self):
        asset = [100.0, 120.0]       # +20 %
        bench = [100.0, 110.0]       # +10 %
        rs = relative_strength(asset, bench, 1)
        assert rs[0] is None
        assert rs[1] == pytest.approx(1.2 / 1.1)
        # même performance -> ratio 1
        assert relative_strength(asset, asset, 1)[1] == pytest.approx(1.0)
        # sous-performance -> ratio < 1
        assert relative_strength(bench, asset, 1)[1] < 1.0

    def test_relative_strength_requires_aligned_lengths(self):
        with pytest.raises(ValueError):
            relative_strength([1.0, 2.0, 3.0], [1.0, 2.0], 1)

    def test_sma_known_series(self):
        closes = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = sma(closes, 3)
        assert out[:2] == [None, None]
        assert out[2:] == pytest.approx([2.0, 3.0, 4.0])
        # comparaison avec une moyenne naïve sur une série longue (stabilité de la somme glissante)
        closes = linear_closes(300, step=0.37)
        naive = [sum(closes[i - 19:i + 1]) / 20 for i in range(19, 300)]
        assert sma(closes, 20)[19:] == pytest.approx(naive)

    def test_sma_alignment_bullish_and_bearish(self):
        up = linear_closes(250, step=1.0)
        align_up = sma_alignment(up, (20, 50, 100, 200))
        assert align_up[198] is None and align_up[199] == 1.0
        assert sma_alignment_signal(align_up[-1]) == "bullish"
        down = linear_closes(250, start=1000.0, step=-1.0)
        align_down = sma_alignment(down, (20, 50, 100, 200))
        assert align_down[-1] == 0.0
        assert sma_alignment_signal(align_down[-1]) == "bearish"
        assert sma_alignment_signal(0.5) == "mixed"
        assert sma_alignment_signal(None) == "unknown"

    def test_atr_known_series(self):
        # TR des barres 1..3 : max(2, |12-9.5|, |10-9.5|) = 2.5 ; max(4, 3, 1) = 4 ; max(6, 4, 2) = 6
        highs = [10.0, 12.0, 14.0, 16.0, 20.0]
        lows = [9.0, 10.0, 10.0, 10.0, 15.0]
        closes = [9.5, 11.0, 12.0, 13.0, 18.0]
        out = atr(highs, lows, closes, 3)
        first = (2.5 + 4.0 + 6.0) / 3          # moyenne simple des 3 premiers TR
        assert out[:3] == [None, None, None]
        assert out[3] == pytest.approx(first)
        # TR barre 4 = max(20-15, |20-13|, |15-13|) = 7 -> lissage Wilder (atr*2 + 7)/3
        assert out[4] == pytest.approx((first * 2 + 7.0) / 3)
        assert atr_last(highs, lows, closes, 3) == round((first * 2 + 7.0) / 3, 4)

    def test_atr_matches_live_formula(self):
        """La copie de la formule doit rester identique à app.utils.math_utils.calc_atr."""
        from app.utils.math_utils import calc_atr

        rows = uptrend_rows(120)
        cols = ds.to_columns(rows)
        assert atr_last(cols["highs"], cols["lows"], cols["closes"], 14) == calc_atr(
            cols["highs"], cols["lows"], cols["closes"], 14
        )
        assert atr(cols["highs"], cols["lows"], cols["closes"], 14)[:14] == [None] * 14

    def test_momentum_score_range_and_warmup(self):
        rows = uptrend_rows(400)
        cols = ds.to_columns(rows)
        cfg = MomentumConfig()
        score = momentum_score(cols["closes"], cols["highs"], cols["lows"], config=cfg)
        assert len(score) == 400
        assert all(v is None for v in score[: cfg.warmup - 1])
        valid = [v for v in score if v is not None]
        assert valid and all(0.0 <= v <= 100.0 for v in valid)
        # tendance haussière nette -> score moyen au-dessus du neutre
        assert sum(valid) / len(valid) > 55.0

    def test_momentum_score_uses_benchmark(self):
        rows = uptrend_rows(400)
        cols = ds.to_columns(rows)
        flat_bench = [100.0] * 400
        strong_bench = [100.0 * (1.01 ** i) for i in range(400)]
        with_flat = momentum_components(cols["closes"], cols["highs"], cols["lows"], flat_bench)
        with_strong = momentum_components(cols["closes"], cols["highs"], cols["lows"], strong_bench)
        without = momentum_components(cols["closes"], cols["highs"], cols["lows"])
        assert without["rs"][-1] is None
        assert with_flat["rs"][-1] > with_strong["rs"][-1]
        assert with_flat["total"][-1] > with_strong["total"][-1]

    def test_baseline_score_reproduces_live_engine(self):
        rows = uptrend_rows(230)
        cols = ds.to_columns(rows)
        score = baseline_score(cols["closes"], cols["highs"], cols["lows"], cols["volumes"], min_bars=200)
        assert score[198] is None and score[199] is not None
        assert all(0.0 <= v <= 100.0 for v in score[199:])
        # vérification croisée sur la dernière barre avec les moteurs live
        from app.services.indicator_engine import compute_indicators
        from app.services.scoring_engine import compute_scores

        ind = compute_indicators("RESEARCH", cols["closes"], cols["highs"], cols["lows"], cols["volumes"])
        assert score[-1] == pytest.approx(compute_scores(ind).market_score)


# ── Découpes anti-overfitting ────────────────────────────────────────────────


class TestSplits:
    def test_split_chronological_preserves_order(self):
        data = list(range(100))
        ins, oos = bt.split_chronological(data, 0.6)
        assert ins == list(range(60))
        assert oos == list(range(60, 100))
        assert ins + oos == data
        assert ins == sorted(ins) and oos == sorted(oos)
        assert max(ins) < min(oos)

    def test_split_chronological_rejects_bad_ratio(self):
        with pytest.raises(ValueError):
            bt.split_chronological([1, 2, 3], 1.0)

    def test_regime_slices_detects_bull_and_bear(self):
        rows = two_regime_rows()
        slices = bt.regime_slices(rows)
        assert len(slices) >= 2
        regimes = {s.regime for s in slices}
        assert regimes == {"bull", "bear"}
        # tranches contiguës, chronologiques, sans chevauchement
        for a, b in zip(slices, slices[1:]):
            assert a.end == b.start
        assert slices[0].regime == "bull" and slices[-1].regime == "bear"
        assert all(s.n_days >= 30 for s in slices)

    def test_regime_slices_always_returns_two_windows(self):
        rows = uptrend_rows(400)
        slices = bt.regime_slices(rows)
        assert len(slices) >= 2
        assert slices[0].end == slices[1].start


# ── Moteur de backtest ───────────────────────────────────────────────────────


class TestBacktest:
    def test_buy_and_hold_on_uptrend(self):
        rows = uptrend_rows(500)
        res = bt.buy_and_hold(rows, fee_pct=0.26)
        assert res.total_return_pct > 0
        assert res.cagr_pct > 0
        assert 0.0 <= res.max_drawdown_pct < 100.0
        assert res.n_trades == 1
        assert res.win_rate_pct == 100.0
        assert len(res.equity_curve) == 500
        assert res.exposure_pct > 99.0

    def test_drawdown_and_sharpe_helpers(self):
        assert bt.max_drawdown([1.0, 1.2, 0.9, 1.3]) == pytest.approx(25.0)
        assert bt.max_drawdown([1.0, 1.1, 1.2]) == 0.0
        assert bt.sharpe_ratio([1.0, 1.0, 1.0]) == 0.0
        assert bt.sharpe_ratio([1.0, 1.01, 1.0, 1.02, 1.03]) > 0

    def test_fees_are_applied(self):
        rows = uptrend_rows(300)
        free = bt.buy_and_hold(rows, fee_pct=0.0)
        paid = bt.buy_and_hold(rows, fee_pct=1.0)
        assert paid.total_return_pct < free.total_return_pct
        # rendement B&H sans frais ≈ close_final / open_entrée (entrée à l'ouverture du 2e jour)
        expected = (rows[-1]["close"] / rows[1]["open"] - 1) * 100
        assert free.total_return_pct == pytest.approx(expected)

    def test_momentum_strategy_trades_and_stop(self):
        rows = two_regime_rows()
        cols = ds.to_columns(rows)
        scores = momentum_score(cols["closes"], cols["highs"], cols["lows"])
        atr_series = atr(cols["highs"], cols["lows"], cols["closes"])
        cfg = bt.BacktestConfig(entry_threshold=60, exit_threshold=45, atr_k=2.0, fee_pct=0.26)
        res = bt.run_backtest(rows, scores, cfg, atr_series=atr_series)
        assert res.n_trades >= 1
        assert 0.0 <= res.win_rate_pct <= 100.0
        assert all(t.exit_idx >= t.entry_idx for t in res.trades)
        assert {t.reason for t in res.trades} <= {"signal", "stop", "end"}
        # le momentum long-only doit perdre beaucoup moins que le B&H dans la phase baissière
        bear = bt.regime_slices(rows)[-1]
        m = bt.run_backtest(rows, scores, cfg, bear.start, bear.end, atr_series)
        bh = bt.buy_and_hold(rows, bear.start, bear.end)
        assert m.max_drawdown_pct < bh.max_drawdown_pct

    def test_no_lookahead_signal_executes_next_open(self):
        rows = uptrend_rows(300)
        scores = [None] * 300
        scores[100] = 100.0          # signal à la clôture du jour 100
        cfg = bt.BacktestConfig(entry_threshold=60, exit_threshold=-1, atr_k=None, fee_pct=0.0)
        res = bt.run_backtest(rows, scores, cfg)
        assert res.n_trades == 1
        assert res.trades[0].entry_idx == 101
        assert res.trades[0].entry_price == rows[101]["open"]


# ── Grid search anti-overfitting ─────────────────────────────────────────────


class TestGridSearch:
    def test_grid_search_rejects_config_better_only_in_sample(self):
        """
        In-sample : hausse ; out-of-sample : baisse.
        Momentum = toujours long (excellent IS, mauvais OOS).
        Baseline = long sur la 2e moitié de l'IS seulement, jamais en OOS (IS moins bon, OOS à 0).
        -> la candidate gagne en IS mais perd en OOS : grid_search DOIT renvoyer le baseline.
        """
        rows = two_regime_rows(n_up=300, n_down=200)
        n = len(rows)
        k = bt.split_index(n, 0.6)
        assert k == 300
        momentum_scores = [100.0] * n
        baseline_scores = [100.0 if k // 2 <= i < k else 0.0 for i in range(n)]
        grid = [
            bt.BacktestConfig(entry_threshold=e, exit_threshold=40, atr_k=None, fee_pct=0.26)
            for e in (50.0, 60.0, 70.0)
        ]
        base_cfg = bt.BacktestConfig(name="baseline", entry_threshold=60, exit_threshold=50, atr_k=None, fee_pct=0.26)
        result = bt.grid_search(rows, momentum_scores, baseline_scores, None, grid, base_cfg, 0.6, metric="total_return_pct")

        assert result.candidate_in_sample > result.baseline_in_sample          # meilleure en IS...
        assert result.candidate_out_of_sample < result.baseline_out_of_sample  # ...mais pire en OOS
        assert result.selected_is_momentum is False
        assert result.selected_config == base_cfg
        assert "baseline conservé" in result.reason

    def test_grid_search_keeps_config_better_in_and_out_of_sample(self):
        rows = uptrend_rows(500)
        n = len(rows)
        momentum_scores = [100.0] * n
        baseline_scores = [0.0] * n  # baseline jamais investi -> 0 %
        grid = [bt.BacktestConfig(entry_threshold=e, exit_threshold=40, atr_k=None) for e in (50.0, 60.0)]
        result = bt.grid_search(rows, momentum_scores, baseline_scores, None, grid, None, 0.6, metric="total_return_pct")
        assert result.selected_is_momentum is True
        assert result.selected_config in grid
        assert result.candidate_out_of_sample > result.baseline_out_of_sample

    def test_grid_search_is_deterministic(self):
        rows = uptrend_rows(400)
        cols = ds.to_columns(rows)
        scores = momentum_score(cols["closes"], cols["highs"], cols["lows"])
        atr_series = atr(cols["highs"], cols["lows"], cols["closes"])
        grid = bt.default_grid()
        a = bt.grid_search(rows, scores, [None] * 400, atr_series, grid)
        b = bt.grid_search(rows, scores, [None] * 400, atr_series, grid)
        assert a.selected_config == b.selected_config
        assert [e.in_sample for e in a.entries] == [e.in_sample for e in b.entries]


# ── Verdict et rapport ───────────────────────────────────────────────────────


class TestReport:
    def test_verdict_requires_oos_and_every_regime(self):
        rows = two_regime_rows()
        windows = bt.build_windows(rows)
        n = len(rows)
        cfg = bt.BacktestConfig(entry_threshold=60, exit_threshold=40, atr_k=None)
        comparison = bt.compare_strategies(rows, [100.0] * n, [0.0] * n, None, cfg, cfg, windows)
        verdict = bt.robustness_verdict(comparison, windows, metric="total_return_pct")
        # toujours long vs jamais investi : perd sur le régime baissier -> NON ROBUSTE
        assert verdict.robust is False
        assert verdict.label == "NON ROBUSTE"
        failed = [c for c in verdict.checks if not c.passed]
        assert failed and all(c.kind in ("out_of_sample", "regime") for c in failed)

    def test_report_file_contains_table_and_verdict(self, tmp_path: Path):
        rows = two_regime_rows()
        result = run_lab(
            rows, asset="SYNTH/USD", source="synthetic", out_dir=tmp_path, metric="sharpe",
            include_baseline=True,
        )
        path = result.report_path
        assert path is not None and path.exists()
        assert path.parent == tmp_path
        assert path.name.endswith("_SYNTH_USD.md")
        text = path.read_text(encoding="utf-8")
        assert "| Fenêtre | Stratégie | Rendement | CAGR | Drawdown max | Sharpe | Trades | Win rate |" in text
        assert "## Verdict : " in text
        assert ("ROBUSTE" in text) and (result.verdict.label in text)
        assert "Out-of-sample" in text and "Régime" in text
        assert "Momentum" in text and "Baseline" in text and "Buy & hold" in text
        assert "Config retenue" in text
        assert result.verdict.label in ("ROBUSTE", "NON ROBUSTE")
        # 3 stratégies x (IS + OOS + >= 2 régimes)
        assert len(result.windows) >= 4
        assert all(len(result.comparison[w.name]) == 3 for w in result.windows)


# ── Sources de données (hors ligne) ──────────────────────────────────────────


class TestDataSources:
    def test_csv_roundtrip(self, tmp_path: Path):
        rows = uptrend_rows(50)
        path = ds.save_csv(rows, tmp_path / "synth.csv")
        loaded = ds.load_csv(path)
        assert len(loaded) == 50
        assert loaded[0]["date"] == rows[0]["date"]
        assert loaded[-1]["close"] == pytest.approx(rows[-1]["close"])

    def test_csv_missing_column(self, tmp_path: Path):
        p = tmp_path / "bad.csv"
        p.write_text("date,open,high,low\n2020-01-01,1,2,0.5\n", encoding="utf-8")
        with pytest.raises(ValueError):
            ds.load_csv(p)

    def test_cache_roundtrip_and_expiry(self, tmp_path: Path):
        rows = uptrend_rows(10)
        ds.cache_write("unit_test", rows, cache_dir=tmp_path)
        assert ds.cache_read("unit_test", cache_dir=tmp_path) == rows
        assert ds.cache_read("unit_test", max_age_hours=0.0, cache_dir=tmp_path) is None
        assert ds.cache_read("absent", cache_dir=tmp_path) is None

    def test_parse_kraken_payload(self):
        payload = {
            "error": [],
            "result": {
                "XXBTZUSD": [
                    [1704067200, "42000.0", "43000.0", "41000.0", "42500.0", "42300.0", "1200.5", 3000],
                    [1704153600, "42500.0", "44000.0", "42000.0", "43500.0", "43000.0", "900.0", 2500],
                ],
                "last": 1704153600,
            },
        }
        rows = ds.parse_kraken_ohlc(payload)
        assert [r["date"] for r in rows] == ["2024-01-01", "2024-01-02"]
        assert rows[0]["close"] == 42500.0 and rows[0]["volume"] == 1200.5
        with pytest.raises(RuntimeError):
            ds.parse_kraken_ohlc({"error": ["EQuery:Unknown asset pair"]})

    def test_parse_coingecko_payload(self):
        day = 86400 * 1000
        payload = {
            "prices": [[19723 * day, 100.0], [19724 * day, 110.0], [19725 * day, 105.0]],
            "total_volumes": [[19723 * day, 1.0], [19724 * day, 2.0], [19725 * day, 3.0]],
        }
        rows = ds.parse_coingecko_market_chart(payload)
        assert len(rows) == 3
        assert rows[1]["open"] == 100.0 and rows[1]["close"] == 110.0
        assert rows[1]["high"] == 110.0 and rows[1]["low"] == 100.0
        assert rows[2]["volume"] == 3.0

    def test_align_on_dates(self):
        a = uptrend_rows(30)
        b = uptrend_rows(30)[5:]
        aa, bb = ds.align_on_dates(a, b)
        assert len(aa) == len(bb) == 25
        assert [r["date"] for r in aa] == [r["date"] for r in bb]

    def test_synthetic_rows_are_deterministic(self):
        assert ds.synthetic_rows(50, seed=3) == ds.synthetic_rows(50, seed=3)
        assert ds.synthetic_rows(50, seed=3) != ds.synthetic_rows(50, seed=4)
        assert json.dumps(ds.synthetic_rows(5))  # sérialisable (cache JSON)
        assert all(r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"]
                   for r in ds.synthetic_rows(200))
        assert not math.isnan(ds.synthetic_rows(5)[-1]["close"])
