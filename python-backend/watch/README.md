# watch/ — couche de veille et d'observation

Outil d'observation continue, **jamais un déclencheur**. Aucun import de `etoro.risk_guard`,
`etoro.signal_service` ni `etoro.decision_log` : les décisions de trade restent sur leur cycle propre,
`risk_guard` reste le seul chemin vers un ordre.

## Composants

| Module | Rôle |
|---|---|
| `config.py` | `WatchSettings` (variables `WATCH_*`, inactif par défaut) |
| `observation_log.py` | journal JSONL append-only + ring buffer mémoire, distinct de `decision_log.py` |
| `market_watch_service.py` | poll des prix Kraken (`market_data_service.get_price`) et eToro (`EtoroService.get_quotes`), observation `price_move` quand la variation depuis la référence dépasse `WATCH_MOVE_THRESHOLD_PCT`, référence remise à jour après chaque observation |
| `news_watch_service.py` | poll RSS Yahoo Finance par ticker et CryptoPanic (si `CRYPTOPANIC_TOKEN`), filtrage sur les tickers suivis, dédoublonnage par URL/titre, observation `news` |
| `api.py` | `GET /watch/observations?limit=&kind=&symbol=&source=`, `GET /watch/status` |
| `runtime.py` | `start()` / `stop()` appelés par le lifespan de `main.py`, no-op si `WATCH_ENABLED=false` |

## Variables

| Variable | Défaut | Description |
|---|---|---|
| `WATCH_ENABLED` | `false` | Active les deux watchers |
| `WATCH_INTERVAL_S` | `300` | Cadence du watcher de prix |
| `WATCH_MOVE_THRESHOLD_PCT` | `2.0` | Variation minimale journalisée |
| `WATCH_KRAKEN_PAIRS` | paires suivies par le bot | CSV |
| `WATCH_ETORO_SYMBOLS` | `AAPL,MSFT,NVDA,AMZN,GOOGL,META,NEM,AEM` | CSV |
| `WATCH_NEWS_INTERVAL_S` | `900` | Cadence du watcher de news |
| `WATCH_NEWS_SOURCES` | `yahoo,cryptopanic` | CSV |
| `CRYPTOPANIC_TOKEN` | | optionnel, source ignorée sans token |
| `WATCH_LOG_PATH` | `/data/observations.jsonl` | vide = mémoire seule |
| `WATCH_MAX_MEMORY` | `1000` | taille du ring buffer |

## Tests

```bash
python -m pytest tests/test_watch_*.py -q
```

Sans réseau : prix injectés par des fakes, RSS mocké par respx.
