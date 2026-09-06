# Sources de news / sentiment (`etoro/news_feed.py`)

Vérification effectuée le 2026-09-05 (WebSearch + WebFetch des pages officielles).

## 1. Conclusion : que expose l'API publique eToro ?

| Fonctionnalité | Exposée à un tiers via l'API publique ? | Preuve |
|---|---|---|
| **Tori** (assistant IA eToro) | **Non.** Tori est un produit in-app (web/mobile). Aucune page de doc, aucun endpoint, aucun outil MCP. | https://www.etoro.com/trading/platforms/tori/ ; index complet de la doc https://api-portal.etoro.com/llms.txt (aucune entrée Tori) |
| **Intégration Grok / xAI** (sentiment temps réel depuis X) | **Non.** Le communiqué du 16/04/2026 décrit une fonctionnalité de Tori, sans aucune mention d'accès développeur. | https://investors.etoro.com/news-releases/news-release-details/etoros-ai-investing-companion-tori-gets-real-time-x-intelligence ; https://www.globenewswire.com/news-release/2026/04/16/3275232/0/en/etoro-s-ai-investing-companion-tori-gets-real-time-x-intelligence-powered-by-grok-4-2.html |
| **Score de sentiment** (valeur numérique par instrument) | **Non.** Aucun endpoint « sentiment » dans la référence API. Le mot apparaît seulement dans le marketing de builders.etoro.com. | https://builders.etoro.com/ (produits : Trading, Market Data, Portfolio, Watchlists, Social & Discovery, Agent Portfolios) ; https://api-portal.etoro.com/llms.txt |
| **News éditoriales** (articles de presse) | **Non.** `GET /api/v1/feeds/news` s'appelle « news feed » mais renvoie un feed social *classé pour l'utilisateur authentifié* (posts, commentaires, likes), pas des articles de presse. | https://api-portal.etoro.com/api-reference/social-feeds/get-news-feed.md |
| **Feed social par instrument** (posts des utilisateurs) | **Oui.** `GET /api/v1/feeds/markets/{marketId}` — texte des posts + horodatage. | https://api-portal.etoro.com/api-reference/social-feeds/get-instrument-feed-posts.md |
| **MCP officiel eToro** | Expose seulement `get-all-routes` et `get-route-spec` (lecture de l'OpenAPI). Pas de news/sentiment. | https://builders.etoro.com/tools/mcp |

**En résumé :** ni Tori, ni Grok, ni un sentiment calculé ne sont accessibles à un tiers. La seule donnée « opinion » exploitable est le
**feed social de l'instrument**, que nous scorons nous-mêmes lexicalement (source 1 ci-dessous). C'est du contenu généré par les
utilisateurs eToro, donc bruité et biaisé (biais haussier des détenteurs) — à considérer comme un signal faible.

### Détails de l'endpoint eToro retenu

- `GET https://public-api.etoro.com/api/v1/feeds/markets/{marketId}?take=20&offset=0`
- En-têtes : `x-api-key`, `x-user-key` (paire de clés) **ou** `Authorization: Bearer` (scope `etoro-public:feed:read`) ; `x-request-id` (UUID) obligatoire.
- Réponse : `discussions[].post.message.text`, `discussions[].post.created` (ISO 8601), `paging`.
- Quota : 60 requêtes / 60 s, partagé entre les 9 endpoints feed.
- `marketId` : la doc parle d'« identifiant unique de l'instrument ». Dans le feed news, `tags[].market.id` est le ticker (ex. `"TSLA"`),
  donc `news_feed.py` envoie le **symbole** (`AAPL`, `XAUUSD`…). Si eToro attend en réalité l'`instrument_id` numérique, l'appel renvoie
  404 → loggé → fallback automatique sur NewsAPI/RSS. À confirmer sur le compte démo lors de l'intégration.
- Cette source n'est tentée que si `ETORO_API_KEY` **et** `ETORO_USER_KEY` sont renseignés (sinon elle est sautée sans erreur).

## 2. Fallbacks

### 2a. NewsAPI (si `NEWS_API_KEY`)

- `GET https://newsapi.org/v2/everything` — en-tête `X-Api-Key`.
- Paramètres utilisés : `q` (requête mappée, voir §3), `language=en`, `sortBy=publishedAt`, `pageSize=20`, `from` = maintenant − 7 j.
- Réponse : `status`, `totalResults`, `articles[].title`, `articles[].publishedAt` (UTC ISO 8601).
- Doc : https://newsapi.org/docs/endpoints/everything
- Limites du plan gratuit « Developer » : 100 requêtes/jour, articles retardés de ~24 h, usage non commercial. Avec un cache de 15 min et
  7 symboles dans l'univers, on reste sous 100 req/jour tant que la boucle ne dépasse pas ~14 rafraîchissements par symbole et par jour.
  Le retard de 24 h annule en pratique le bonus « fraîcheur » sur ce plan.

### 2b. RSS Yahoo Finance (sans clé, dernier recours)

- `GET https://feeds.finance.yahoo.com/rss/2.0/headline?s=<ticker>` — RSS 2.0 valide (vérifié le 2026-09-05 : `title`, `link`, `pubDate`, `description`).
- Parsing via `feedparser` ; `published_parsed` → datetime UTC.
- Aucune clé, aucune limite documentée ; flux non officiel, peut changer sans préavis.

## 3. Mapping symbole → requête / ticker

| Symbole eToro | NewsAPI `q` | Yahoo `s` |
|---|---|---|
| XAUUSD | `gold price OR XAU/USD` | `GC=F` (contrat or COMEX ; flux vérifié non vide le 2026-09-05) |
| SPY | `S&P 500` | `SPY` |
| AAPL / MSFT / NVDA / AMZN / GOOGL | `Apple OR AAPL`, `Microsoft OR MSFT`, … | ticker tel quel |
| autre | symbole tel quel | symbole tel quel |

## 4. Scoring lexical

- Deux listes de mots anglais orientés finance (`POSITIVE_WORDS` : beat, surge, upgrade, record, rally… ; `NEGATIVE_WORDS` : miss,
  plunge, downgrade, lawsuit, recall, cut…).
- Par titre : `(pos − neg) / max(1, pos + neg)` ∈ [−1, 1].
- Moyenne sur les 20 derniers titres ; un titre publié il y a < 24 h pèse **2**, sinon **1** (`score_items`). `score_headlines(titles)`
  est la version pure sans dates (poids uniformes), testable directement.
- Cache mémoire par symbole, TTL 15 min ; seules les valeurs obtenues sont cachées (un échec n'est pas caché, on réessaie à l'appel suivant).
- Aucune exception ne remonte : erreur réseau / JSON / XML → log `warning` + source suivante ; toutes les sources échouent → `None`.
  `agent.py` doit traiter `None` comme « pas d'info » (ne pas bloquer le trade) ; seul un score < `news_min_sentiment` (−0.5) déclenche un skip.

## 5. Champs `config.py`

Aucun champ manquant : `news_api_key`, `etoro_api_key`, `etoro_user_key`, `base_url`, `etoro_timeout_s` suffisent.
Amélioration possible (non bloquante) : un `NEWS_CACHE_TTL_S` configurable.
