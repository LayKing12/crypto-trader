"""Journal des décisions de l'Agent Portfolio eToro : JSONL append-only + ring buffer mémoire.

Chaque `DecisionRecord` est écrit sur une ligne JSON (`model_dump(mode="json")`) dans
`settings.etoro_decisions_path` et conservé dans un `deque(maxlen=max_memory)` pour
alimenter `GET /etoro/decisions` sans relire le fichier. Le journal ne lève jamais
d'exception vers l'appelant : une erreur d'écriture est loguée et l'agent continue.
Si `path` est None ou vide, le journal fonctionne en mémoire seule (tests, dry-run).
Persistance distante optionnelle (`remote`, duck typing d'un `SupabaseStore`) : `record()`
écrit local PUIS `remote.insert_decision(...)`, `load_tail()` prend `remote.recent_decisions()`
si disponible et non vide, sinon le fichier local. La couche distante ne lève jamais.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from etoro.models import DecisionRecord

if TYPE_CHECKING:  # pragma: no cover - évite un import circulaire au niveau module
    from etoro.supabase_store import SupabaseStore

logger = logging.getLogger(__name__)


def new_record(kind: str, reason: str, action: str, **fields: Any) -> DecisionRecord:
    """Construit un DecisionRecord avec un id uuid4 et `at` = maintenant (UTC)."""
    return DecisionRecord(
        id=str(uuid.uuid4()), at=datetime.now(UTC), kind=kind, reason=reason, action=action, **fields
    )


class DecisionLog:
    """JSONL append-only (optionnel) + ring buffer mémoire des dernières décisions."""

    def __init__(
        self, path: str | None, max_memory: int = 500, remote: "SupabaseStore | None" = None
    ) -> None:
        self.path: Path | None = Path(path) if path else None
        self.max_memory = max(1, int(max_memory))
        self.remote = remote
        self._buffer: deque[DecisionRecord] = deque(maxlen=self.max_memory)

    @property
    def memory_only(self) -> bool:
        return self.path is None

    def __len__(self) -> int:
        return len(self._buffer)

    # ------------------------------------------------------------------ écriture
    def record(self, rec: DecisionRecord) -> DecisionRecord:
        """Ajoute la décision au buffer puis au fichier JSONL. Ne lève jamais."""
        try:
            self._buffer.append(rec)
        except Exception:  # noqa: BLE001 - défensif, un deque ne lève pas en pratique
            logger.exception("Journal des décisions : ajout mémoire impossible")
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(rec.model_dump(mode="json"), ensure_ascii=False)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:  # noqa: BLE001 - le journal ne doit jamais bloquer l'agent
                logger.exception("Journal des décisions : écriture impossible dans %s", self.path)
        if self.remote is not None:
            try:
                ok = self.remote.insert_decision(rec.model_dump(mode="json"))
                if ok is False:
                    logger.warning("Journal des décisions : insertion Supabase refusée pour %s", rec.id)
            except Exception:  # noqa: BLE001 - la couche distante ne bloque jamais
                logger.exception("Journal des décisions : insertion Supabase impossible pour %s", rec.id)
        return rec

    # ------------------------------------------------------------------ lecture
    def recent(
        self, limit: int = 100, kind: str | None = None, symbol: str | None = None
    ) -> list[DecisionRecord]:
        """Dernières décisions, de la plus récente à la plus ancienne, filtrées par kind / symbole."""
        limit = max(0, int(limit))
        wanted_symbol = symbol.strip().upper() if symbol else None
        out: list[DecisionRecord] = []
        if limit == 0:
            return out
        for rec in reversed(self._buffer):
            if kind and rec.kind != kind:
                continue
            if wanted_symbol and (rec.symbol or "").upper() != wanted_symbol:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    def load_tail(self) -> None:
        """Recharge les `max_memory` dernières décisions au démarrage : Supabase si branché
        et non vide, sinon les dernières lignes valides du fichier."""
        if self._load_tail_remote():
            return
        if self.path is None:
            return
        try:
            if not self.path.exists():
                return
            loaded: deque[DecisionRecord] = deque(maxlen=self.max_memory)
            corrupted = 0
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        loaded.append(DecisionRecord.model_validate_json(line))
                    except Exception:  # noqa: BLE001 - ligne corrompue : ignorée
                        corrupted += 1
            self._buffer.clear()
            self._buffer.extend(loaded)
            if corrupted:
                logger.warning(
                    "Journal des décisions : %d ligne(s) corrompue(s) ignorée(s) dans %s", corrupted, self.path
                )
            logger.info("Journal des décisions : %d décision(s) rechargée(s) depuis %s", len(loaded), self.path)
        except Exception:  # noqa: BLE001
            logger.exception("Journal des décisions : rechargement impossible depuis %s", self.path)

    def _load_tail_remote(self) -> bool:
        """Recharge le buffer depuis Supabase. True si au moins une décision valide a été chargée."""
        if self.remote is None:
            return False
        try:
            rows = self.remote.recent_decisions(self.max_memory)
        except Exception:  # noqa: BLE001 - la couche distante ne bloque jamais
            logger.exception("Journal des décisions : lecture Supabase impossible, repli sur le fichier")
            return False
        if not rows:
            return False
        loaded: list[DecisionRecord] = []
        invalid = 0
        for row in rows:  # du plus récent au plus ancien
            try:
                loaded.append(DecisionRecord.model_validate(row))
            except Exception:  # noqa: BLE001 - ligne invalide : ignorée
                invalid += 1
        if invalid:
            logger.warning("Journal des décisions : %d ligne(s) Supabase invalide(s) ignorée(s)", invalid)
        if not loaded:
            return False
        self._buffer.clear()
        self._buffer.extend(reversed(loaded))  # buffer du plus ancien au plus récent
        logger.info("Journal des décisions : %d décision(s) rechargée(s) depuis Supabase", len(loaded))
        return True
