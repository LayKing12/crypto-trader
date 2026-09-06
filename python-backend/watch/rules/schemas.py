"""Schémas pydantic des règles et actions : c'est ici que naissent les 422 de l'API.

`validate_params(family, params)` normalise les `params` d'une famille et lève `ValueError`
(message lisible) si le contenu est invalide. Utilisé par `RuleCreate`, par le PUT partiel
(fusion avec la règle existante) et par le store.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Family = Literal["take_profit", "allocation_drift", "sentiment_zone"]
Category = Literal["crypto", "stocks", "gold_miners", "cash"]
Zone = Literal["extreme_fear", "fear", "greed", "extreme_greed"]
Action = Literal["executed", "ignored", "postponed"]

CATEGORIES: tuple[str, ...] = ("crypto", "stocks", "gold_miners", "cash")
ZONES: tuple[str, ...] = ("extreme_fear", "fear", "greed", "extreme_greed")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TakeProfitLevel(_Strict):
    price: float | None = Field(default=None, gt=0)
    multiple_of_pru: float | None = Field(default=None, gt=0)
    sell_pct: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _one_target(self) -> "TakeProfitLevel":
        if (self.price is None) == (self.multiple_of_pru is None):
            raise ValueError("un palier porte soit `price`, soit `multiple_of_pru` (exactement un)")
        return self


class TakeProfitParams(_Strict):
    levels: list[TakeProfitLevel] = Field(min_length=1, max_length=10)
    pru: float | None = Field(default=None, gt=0)


class AllocationDriftParams(_Strict):
    category: Category
    target_pct: float = Field(ge=0, le=100)
    threshold_points: float = Field(default=5.0, gt=0, le=100)
    min_rebalance_usd: float = Field(default=100.0, ge=0)


class SentimentZoneParams(_Strict):
    zone: Zone
    consecutive_days: int = Field(default=3, ge=1, le=90)


PARAMS_BY_FAMILY: dict[str, type[BaseModel]] = {
    "take_profit": TakeProfitParams,
    "allocation_drift": AllocationDriftParams,
    "sentiment_zone": SentimentZoneParams,
}


def _flatten(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts)


def validate_params(family: str, params: Any) -> dict[str, Any]:
    """Retourne les params normalisés (dict JSON) ou lève ValueError."""
    model = PARAMS_BY_FAMILY.get(family)
    if model is None:
        raise ValueError(f"famille inconnue : {family!r} (attendu : {', '.join(PARAMS_BY_FAMILY)})")
    if not isinstance(params, dict):
        raise ValueError("params doit être un objet JSON")
    try:
        return model.model_validate(params).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError(f"params invalides pour {family} : {_flatten(exc)}") from exc


def normalize_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    return value or None


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    family: Family
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    symbol: str | None = Field(default=None, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name vide")
        return v

    @field_validator("symbol")
    @classmethod
    def _norm_symbol(cls, v: str | None) -> str | None:
        return normalize_symbol(v)

    @model_validator(mode="after")
    def _check(self) -> "RuleCreate":
        self.params = validate_params(self.family, self.params)
        if self.family == "take_profit" and not self.symbol:
            raise ValueError("take_profit exige un `symbol`")
        return self


class RuleUpdate(BaseModel):
    """Body partiel du PUT : la validation des params se fait après fusion (voir api/store)."""

    model_config = ConfigDict(extra="ignore")

    family: Family | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    symbol: str | None = Field(default=None, max_length=32)
    params: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name vide")
        return v

    @field_validator("symbol")
    @classmethod
    def _norm_symbol(cls, v: str | None) -> str | None:
        return normalize_symbol(v)


def merge_rule_update(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Fusionne un patch partiel avec la règle existante et valide le résultat (ValueError)."""
    merged = {
        "family": patch.get("family") or existing["family"],
        "name": patch.get("name") if patch.get("name") is not None else existing["name"],
        "enabled": patch["enabled"] if patch.get("enabled") is not None else existing["enabled"],
        "symbol": patch["symbol"] if "symbol" in patch and patch["symbol"] is not None else existing.get("symbol"),
        "params": patch["params"] if patch.get("params") is not None else existing.get("params", {}),
    }
    merged["params"] = validate_params(merged["family"], merged["params"])
    merged["symbol"] = normalize_symbol(merged["symbol"])
    if merged["family"] == "take_profit" and not merged["symbol"]:
        raise ValueError("take_profit exige un `symbol`")
    return merged


class AlertActionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Action
    postpone_hours: float = Field(default=24.0, gt=0, le=24 * 365)
