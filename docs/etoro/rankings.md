# Rankings eToro -> signal de confirmation (`etoro/rankings.py`)

Recherche effectuée le 2026-09-05 sur la doc publique eToro. Chaque point est marqué
**Confirmé** (lu dans la doc officielle, URL donnée) ou **Hypothèse** (choix d'implémentation à valider
sur le compte demo).

## 1. Sources

| Source | Contenu |
|---|---|
| https://api-portal.etoro.com/llms.txt | Index de toutes les pages de la référence API |
| https://api-portal.etoro.com/api-reference/rankings/get-paginated-investor-rankings.md | Endpoint rankings paginé (paramètres + schéma complet) |
| https://api-portal.etoro.com/api-reference/rankings/get-rankings-by-named-preset.md | Rankings par preset (`top-gainers`, `low-risk`…) |
| https://api-portal.etoro.com/api-reference/rankings/get-ranking-row-for-a-single-investor.md | Ligne de classement d'un seul investisseur |
| https://api-portal.etoro.com/api-reference/users-info/get-user-live-portfolio.md | Portfolio public (positions ouvertes) d'un utilisateur |
| https://api-portal.etoro.com/core/getting-started/authentication.md | En-têtes `x-api-key`, `x-user-key`, `x-request-id` |
| https://builders.etoro.com/learn/authentication-and-api-keys | Idem, côté portail Builders |
| https://builders.etoro.com/ | Mention d'un billet « Rankings API : filter investors, fetch period-scoped performance, cache-friendly leaderboard » |
| https://www.etoro.com/app/openapi.yaml | Ancienne API `/sapi/rankings/rankings/` (PascalCase : `CID`, `UserName`, `Gain`…) — non utilisée mais tolérée par le parseur |
| https://github.com/eToro-API/examples/blob/master/discovery-documentation.html | Ancienne « Discovery API » : liste des champs (`dailyDD`, `weeklyDD`, `maxDailyRiskScore`, `profitableMonthsPct`, `activeWeeksPct`…) et syntaxe `XxxMin`/`XxxMax` |

## 2. Authentification et base URL — **Confirmé**

- Base : `https://public-api.etoro.com` (= `settings.base_url`, identique en demo et real ; seule la clé
  diffère, chaque clé étant liée à un environnement).
- En-têtes obligatoires : `x-api-key` (application), `x-user-key` (utilisateur), `x-request-id` (UUID unique
  par requête). Alternative OAuth `Authorization: Bearer` non utilisée ici.
- Quota : 60 requêtes / 60 s partagées entre les endpoints sans quota dédié. Le portfolio public a son
  propre quota de 60 / 60 s. D'où le cache 1 h et la concurrence limitée à 5.

## 3. Endpoint rankings — **Confirmé**

`GET /api/v2/portfolios/rankings`

| Paramètre | Valeurs | Statut |
|---|---|---|
| `period` (**obligatoire**) | `CurrMonth`, `OneMonthAgo`, `TwoMonthsAgo`, `CurrQuarter`, `ThreeMonthsAgo`, `SixMonthsAgo`, `CurrYear`, `OneYearAgo`, `LastYear`, `LastTwoYears`, `AbsOneYear`, `AbsTwoYears` | Confirmé |
| `sort` | champ camelCase, préfixe `-` pour décroissant (`-gain`, `-copiers`) | Confirmé |
| `page` / `pageSize` | défaut 1 / 20, `pageSize` max 100 | Confirmé |
| `gainMin/Max`, `copiersMin/Max`, `riskScoreMin/Max` (1-10), `popularInvestor`, `aumTier`, `country` | filtres serveur | Confirmé |
| filtres serveur sur `dailyDD`, `profitableMonthsPct`, `activeWeeks` | **absents** de la page v2 (ils existaient dans l'ancienne Discovery API en `XxxMin`/`XxxMax`) | Confirmé absent -> filtrage **côté client** |

Réponse : `{"results": [RankItem…], "pagination": {"page", "pageSize", "totalItems", "hasNext"}}`.

Champs `RankItem` utiles (tous confirmés) : `cid`, `username`, `gain`, `dailyGain`, `riskScore`,
`maxDailyRiskScore`, `maxMonthlyRiskScore`, `dailyDD`, `weeklyDD`, `peakToValley` (+ `peakToValleyStart/End`),
`profitableWeeksPct`, `profitableMonthsPct`, `activeWeeks`, `activeWeeksPct`, `weeksSinceRegistration`,
`firstActivity`, `lastActivity`, `copiers`, `trades`, `winRatio`, `exposure`, `topTradedInstrumentId`,
`type` (`trader` | `smart-portfolio`), `subType` (`pi-champion`, `pi-elite`…), `country`.

Autres endpoints confirmés, non utilisés : `GET /api/v2/portfolios/rankings/presets/{type}` et
`GET /api/v2/portfolios/{username}/rankings?period=…`.

## 4. Portfolio public d'un trader — **Confirmé**

`GET /api/v1/user-info/people/{username}/portfolio/live`

Réponse : `{"realizedCreditPct", "unrealizedCreditPct", "positions": [...], "socialTrades": [...]}`.
Chaque position : `positionId`, `instrumentId`, `openRate`, `openTimestamp`, `isBuy`, `leverage`,
`takeProfitRate`, `stopLossRate`, `investmentPct`, `netProfit`, `trailingStopLoss`.
`socialTrades[]` contient les copies (avec `parentUsername` et leurs propres `positions`).
Un profil privé / inconnu renvoie 404 (documenté pour l'endpoint ranking-row ; supposé identique ici).

## 5. Hypothèses d'implémentation

| Choix | Détail | Statut |
|---|---|---|
| `period = "OneYearAgo"` | supposé = fenêtre glissante de 12 mois. `LastYear` semble être l'année civile précédente, `AbsOneYear` n'est pas expliqué. À vérifier sur le demo ; constante `RANKINGS_PERIOD`. | Hypothèse |
| Unités | `gain`, `dailyDD`, `weeklyDD`, `peakToValley`, `profitableMonthsPct` en pourcents ; les drawdowns sont probablement négatifs -> on prend la valeur absolue. | Hypothèse |
| « Max drawdown » | `peakToValley` (drawdown crête-creux sur la période) si présent, sinon `max(|dailyDD|, |weeklyDD|)`. | Hypothèse |
| « >= 12 mois d'historique » | `now - firstActivity >= 365 j`, sinon `weeksSinceRegistration >= 52`, sinon `activeWeeks >= 52`. | Hypothèse |
| Univers | filtres serveur `popularInvestor=true`, `copiersMin=100`, `riskScoreMax=5`, tri `-copiers`, 5 pages x 100, puis filtres client (DD, historique, mois profitables) et re-tri. Validé le 2026-09-07 après comparaison chiffrée : le tri `-gain` ramenait des comptes inactifs à drawdown nul et 0 copieur. | Validé |
| Exposition | seules les `positions` directes comptent (pas les `socialTrades` copiées : on cherche la conviction propre du trader). | Hypothèse |
| Sens | `get_confirmation(..., side=None)` : par défaut toute exposition compte (contrat). Si `side` est passé, seules les positions dans le même sens (`isBuy`) comptent. | Extension optionnelle |
| Dénominateur | traders dont le portfolio est illisible (404/403/erreur) exclus du dénominateur ; si aucun n'est lisible -> `None`. | Choix |

## 6. Pipeline de `get_confirmation`

1. `get_top_traders` : rankings 12 mois (pages jusqu'à `hasNext=false` ou 5 pages) ->
   `TraderRank.from_api` (tolère camelCase v2 et PascalCase ancien).
2. `filter_traders` : `max_drawdown <= settings.rankings_max_drawdown_pct` (15 %), historique >= 365 j,
   `profitable_months_pct >= settings.rankings_min_profitable_months_pct` (60 %).
3. `rank_traders` : score de régularité puis top `settings.rankings_top_n` (20).
4. Pour chaque trader, `get_trader_positions` (concurrence 5) ; ratio = exposés / lisibles, borné à [0, 1].
5. Toute erreur -> log `WARNING` + `None` (`get_top_traders` renvoie `[]`).

### Score de régularité

```
score = profitable_months_pct - max_drawdown   # le gain n'entre pas dans le score
```

Le gain est volontairement exclu : un compte inactif affiche 100 % de mois profitables, 0 % de drawdown
et un gain fantaisiste, et tout score fondé sur le gain le met en tête. Exemple : 85 % de mois profitables
avec 12 % de DD donne 73 ; 77 % avec 14 % donne 63 — le premier passe devant. Départage par copieurs réels, puis
un gain brut deux fois plus faible. Départage : `profitable_months_pct` puis `gain`.

## 7. Cache

Cache mémoire module-level, TTL 1 h (`CACHE_TTL_S`), deux clés :
- `top:<base_url>:<period>:<top_n>:<max_dd>:<min_pm>` -> liste `TraderRank` ;
- `portfolio:<base_url>:<username>` -> positions.
Ainsi, 20 traders et 7 instruments coûtent ~25 requêtes/heure au lieu de 175. `clear_cache()` force le
rafraîchissement. Coût worst-case d'un cycle froid : 5 pages + 20 portfolios = 25 requêtes < quota 60/min.

## 8. Ce que l'agent doit savoir

- `Signal.rankings_confirmation = await get_confirmation(instrument_id, settings, client)` ;
  `None` = indisponible (ne pas bloquer le trade sur cette seule base), sinon comparer à
  `settings.rankings_min_confirmation` (0.3).
- Aucun champ manquant dans `config.py` pour ce module. Amélioration possible : exposer
  `RANKINGS_PERIOD`, `RANKINGS_MAX_PAGES` et un filtre `popularInvestor` en variables d'environnement.
