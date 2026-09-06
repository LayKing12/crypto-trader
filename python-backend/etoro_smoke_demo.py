"""Test de fumée du compte démo eToro : lecture seule, aucun ordre passé.

Usage : python scripts/smoke_demo.py   (lit .env à la racine du projet)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from etoro.config import get_settings  # noqa: E402
from etoro.etoro_service import EtoroApiError, EtoroService  # noqa: E402


async def main() -> int:
    s = get_settings()
    if not s.etoro_api_key:
        print("ETORO_API_KEY vide : remplissez .env d'abord.")
        return 2
    print(f"mode={s.etoro_mode}  base={s.base_url}  univers={s.universe}")
    svc = EtoroService(s)
    ok = True
    try:
        print("health        :", await svc.health())
        instruments = await svc.get_instruments(s.universe)
        print(f"instruments   : {len(instruments)}/{len(s.universe)} résolus")
        for inst in instruments:
            print(f"   {inst.symbol:8s} id={inst.instrument_id:<8d} {inst.asset_class:10s} {inst.display_name}")
        missing = set(s.universe) - {i.symbol for i in instruments}
        if missing:
            ok = False
            print("   NON RESOLUS :", ", ".join(sorted(missing)))
        quotes = await svc.get_quotes([i.instrument_id for i in instruments])
        print(f"cours         : {len(quotes)} reçus")
        for q in quotes[:8]:
            print(f"   id={q.instrument_id:<8d} bid={q.bid:<12g} ask={q.ask:<12g} {q.timestamp:%H:%M:%S}")
        print("equity        :", await svc.get_account_balance())
        positions = await svc.get_open_positions()
        print(f"positions     : {len(positions)} ouvertes")
        for p in positions:
            print(f"   {p.position_id} id={p.instrument_id} {p.side.value} {p.amount} @ {p.open_rate} SL={p.stop_loss_rate} TP={p.take_profit_rate}")
    except EtoroApiError as exc:
        ok = False
        print(f"ERREUR API eToro : HTTP {exc.status_code} -> {exc.payload}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"ERREUR : {type(exc).__name__}: {exc}")
    finally:
        await svc.aclose()
    print("\nRESULTAT :", "OK" if ok else "A CORRIGER")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
