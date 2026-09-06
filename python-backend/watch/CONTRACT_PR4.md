# PR 4 — Interface « Veille » : contrat entre backend, Telegram et frontend

Base : branche `feat/veille-ui` (issue de `feat/watch-layer`, qui contient `watch/` et `etoro/`).
Règle absolue : les règles génèrent des **alertes à valider manuellement**. Aucun ordre automatique.
`etoro.risk_guard` reste le seul chemin vers un trade réel ; aucun import de `risk_guard`,
`signal_service`, `decision_log` depuis `watch/` ou `notifications/`.

## Base de données

Les tables de la veille utilisent leur propre `WatchBase(DeclarativeBase)` dans
`watch/rules/models.py` (pas `app.database.Base`, pour rester testables sur sqlite+aiosqlite sans
asyncpg). En production, `watch/runtime.start()` fait `WatchBase.metadata.create_all` via
`app.database.engine` (import tardif). Types JSON : `sqlalchemy.JSON().with_variant(JSONB, "postgresql")`.

Tables : `watch_rules`, `watch_alerts`, `watch_portfolio_snapshots`, `watch_fng_daily`.

## Modèles JSON (ce que l'API renvoie ; le frontend s'y fie)

```jsonc
Rule = {
  "id": "uuid", "family": "take_profit" | "allocation_drift" | "sentiment_zone",
  "name": "string", "enabled": true, "symbol": "AAPL" | null,
  "params": { ... },               // dépend de family, voir ci-dessous
  "state": { "condition_active": false, "last_fired_at": null, "consecutive_days": 0 },
  "created_at": "iso", "updated_at": "iso"
}
// take_profit  : { "levels": [ { "price": 250.0 } | { "multiple_of_pru": 1.5 }, "sell_pct": 25 ], "pru": 190.0 }
// allocation_drift : { "category": "crypto"|"stocks"|"gold_miners"|"cash", "target_pct": 40, "threshold_points": 5, "min_rebalance_usd": 100 }
// sentiment_zone : { "zone": "extreme_fear"|"fear"|"greed"|"extreme_greed", "consecutive_days": 3 }

Alert = {
  "id": "uuid", "rule_id": "uuid", "family": "...", "rule_name": "string", "symbol": "AAPL" | null,
  "title": "AAPL a franchi le palier 1 (250.00) : vendre 25 %",
  "detail": { ... },
  "status": "pending" | "executed" | "ignored" | "postponed",
  "created_at": "iso", "acted_at": "iso" | null, "postponed_until": "iso" | null,
  "telegram_message_id": 123 | null
}
```

## Endpoints (préfixe `/watch`, dans `watch/api.py`)

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/watch/alerts?status=pending&limit=50` | `{ "alerts": [Alert], "count": n }` (status=all pour tout, plus récent en premier ; les `postponed` dont `postponed_until` est passé redeviennent `pending`) |
| POST | `/watch/alerts/{id}/action` body `{ "action": "executed"|"ignored"|"postponed", "postpone_hours": 24 }` | `Alert` mis à jour (404 si inconnu, 409 si déjà traité) |
| GET | `/watch/rules` | `{ "rules": [Rule] }` |
| POST | `/watch/rules` body `{ family, name, enabled, symbol, params }` | `Rule` (422 si params invalides pour la famille) |
| PUT | `/watch/rules/{id}` body partiel | `Rule` |
| DELETE | `/watch/rules/{id}` | `{ "deleted": true }` |
| GET | `/watch/portfolio/history?days=30` | `{ "points": [ { "at": "iso", "value_usd": 1234.5 } ], "days": 30 }` (7/30/90/365) |
| GET | `/watch/allocation` | `{ "total_usd": n, "categories": [ { "category", "value_usd", "actual_pct", "target_pct" \| null, "delta_points" \| null } ] }` |
| GET | `/watch/fng` | `{ "value": 25, "classification": "Extreme Fear", "date": "YYYY-MM-DD", "history": [ { "date", "value", "classification" } ] }` |

Les routes existantes `/watch/observations` et `/watch/status` restent inchangées.

## Moteur de règles (`watch/rules/engine.py`, pur, testable)

- `evaluate(rule, context) -> Alert | None` où `context = { "prices": {symbol: last}, "positions": {symbol: {"units", "pru", "value_usd"}}, "allocation": {category: {"value_usd", "actual_pct"}}, "total_usd", "fng_history": [{"date","value","classification"}], "now" }`.
- **Dédoublonnage sur la transition** : chaque règle porte `state.condition_active`. On n'émet une alerte
  que si la condition passe de faux à vrai ; tant qu'elle reste vraie, rien ; quand elle redevient
  fausse, `condition_active` repasse à faux (réarmement). Pour `take_profit`, l'état est par palier
  (`state.levels_fired: [bool]`). Pour `sentiment_zone`, la condition est « zone atteinte N jours
  consécutifs » calculée sur `fng_history`, jamais sur une valeur isolée.
- `allocation_drift` : `|actual_pct - target_pct| >= threshold_points` ET montant à rééquilibrer
  `>= min_rebalance_usd`.

## Contexte de portefeuille (`watch/portfolio.py`)

- `collect_snapshot(etoro_service, kraken_balance_fn) -> {total_usd, categories, positions}` :
  eToro via `get_open_positions()` + `get_account_balance()` (symboles via instruments), Kraken via un
  callable async optionnel `-> {symbol: value_usd}` ; catégories par défaut : `crypto` (Kraken),
  `gold_miners` (NEM, AEM), `stocks` (autres eToro), `cash` (equity − positions). Cibles = règles
  `allocation_drift` actives (target_pct par catégorie).
- Un snapshot `watch_portfolio_snapshots` par heure maximum, écrit par `runtime` à chaque tick marché.
- Fear & Greed : `watch/fng_source.py`, `https://api.alternative.me/fng/?limit=30&format=json`,
  sans clé, cache 1 h, historique persisté dans `watch_fng_daily`.

## Telegram (`notifications/`)

- `notifications/config.py` : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID_WATCH`, `TELEGRAM_CHAT_ID_TRADING`,
  `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ENABLED` (false). Seuls ces `chat_id` sont acceptés : tout
  update d'un autre chat/utilisateur est ignoré et journalisé.
- `notifications/telegram_service.py` : `send_text(chat, text, buttons=None) -> int | None`
  (`chat` = "watch" | "trading"), `send_watch_alert(alert: dict) -> int | None` avec clavier inline
  `[Exécuté] [Ignoré] [Reporté]` et `callback_data = "alert:<id>:<action>"`, `send_trading_event(text)`.
  httpx async, ne lève jamais, no-op si non configuré.
- `notifications/api.py` : `POST /telegram/webhook` (header `X-Telegram-Bot-Api-Secret-Token` comparé
  en temps constant), traite `callback_query` : vérifie le chat, appelle
  `watch.rules.store.apply_alert_action(alert_id, action, actor="telegram")` (import tardif),
  répond `answerCallbackQuery` et édite le message (`editMessageReplyMarkup` pour retirer les boutons).
  `setup_webhook(public_url)` enregistre l'URL avec `secret_token`.
- Le backend des règles appelle `notifications.telegram_service.send_watch_alert` à la création d'une
  alerte (import tardif, no-op si absent) et stocke `telegram_message_id`.
- Twilio reste en place dans cette PR (retrait après validation de Telegram en production).

## Frontend (`frontend/src/tabs/Veille.jsx`)

Même style que `Etoro.jsx` (THEME inline, BOT_API). Ordre vertical :
1. **File d'alertes** (élément central) : cartes `pending` d'abord : titre, famille + nom de règle,
   horodatage, boutons `Exécuté` / `Ignoré` / `Reporté` (POST action ; Reporté demande les heures,
   défaut 24). Les alertes traitées récentes en dessous, grisées.
2. **Règles** : liste + formulaire par famille (3 onglets internes), activer/désactiver, supprimer.
3. **Courbe de valeur du portefeuille** : boutons 7j/30j/90j/1an, recharts `AreaChart` (déjà dans le
   projet), `/watch/portfolio/history`.
4. **Allocation réelle vs cible** : barres par catégorie avec l'écart en points, `/watch/allocation`.
5. **Sentiment** : valeur Fear & Greed du jour + mini historique, `/watch/fng`.
Polling 30 s pour les alertes, 5 min pour le reste. Onglet `{ id: "veille", label: "Veille", icon: "👁️" }`.
