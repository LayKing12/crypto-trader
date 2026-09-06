# API publique eToro — synthèse pour `etoro/etoro_service.py`

Recherche effectuée le 2026-09-05 sur la doc publique. Chaque élément est marqué
**[CONFIRMÉ]** (vu dans la doc officielle) ou **[HYPOTHÈSE]** (déduit / non vérifié, à
valider sur le compte démo). Les chemins incertains sont isolés dans `ENDPOINTS` en tête de
`etoro/etoro_service.py` avec un commentaire « À VÉRIFIER ».

## Sources

- Portail builders : https://builders.etoro.com (accueil, `/reference`, `/playground`)
- Getting started v2 : https://builders.etoro.com/learn/getting-started-with-etoro-api-v2
- Authentification : https://builders.etoro.com/learn/authentication-and-api-keys
- Instrument discovery : https://builders.etoro.com/blog/developers-guide-to-instrument-discovery
- Premier bot : https://builders.etoro.com/blog/building-your-first-trading-bot
- Démo -> production : https://builders.etoro.com/blog/from-demo-to-production-trading-bot
- Référence API (OpenAPI) : https://api-portal.etoro.com/api-reference et
  https://api-portal.etoro.com/api-reference/openapi.json (index : https://api-portal.etoro.com/llms.txt)
- Guides : https://api-portal.etoro.com/core/guides/market-orders ,
  https://api-portal.etoro.com/core/guides/get-instrument-id
- Fermeture démo : https://api-portal.etoro.com/docs/close-demo-position-by-units
- Ordre marché (unités) : https://api-portal.etoro.com/api-reference/trading--demo/places-a-market-order-to-open-a-position-by-specifying-the-number-of-units-you-would-like-to-trade
- Search : https://api-portal.etoro.com/api-reference/market-data/search-for-instruments
- Rates : https://api-portal.etoro.com/api-reference/market-data/retrieve-current-market-rates-and-pricing-information-for-specified-instruments
- Portfolio réel : https://api-portal.etoro.com/api-reference/trading--real/retrieve-comprehensive-portfolio-information-including-positions-orders-and-account-status
- PnL réel : https://api-portal.etoro.com/api-reference/trading--real/get-real-account-pnl-and-portfolio-details
- Balances : https://api-portal.etoro.com/api-reference/balances
- Agent Portfolios : https://api-portal.etoro.com/api-reference/agent-portfolios/get-agent-portfolios ,
  https://www.etoro.com/news-and-analysis/etoro-updates/agent-portfolios-let-your-ai-agent-trade-for-you/
- Exemple officiel (C#, ancien SDK) : https://github.com/eToro-API/examples

Remarque : `api-portal.etoro.com` est derrière Cloudflare ; plusieurs pages ne se rendent
que dans un navigateur. `https://public-api.etoro.com` (racine) n'affiche qu'un titre.

## URL de base et distinction démo / réel

- **[CONFIRMÉ]** Une seule base : `https://public-api.etoro.com`. Pas d'hôte démo séparé.
  `ETORO_BASE_URL_DEMO` et `ETORO_BASE_URL_REAL` (config.py) ont donc la même valeur par défaut.
- **[CONFIRMÉ]** La distinction se fait par un **segment de chemin** :
  - exécution : `/api/v1/trading/execution/demo/...` (démo) vs `/api/v1/trading/execution/...` (réel)
  - portfolio : `/api/v1/trading/info/demo/portfolio` vs `/api/v1/trading/info/portfolio`
  - PnL : `/api/v1/trading/info/demo/pnl` vs `/api/v1/trading/info/real/pnl`
  - ordres unifiés v2/v3 : `/api/v2/trading/execution/demo/orders` vs `/api/v2/trading/execution/orders`
- **[CONFIRMÉ]** La User Key est émise **par environnement** (Virtual vs Real) : une clé qui
  marche en démo ne marche pas forcément en réel (`ETORO_USER_KEY` doit correspondre à `ETORO_TRADING_MODE`).
- **[CONFIRMÉ]** Le trading réel exige un compte vérifié (KYC) ; sinon 403.

## En-têtes d'authentification

| En-tête | Statut | Rôle |
|---|---|---|
| `x-api-key` | [CONFIRMÉ] | identifie l'application (clé API) |
| `x-user-key` | [CONFIRMÉ] | identifie l'utilisateur agissant ; obligatoire sur les endpoints trading/portfolio |
| `x-request-id` | [CONFIRMÉ] | UUID unique par requête, exigé par le schéma OpenAPI (traçabilité support) |
| `Authorization: Bearer` | [CONFIRMÉ] | alternative OAuth, **mutuellement exclusive** avec `x-api-key`+`x-user-key` |
| `content-type: application/json` | [CONFIRMÉ] | sur les POST |

Codes : 400 validation, 401 clé manquante/invalide ou user key expirée, 403 droits/KYC,
429 rate limit (avec `Retry-After`), 500 serveur. **[CONFIRMÉ]**

Rate limits **[CONFIRMÉ]** : trading (écriture) 20 req/60 s partagés entre les 11 endpoints
d'exécution ; market data 120 req/60 s partagés ; défaut 60 req/60 s. En-têtes
`RateLimit-Limit/Remaining/Reset/Policy`.

## Endpoints utilisés

### Recherche d'instruments — `GET /api/v1/market-data/search` [CONFIRMÉ]

- Query : `fields` (**obligatoire**, liste de colonnes), `internalSymbolFull=AAPL` (match exact),
  `searchText`, `pageSize`, `pageNumber`, `sort`. Tout champ du schéma Instrument peut servir de filtre.
- Réponse : `{ "page", "pageSize", "totalItems", "items": [ { "instrumentId", "internalSymbolFull",
  "displayname", "instrumentTypeID", "exchangeID", "currentRate", "isCurrentlyTradable" } ] }`
- Métadonnées par id : `GET /api/v1/market-data/instruments?instrumentIds=1,2` [CONFIRMÉ] ;
  types : `GET /api/v1/market-data/instrument-types` [CONFIRMÉ].
- **[HYPOTHÈSE]** L'or est référencé `internalSymbolFull = "GOLD"` (pas `XAUUSD`), id historique 14.
  `SYMBOL_ALIASES` dans le service fait `XAUUSD -> GOLD` ; à valider avec un appel search.
- **[HYPOTHÈSE]** Mapping `instrumentTypeID` : 1 forex, 2 commodities, 4 indices, 5 stocks, 6 ETF,
  10 crypto (`INSTRUMENT_TYPE_TO_ASSET_CLASS`).

### Cours temps réel — `GET /api/v1/market-data/instruments/rates?instrumentIds=1,2` [CONFIRMÉ]

Réponse : `{ "rates": [ { "instrumentID", "bid", "ask", "lastExecution", "date" (ISO 8601),
"conversionRateAsk", "conversionRateBid", "unitMargin", "priceRateID", ... } ] }`.
Mappé vers `Quote(instrument_id, bid, ask, timestamp=date)`. Un flux WebSocket existe aussi
(`builders.etoro.com/playground/websocket`), non utilisé ici.

### Positions ouvertes — `GET /api/v1/trading/info/{demo/}portfolio` [CONFIRMÉ]

Réponse : `{ "clientPortfolio": { "credit", "unrealizedPnL"?, "positions": [...], "orders": [...],
"mirrors": [...] } }`. Position : `positionID`, `CID`, `instrumentID`, `isBuy`, `amount`, `leverage`,
`openRate`, `stopLossRate`, `takeProfitRate`, `openDateTime`, `orderID`, `mirrorID`, `units`...
**[HYPOTHÈSE]** : `takeProfitRate: 0` / `stopLossRate: 0.0001` dans l'exemple officiel semblent être
des sentinelles « pas de SL/TP » ; le service traite `<= 0` comme `None`. Le nom du champ de PnL latent
par position (`unrealizedPnL` ?) n'est pas confirmé (repli 0.0).

### Ouverture marché — `POST /api/v1/trading/execution/{demo/}market-open-orders/by-amount` [CONFIRMÉ]

Corps (PascalCase) : `InstrumentID` (int, requis), `IsBuy` (bool, requis), `Leverage` (int, requis),
`Amount` (montant en devise du compte, requis) ; optionnels `StopLossRate`, `TakeProfitRate`,
`IsTslEnabled`, `IsNoStopLoss`, `IsNoTakeProfit`. Variante `/by-units` avec `AmountInUnits`.

- **[CONFIRMÉ]** `StopLossRate` est « le prix de déclenchement du stop-loss auquel la position
  générera un ordre marché de clôture » -> **taux absolu** (prix), pas un montant ni un pourcentage.
  `TakeProfitRate` idem par symétrie. `IsTslEnabled` = trailing stop.
- **[CONFIRMÉ]** Réponse : `{ "orderForOpen": { "instrumentID", "amount", "amountInUnits", "isBuy",
  "leverage", "stopLossRate", "takeProfitRate", "isTslEnabled", "mirrorID", "totalExternalCosts",
  "lotCount", "orderID", "orderType", "statusID", "CID", "openDateTime", "lastUpdate" }, "token": uuid }`.
- **[CONFIRMÉ]** L'exécution est **asynchrone** : la réponse contient un `orderID`, pas de `positionID`.
  Le positionId se découvre ensuite via le portfolio (doc : « use GET /trading/info/portfolio to
  discover positionId and orderId »). Le service prend un instantané des positions avant l'ordre puis
  interroge le portfolio (`position_poll_attempts` x `position_poll_delay_s`) pour trouver la nouvelle
  position sur l'instrument. S'il ne la voit pas, il renvoie une `Position` provisoire
  `position_id="order:<orderID>"` (à resynchroniser par `agent.sync_positions`).
- Le service exige que `orderForOpen.stopLossRate` et `takeProfitRate` soient renvoyés > 0, sinon
  `EtoroApiError` (pas de position sans SL/TP natifs). Un écart > 0,5 % vs demandé est loggé (eToro
  peut ajuster aux bornes autorisées).
- **[HYPOTHÈSE]** Un endpoint GET de statut d'ordre par `orderID` n'a pas été trouvé dans la doc ;
  seuls `DELETE .../market-open-orders/{orderId}` (annulation) et le portfolio existent.
- Endpoint unifié v2 (`POST /api/v2/trading/execution/{demo/}orders`, corps `action`, `transaction`,
  `instrumentId`, `orderType: "mkt"`, `amount`, `orderCurrency`, `leverage`, `stopLossType`...) [CONFIRMÉ
  pour le chemin] mais le format SL/TP v2 (`stopLossType`) n'est pas documenté publiquement -> non utilisé.

### Fermeture — `POST /api/v1/trading/execution/{demo/}market-close-orders/positions/{positionId}` [CONFIRMÉ]

Corps : `{ "InstrumentID": int (requis), "UnitsToDeduct": null }` (null/omis = fermeture totale).
Réponse : `{ "orderForClose": { "positionID", "instrumentID", "unitsToDeduct", "orderID", "orderType",
"statusID", "CID", "openDateTime", "lastUpdate" }, "token": uuid }`. Asynchrone aussi.
**[HYPOTHÈSE]** Le PnL réalisé n'est pas dans la réponse : le service renvoie le PnL latent lu dans le
portfolio juste avant la demande. Le PnL exact est à lire via
`GET /api/v1/trading/info/trade/history?minDate=YYYY-MM-DD` (À VÉRIFIER, champ `netProfit` ?).

### Solde / equity

- `GET /api/v1/trading/info/{demo|real}/pnl` [CONFIRMÉ chemin] -> `clientPortfolio.credit`,
  `unrealizedPnL`, `mirrors`... Pas de champ `equity` explicite vu dans la doc.
- `GET /api/v1/balances` (+ `expand=equityDetails`) [CONFIRMÉ] -> `totalBalance`, `displayCurrency`,
  `balances[] { accountId, accountType (Trading|Cash|Options|Crypto...), balance, currency,
  equityDetails { available, frozenCash, currentPNL, totalUsedMargin } }`. **[HYPOTHÈSE]** ne couvre
  pas le compte virtuel démo.
- **[HYPOTHÈSE]** retenue dans `get_account_balance` : `equity = credit + Σ positions.amount +
  unrealizedPnL` à partir du portfolio (si un champ `equity`/`totalEquity`/`realizedEquity` apparaît,
  il est utilisé directement). À VÉRIFIER sur le compte démo.

## Agent Portfolio / sous-portfolio

- **[CONFIRMÉ]** Le produit « Agent Portfolios » existe : sous-portefeuille dédié dans le compte,
  budget minimum 200 $, **clé API scopée** au sous-portefeuille ; l'agent peut ouvrir/fermer des
  positions, lire les soldes, gérer ce sous-portefeuille uniquement.
- **[CONFIRMÉ]** `GET /api/v1/agent-portfolios` -> `{ "agentPortfolios": [ { "agentPortfolioId" (uuid),
  "agentPortfolioName", "agentPortfolioVirtualBalance" (USD), "mirrorId" (int), "createdAt",
  "userTokens": [ { scopes ex. "etoro-public:trade.real:read|write", expiration, IP whitelist } ] } ] }`.
  `DELETE /api/v1/agent-portfolios/{agentPortfolioId}` et
  `DELETE /api/v1/agent-portfolios/{agentPortfolioId}/user-tokens/{userTokenId}` [CONFIRMÉ].
  Création et génération de token existent (annoncées) mais leur schéma n'a pas été lu [HYPOTHÈSE :
  `POST /api/v1/agent-portfolios` et `POST .../user-tokens`].
- **[HYPOTHÈSE]** Le token délégué généré se passe en `x-user-key` ; les positions du sous-portefeuille
  portent `mirrorID == agentPortfolios[].mirrorId`. Le service filtre les positions par `mirrorID` si
  `ETORO_PORTFOLIO_ID` est numérique (sinon aucun filtre). Il n'existe pas de paramètre `portfolioId`
  sur les endpoints trading : c'est la clé scopée qui porte le périmètre.

## Récapitulatif « À VÉRIFIER » (constantes dans `etoro_service.py`)

1. `SYMBOL_ALIASES` : `XAUUSD -> GOLD`, id 14.
2. `INSTRUMENT_TYPE_TO_ASSET_CLASS` : codes `instrumentTypeID`.
3. `ENDPOINTS["trade_history"]` : PnL réalisé après fermeture (`get_closed_position`, utilisé par
   `agent.sync_positions`) — clés lues : `positionID`, `netProfit`, `closeDateTime` (hypothèse).
4. `ENDPOINTS["balances"]` en démo ; formule d'equity de `get_account_balance`.
5. `ENDPOINTS["agent_portfolios"]` : création/token, filtre `mirrorID`, `ETORO_PORTFOLIO_ID`.
6. Nom du champ de PnL latent par position dans le portfolio.
7. Comportement quand le marché est fermé (ordre en attente : position provisoire `order:<id>`).


## Ids confirmés via le connecteur eToro (2026-09-06)

| Symbole | Id | Ouvrable sur ce compte |
|---|---|---|
| AAPL | 1001 | oui |
| MSFT | 1004 | oui |
| NVDA | 1137 | oui |
| AMZN | 1005 | oui |
| GOOGL | 6434 | oui |
| META | 1003 | oui |
| NEM (Newmont) | 1757 | oui, proxy or |
| AEM (Agnico Eagle) | 6582 | oui, proxy or |
| GOLD (CFD or) | **18** (et non 14) | non, `allowOpenPosition=false` |
| SPY / VOO / IVV / QQQ / GLD / IAU / GDX | 3000 / 4238 / 3138 / 3006 / 3025 / 4365 / 3002 | non (ETF US, compte belge) |

Levier autorisé sur les actions : 1 uniquement. Exposition minimale par position : 10 USD.
