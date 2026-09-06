"""Journal des observations de la veille : JSONL append-only + ring buffer mémoire.

Même philosophie que `etoro/decision_log.py` (sans l'importer) : chaque `Observation` est
écrite sur une ligne JSON dans `path` et conservée dans un `deque(maxlen=max_memory)` pour
alimenter `GET /watch/observations` sans relire le fichier. Le journal ne lève jamais vers
l'appelant. Si `path` est None ou vide, il fonctionne en mémoire seule (tests, dry-run).

Une observation n'est PAS une décision : elle ne porte ni action, ni ordre, ni score.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ObservationKind = Literal["price_move", "news", "watch_started", "error"]
ObservationSource = Literal["kraken", "etoro", "yahoo", "cryptopanic", "system"]

KINDS: frozenset[str] = frozenset({"price_move", "news", "watch_started", "error"})
SOURCES: frozenset[str] = frozenset({"kraken", "etoro", "yahoo", "cryptopanic", "system"})


class Observation(BaseModel):
    id: str
    at: datetime
    kind: ObservationKind
    source: ObservationSource
    symbol: str | None = None
    title: str
    detail: dict[str, Any] = Field(default_factory=dict)


def new_observation(
    kind: ObservationKind,
    source: ObservationSource,
    title: str,
    symbol: str | None = None,
    detail: dict[str, Any] | None = None,
) -> Observation:
    """Construit une Observation avec un id uuid4 et `at` = maintenant (UTC)."""
    return Observation(
        id=str(uuid.uuid4()),
        at=datetime.now(UTC),
        kind=kind,
        source=source,
        symbol=symbol.strip().upper() if symbol else None,
        title=title,
        detail=dict(detail or {}),
    )


class ObservationLog:
    """JSONL append-only (optionnel) + ring buffer mémoire des dernières observations."""

    def __init__(self, path: str | None, max_memory: int = 1000) -> None:
        self.path: Path | None = Path(path) if path else None
        self.max_memory = max(1, int(max_memory))
        self._buffer: deque[Observation] = deque(maxlen=self.max_memory)
        self.write_errors = 0

    @property
    def memory_only(self) -> bool:
        return self.path is None

    def __len__(self) -> int:
        return len(self._buffer)

    # ------------------------------------------------------------------ écriture
    def record(self, obs: Observation) -> Observation:
        """Ajoute l'observation au buffer puis au fichier JSONL. Ne lève jamais."""
        try:
            self._buffer.append(obs)
        except Exception:  # noqa: BLE001 - défensif
            logger.exception("Journal des observations : ajout mémoire impossible")
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(obs.model_dump(mode="json"), ensure_ascii=False)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:  # noqa: BLE001 - le journal ne doit jamais bloquer la veille
                self.write_errors += 1
                logger.exception("Journal des observations : écriture impossible dans %s", self.path)
        return obs

    # ------------------------------------------------------------------ lecture
    def recent(
        self,
        limit: int = 100,
        kind: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
    ) -> list[Observation]:
        """Dernières observations, de la plus récente à la plus ancienne, filtrées."""
        limit = max(0, int(limit))
        wanted_symbol = symbol.strip().upper() if symbol and symbol.strip() else None
        wanted_kind = kind.strip() if kind and kind.strip() else None
        wanted_source = source.strip().lower() if source and source.strip() else None
        out: list[Observation] = []
        if limit == 0:
            return out
        for obs in reversed(self._buffer):
            if wanted_kind and obs.kind != wanted_kind:
                continue
            if wanted_source and obs.source != wanted_source:
                continue
            if wanted_symbol and (obs.symbol or "").upper() != wanted_symbol:
                continue
            out.append(obs)
            if len(out) >= limit:
                break
        return out

    def load_tail(self) -> int:
        """Recharge les `max_memory` dernières lignes valides du fichier. Retourne le nombre chargé."""
        if self.path is None:
            return 0
        try:
            if not self.path.exists():
                return 0
            loaded: deque[Observation] = deque(maxlen=self.max_memory)
            corrupted = 0
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        loaded.append(Observation.model_validate_json(line))
                    except Exception:  # noqa: BLE001 - ligne corrompue : ignorée
                        corrupted += 1
            self._buffer.clear()
            self._buffer.extend(loaded)
            if corrupted:
                logger.warning(
                    "Journal des observations : %d ligne(s) corrompue(s) ignorée(s) dans %s",
                    corrupted, self.path,
                )
            logger.info("Journal des observations : %d observation(s) rechargée(s) depuis %s",
                        len(loaded), self.path)
            return len(loaded)
        except Exception:  # noqa: BLE001
            logger.exception("Journal des observations : rechargement impossible depuis %s", self.path)
            return 0
