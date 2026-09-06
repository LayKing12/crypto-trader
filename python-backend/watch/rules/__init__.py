"""Règles de veille : alertes à valider manuellement, jamais d'ordre automatique.

    models.py  -> tables SQLAlchemy sur `WatchBase` (propre au module)
    schemas.py -> validation pydantic des règles / actions (422 côté API)
    engine.py  -> moteur pur `evaluate(rule, context) -> (alert | None, new_state)`
    store.py   -> persistance async (`RulesStore`) + `apply_alert_action` module

Aucun import de `etoro.risk_guard`, `etoro.signal_service`, `etoro.decision_log`.
"""
from __future__ import annotations

from .models import WatchBase

__all__ = ["WatchBase"]
