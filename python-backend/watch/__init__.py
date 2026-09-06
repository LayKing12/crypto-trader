"""Couche de veille / observation de CryptoMind (PR 3).

Observe en continu les marchés (Kraken, eToro) et l'actualité (Yahoo Finance, CryptoPanic)
et journalise des `Observation` (JSONL + mémoire). Cette couche est strictement passive :
elle ne déclenche aucune décision, aucun ordre, aucun signal. Elle n'importe jamais
`etoro.risk_guard`, `etoro.signal_service` ni `etoro.decision_log`.

Points d'entrée :
    watch.runtime.start() / stop()   -> tâches asyncio (no-op si WATCH_ENABLED=false)
    watch.api.router                 -> GET /watch/observations, GET /watch/status
"""
