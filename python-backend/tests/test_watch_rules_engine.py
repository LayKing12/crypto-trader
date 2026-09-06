"""Tests — watch/rules/engine.py (moteur pur : transition, paliers, allocation, sentiment)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from watch.rules.engine import classify_fng, evaluate, zone_streak

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _ctx(**kw):
    base = {"prices": {}, "positions": {}, "allocation": {}, "total_usd": 0.0, "fng_history": [], "now": NOW}
    base.update(kw)
    return base


def _rule(family, params, symbol=None, state=None, name="r"):
    return {"id": "rule-1", "family": family, "name": name, "enabled": True, "symbol": symbol,
            "params": params, "state": state or {}}


def _fng(values):
    """values[0] = aujourd'hui, values[1] = hier, ..."""
    today = date(2026, 9, 6)
    return [{"date": (today - timedelta(days=i)).isoformat(), "value": v} for i, v in enumerate(values)]


# ------------------------------------------------------------------ classification

def test_classify_fng_bands():
    assert classify_fng(0) == "extreme_fear" and classify_fng(24) == "extreme_fear"
    assert classify_fng(25) == "fear" and classify_fng(44) == "fear"
    assert classify_fng(45) == "neutral" and classify_fng(55) == "neutral"
    assert classify_fng(56) == "greed" and classify_fng(75) == "greed"
    assert classify_fng(76) == "extreme_greed" and classify_fng("100") == "extreme_greed"
    assert classify_fng("x") is None


# ------------------------------------------------------------------ take_profit

def test_take_profit_transition_dedup_and_rearm():
    rule = _rule("take_profit", {"levels": [{"price": 250.0, "sell_pct": 25}], "pru": 190.0}, symbol="AAPL")
    ctx = _ctx(prices={"AAPL": 251.0}, positions={"AAPL": {"units": 10, "pru": 190.0, "value_usd": 2510}})
    alert, state = evaluate(rule, ctx)
    assert alert is not None
    assert alert["title"] == "AAPL a franchi le palier 1 (250.00) : vendre 25 %"
    assert alert["detail"]["units_to_sell"] == 2.5 and alert["symbol"] == "AAPL"
    assert state["levels_fired"] == [True] and state["condition_active"] is True
    assert state["last_fired_at"] == NOW.isoformat()

    rule["state"] = state
    alert2, state2 = evaluate(rule, ctx)          # condition toujours vraie : rien
    assert alert2 is None and state2["levels_fired"] == [True]

    rule["state"] = state2
    alert3, state3 = evaluate(rule, _ctx(prices={"AAPL": 240.0}))   # retour sous le palier : réarmement
    assert alert3 is None and state3["levels_fired"] == [False] and state3["condition_active"] is False

    rule["state"] = state3
    alert4, _ = evaluate(rule, _ctx(prices={"AAPL": 255.0}))        # nouveau franchissement : nouvelle alerte
    assert alert4 is not None


def test_take_profit_levels_are_independent():
    rule = _rule("take_profit", {
        "levels": [{"price": 250.0, "sell_pct": 25}, {"multiple_of_pru": 1.5, "sell_pct": 50}],
        "pru": 200.0,
    }, symbol="AAPL")
    a1, s1 = evaluate(rule, _ctx(prices={"AAPL": 260.0}))
    assert a1 is not None and a1["detail"]["level"] == 1 and s1["levels_fired"] == [True, False]
    rule["state"] = s1
    a2, s2 = evaluate(rule, _ctx(prices={"AAPL": 265.0}))
    assert a2 is None                                                # palier 2 (300) pas atteint
    rule["state"] = s2
    a3, s3 = evaluate(rule, _ctx(prices={"AAPL": 301.0}))
    assert a3 is not None and a3["detail"]["level"] == 2 and a3["detail"]["target_price"] == 300.0
    assert "vendre 50 %" in a3["title"] and s3["levels_fired"] == [True, True]
    rule["state"] = s3
    a4, s4 = evaluate(rule, _ctx(prices={"AAPL": 280.0}))            # sous le 2, au-dessus du 1
    assert a4 is None and s4["levels_fired"] == [True, False]
    rule["state"] = s4
    a5, _ = evaluate(rule, _ctx(prices={"AAPL": 305.0}))             # palier 2 retiré seul
    assert a5 is not None and a5["detail"]["level"] == 2


def test_take_profit_without_price_or_pru_is_silent():
    rule = _rule("take_profit", {"levels": [{"multiple_of_pru": 1.2, "sell_pct": 10}]}, symbol="NVDA")
    assert evaluate(rule, _ctx(prices={"NVDA": 1000.0}))[0] is None            # pas de PRU
    assert evaluate(rule, _ctx(prices={}, positions={"NVDA": {"pru": 100.0}}))[0] is None  # pas de prix
    alert, _ = evaluate(rule, _ctx(prices={"NVDA": 121.0}, positions={"NVDA": {"units": 3, "pru": 100.0}}))
    assert alert is not None and alert["detail"]["target_price"] == 120.0    # PRU pris sur la position


# ------------------------------------------------------------------ allocation_drift

def test_allocation_drift_threshold_and_min_amount():
    params = {"category": "crypto", "target_pct": 40, "threshold_points": 5, "min_rebalance_usd": 100}
    rule = _rule("allocation_drift", params)
    # 43 % : écart 3 pts < 5 -> rien
    alert, state = evaluate(rule, _ctx(total_usd=10000, allocation={"crypto": {"value_usd": 4300, "actual_pct": 43}}))
    assert alert is None and state["condition_active"] is False
    # 48 % sur 1 000 USD : écart 8 pts mais 80 USD < 100 -> rien
    alert, state = evaluate(rule, _ctx(total_usd=1000, allocation={"crypto": {"value_usd": 480, "actual_pct": 48}}))
    assert alert is None and state["condition_active"] is False
    # 48 % sur 10 000 USD : 8 pts et 800 USD -> alerte
    alert, state = evaluate(rule, _ctx(total_usd=10000, allocation={"crypto": {"value_usd": 4800, "actual_pct": 48}}))
    assert alert is not None and state["condition_active"] is True
    assert alert["detail"]["rebalance_usd"] == 800.0 and alert["detail"]["direction"] == "alléger"
    assert "Allocation crypto à 48.0 %" in alert["title"]
    # même état : pas de doublon
    rule["state"] = state
    assert evaluate(rule, _ctx(total_usd=10000, allocation={"crypto": {"value_usd": 4900, "actual_pct": 49}}))[0] is None
    # retour dans la bande puis nouvelle dérive : nouvelle alerte
    _, state = evaluate(rule, _ctx(total_usd=10000, allocation={"crypto": {"value_usd": 4000, "actual_pct": 40}}))
    assert state["condition_active"] is False
    rule["state"] = state
    alert, _ = evaluate(rule, _ctx(total_usd=10000, allocation={"crypto": {"value_usd": 3000, "actual_pct": 30}}))
    assert alert is not None and alert["detail"]["direction"] == "renforcer"


def test_allocation_drift_missing_category_counts_as_zero():
    rule = _rule("allocation_drift", {"category": "gold_miners", "target_pct": 10, "threshold_points": 5,
                                      "min_rebalance_usd": 50})
    alert, _ = evaluate(rule, _ctx(total_usd=5000, allocation={"stocks": {"value_usd": 5000, "actual_pct": 100}}))
    assert alert is not None and alert["detail"]["actual_pct"] == 0.0
    assert evaluate(rule, _ctx(total_usd=0, allocation={}))[0] is None   # portefeuille vide : rien


# ------------------------------------------------------------------ sentiment_zone

def test_sentiment_zone_requires_consecutive_days():
    rule = _rule("sentiment_zone", {"zone": "extreme_fear", "consecutive_days": 3})
    # 2 jours sur 3 (hier hors zone) : rien
    alert, state = evaluate(rule, _ctx(fng_history=_fng([20, 30, 15, 10])))
    assert alert is None and state["consecutive_days"] == 1 and state["condition_active"] is False
    # 3 jours consécutifs : alerte
    alert, state = evaluate(rule, _ctx(fng_history=_fng([20, 18, 15, 60])))
    assert alert is not None and state["consecutive_days"] == 3 and state["condition_active"] is True
    assert "Extreme Fear" in alert["title"] and alert["detail"]["required_days"] == 3
    # 4e jour : condition toujours vraie, pas de doublon
    rule["state"] = state
    alert, state = evaluate(rule, _ctx(fng_history=_fng([12, 20, 18, 15])))
    assert alert is None and state["consecutive_days"] == 4
    # sortie de zone : réarmement
    rule["state"] = state
    alert, state = evaluate(rule, _ctx(fng_history=_fng([50, 12, 20, 18])))
    assert alert is None and state["condition_active"] is False and state["consecutive_days"] == 0


def test_sentiment_zone_never_fires_on_a_single_isolated_value():
    rule = _rule("sentiment_zone", {"zone": "extreme_greed", "consecutive_days": 2})
    assert evaluate(rule, _ctx(fng_history=[{"date": "2026-09-06", "value": 90}]))[0] is None
    assert evaluate(rule, _ctx(fng_history=[]))[0] is None
    # trou dans l'historique : la série n'est pas consécutive
    hist = [{"date": "2026-09-06", "value": 90}, {"date": "2026-09-04", "value": 92}]
    assert zone_streak(hist, "extreme_greed") == 1
    # classification textuelle acceptée quand la valeur manque
    hist = [{"date": "2026-09-06", "classification": "Extreme Greed"}, {"date": "2026-09-05", "classification": "extreme_greed"}]
    assert zone_streak(hist, "extreme_greed") == 2


def test_unknown_family_is_silent():
    alert, state = evaluate({"family": "nope", "state": {}}, _ctx())
    assert alert is None and state["condition_active"] is False
