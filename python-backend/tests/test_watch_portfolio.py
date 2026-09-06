"""Tests — watch/portfolio.py (snapshot eToro + Kraken, catégories, deltas, tolérance aux erreurs)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from etoro.models import Instrument, Position, Side
from watch.portfolio import build_categories, category_for, collect_snapshot


def _pos(instrument_id, amount, open_rate, pnl=0.0):
    return Position(position_id=f"p{instrument_id}-{amount}", instrument_id=instrument_id, side=Side.BUY,
                    amount=amount, open_rate=open_rate, opened_at=datetime.now(timezone.utc), unrealized_pnl=pnl)


class FakeEtoro:
    def __init__(self, positions, equity, fail=None):
        self.positions, self.equity, self.fail = positions, equity, fail or set()

    async def get_instruments(self, symbols):
        if "instruments" in self.fail:
            raise RuntimeError("boom")
        table = {"AAPL": 1001, "NEM": 1002, "AEM": 1003}
        return [Instrument(symbol=s, instrument_id=table[s]) for s in symbols if s in table]

    async def get_open_positions(self):
        if "positions" in self.fail:
            raise RuntimeError("boom")
        return self.positions

    async def get_account_balance(self):
        if "balance" in self.fail:
            raise RuntimeError("boom")
        return self.equity


async def _kraken():
    return {"BTCUSD": 3000.0, "ETHUSD": 1000.0, "DUST": 0.0}


def test_category_for():
    assert category_for("NEM") == "gold_miners" and category_for("aem") == "gold_miners"
    assert category_for("AAPL") == "stocks" and category_for("BTCUSD", "kraken") == "crypto"


async def test_collect_snapshot_categories_and_deltas():
    etoro = FakeEtoro([_pos(1001, 1000, 200, pnl=100), _pos(1001, 500, 250), _pos(1002, 400, 40, pnl=-50)], equity=3000)
    snap = await collect_snapshot(etoro, _kraken, targets={"crypto": 40, "cash": 5}, symbols=["AAPL", "NEM", "AEM"])
    # stocks = AAPL 1100 + 500 = 1600 ; gold = NEM 350 ; cash = 3000 - 1950 = 1050 ; crypto = 4000 ; total 7000
    cats = {c["category"]: c for c in snap["categories"]}
    assert [c["category"] for c in snap["categories"]] == ["crypto", "stocks", "gold_miners", "cash"]
    assert cats["crypto"]["value_usd"] == 4000.0 and cats["stocks"]["value_usd"] == 1600.0
    assert cats["gold_miners"]["value_usd"] == 350.0 and cats["cash"]["value_usd"] == 1050.0
    assert snap["total_usd"] == 7000.0
    assert cats["crypto"]["actual_pct"] == pytest.approx(57.14, abs=0.01)
    assert cats["crypto"]["target_pct"] == 40.0 and cats["crypto"]["delta_points"] == pytest.approx(17.14, abs=0.01)
    assert cats["cash"]["delta_points"] == pytest.approx(10.0, abs=0.01)
    assert cats["stocks"]["target_pct"] is None and cats["stocks"]["delta_points"] is None

    aapl = snap["positions"]["AAPL"]
    assert aapl["units"] == pytest.approx(7.0) and aapl["pru"] == pytest.approx(1500 / 7)   # PRU moyen pondéré
    assert aapl["value_usd"] == 1600.0 and aapl["category"] == "stocks" and aapl["positions"] == 2
    assert snap["positions"]["BTCUSD"] == {"units": None, "pru": None, "value_usd": 3000.0, "category": "crypto",
                                           "source": "kraken", "positions": 1}
    assert "DUST" not in snap["positions"]
    assert snap["allocation"]["crypto"] == {"value_usd": 4000.0, "actual_pct": cats["crypto"]["actual_pct"]}
    assert snap["at"].endswith("+00:00")


async def test_collect_snapshot_tolerates_missing_or_failing_sources():
    snap = await collect_snapshot(None, None)
    assert snap["total_usd"] == 0.0 and snap["positions"] == {}
    assert all(c["value_usd"] == 0.0 and c["actual_pct"] == 0.0 for c in snap["categories"])

    async def bad_kraken():
        raise RuntimeError("kraken down")

    etoro = FakeEtoro([_pos(1001, 1000, 100)], equity=1500, fail={"balance"})
    snap = await collect_snapshot(etoro, bad_kraken, symbols=["AAPL"])
    cats = {c["category"]: c for c in snap["categories"]}
    assert cats["stocks"]["value_usd"] == 1000.0 and cats["cash"]["value_usd"] == 0.0 and cats["crypto"]["value_usd"] == 0.0

    etoro = FakeEtoro([_pos(1001, 1000, 100)], equity=1500, fail={"positions"})
    snap = await collect_snapshot(etoro, None, symbols=["AAPL"])
    cats = {c["category"]: c for c in snap["categories"]}
    assert cats["stocks"]["value_usd"] == 0.0 and cats["cash"]["value_usd"] == 1500.0

    # instruments inconnus : symbole de repli, classé en stocks ; instruments en erreur : idem
    etoro = FakeEtoro([_pos(4242, 200, 10)], equity=200, fail={"instruments"})
    snap = await collect_snapshot(etoro, None, symbols=["AAPL"])
    assert snap["positions"]["ETORO:4242"]["category"] == "stocks" and snap["total_usd"] == 200.0


def test_build_categories_negative_cash_clamped():
    cats = build_categories({"crypto": 100, "cash": -50}, targets={"crypto": "80"})
    by = {c["category"]: c for c in cats}
    assert by["cash"]["value_usd"] == 0.0 and by["crypto"]["actual_pct"] == 100.0
    assert by["crypto"]["target_pct"] == 80.0 and by["crypto"]["delta_points"] == 20.0
