# Momentum Lab — laboratoire de recherche (backtest pur)

Laboratoire **hors ligne, zéro impact live** : il ne lit aucune clé API, n'envoie aucun ordre
et n'importe le moteur live (`app.services.indicator_engine`, `app.services.scoring_engine`)
qu'en **lecture seule**, pour reproduire le score « baseline » actuel et le comparer à un score
momentum.

## Question de recherche

Un score momentum (ROC multi-horizons + force relative + alignement des SMA + confirmation ATR)
bat-il le score actuel RSI/EMA/MACD **de façon robuste**, c'est-à-dire out-of-sample **et**
dans chaque régime de marché ? Tant que la réponse n'est pas « ROBUSTE », rien ne doit passer
en live.

## Structure

| Fichier | Rôle |
|---|---|
| `data_sources.py` | OHLC journalier : Kraken public (~720 bougies max), CoinGecko public (`days=max`), CSV figé ; cache JSON dans `data_cache/` (gitignoré) ; `synthetic_rows` pour les tests |
| `momentum_lab.py` | Indicateurs purs (`roc`, `relative_strength`, `sma`, `sma_alignment`, `atr`), `momentum_score` 0..100, `baseline_score` (moteur live en lecture seule), `run_lab` et le CLI |
| `backtest.py` | Moteur long-only journalier, `split_chronological`, `grid_search` anti-overfitting, `regime_slices`, comparatif et verdict |
| `report.py` | Rapport markdown `results/<date>_<actif>.md` |
| `results/` | Rapports générés (voir `results/README.md`) |
| `../tests/test_momentum_lab.py` | Tests synthétiques, déterministes, sans réseau |

## Utilisation

Depuis `python-backend/` :

```bash
# Kraken (réseau, API publique, pas de clé) — BTC/USD, ~720 jours
python -m research.momentum_lab --pair XBTUSD --source kraken

# Un altcoin avec BTC en benchmark pour la force relative
python -m research.momentum_lab --pair ETHUSD --source kraken --benchmark XBTUSD

# CoinGecko (profondeur maximale, prix journaliers reconstruits en OHLC approximatif)
python -m research.momentum_lab --pair ethereum --source coingecko --benchmark bitcoin --days 0

# Hors ligne, données figées (CSV : date,open,high,low,close[,volume])
python -m research.momentum_lab --csv data/btc.csv --asset BTC --benchmark-csv data/spx.csv
```

Options utiles : `--fee 0.26` (frais par ordre en %), `--in-sample-ratio 0.6`,
`--metric sharpe|total_return_pct|cagr_pct`, `--out-dir`, `--no-cache`, `--no-baseline`.

Tests :

```bash
python -m pytest tests/test_momentum_lab.py -q
```

## Score momentum (0..100)

| Composante | Calcul | Poids |
|---|---|---|
| ROC | ROC 7 / 14 / 30 j, chacun mappé linéairement sur 0..100 (±10 / 15 / 25 % = saturation) | 0,40 |
| Force relative | ratio de performance 30 j actif / benchmark (BTC pour la crypto, indice pour eToro) ; sans benchmark le poids est redistribué | 0,20 |
| Alignement SMA | fraction des relations prix > SMA20 > SMA50 > SMA100 > SMA200 vérifiées | 0,25 |
| Confirmation ATR | ATR14 / prix : 100 si ≤ 4 %, 0 si ≥ 10 %, linéaire entre (pénalise la volatilité excessive) | 0,15 |

La formule ATR est **recopiée** de `app/utils/math_utils.py::calc_atr` (pas importée) pour
rester cohérente sans coupler la recherche au moteur live ; un test vérifie l'égalité.

## Baseline

`baseline_score` rejoue `compute_indicators` + `compute_scores` barre par barre sur une
fenêtre glissante de 300 bougies (aucun look-ahead). Les composantes externes (whales,
sentiment, OI, funding, fear & greed) sont neutres à 50 : le `market_score` est donc borné
≈ 40..72, d'où la config baseline `entrée ≥ 60 / sortie < 50 / stop 3×ATR`.

## Moteur de backtest

- long-only, journalier, une position par actif, pas de levier, capital 100 % engagé ;
- signal lu à la clôture du jour i, exécuté à l'ouverture du jour i+1 ;
- entrée si score ≥ seuil d'entrée, sortie si score < seuil de sortie ou stop k×ATR (fixe,
  vérifié sur le plus bas du jour) ;
- frais par ordre configurables (0,26 % Kraken taker par défaut) ;
- métriques : rendement total, CAGR, drawdown max, Sharpe journalier annualisé (sans risque
  = 0, 365 périodes/an ; `periods_per_year=252` pour des actions), trades, win rate, exposition.

Les scores sont calculés une seule fois sur la série complète puis rejoués sur des fenêtres
d'indices : l'out-of-sample et les régimes ne perdent pas leurs 200 jours de chauffe.

## Anti-overfitting (non négociable)

1. **Split chronologique** (`split_chronological`, 60 / 40 par défaut), jamais aléatoire.
2. **Grid search** sur une grille volontairement petite (seuils d'entrée / sortie, k ATR).
   La candidate = meilleure config in-sample. Elle n'est retenue **que si** elle bat le
   baseline in-sample **et** out-of-sample ; sinon la config retenue est le baseline.
   On ne sélectionne jamais sur l'out-of-sample seul.
3. **Régimes** (`regime_slices`) : tranches contiguës haussières / baissières selon la pente
   de la SMA200 (au moins 2 tranches, fusion des tranches < 30 jours).
4. **Verdict** : `ROBUSTE` uniquement si le momentum bat strictement le baseline (métrique
   choisie) sur l'out-of-sample **et** sur chaque régime. Une égalité (par ex. les deux
   stratégies restent à plat dans un marché baissier) compte comme un échec : c'est voulu.

## Limites connues

- CoinGecko ne fournit qu'un prix par jour : OHLC reconstruit (open = clôture de la veille,
  high/low = max/min(open, close)), donc ATR sous-estimé. Préférer Kraken ou un CSV OHLC.
- Kraken plafonne à ~720 bougies journalières par appel (≈ 2 ans) ; peu de cycles complets.
- Pas de slippage, de spread variable ni de financement ; frais fixes par ordre.
- Une seule série par actif, un seul jeu de poids pour le score momentum : ce laboratoire
  teste des seuils, pas les poids (les figer limite la sur-optimisation, mais c'est un choix).
- Importer le moteur live charge `app.config` (lecture du `.env` par pydantic-settings comme
  effet de bord) ; le laboratoire n'utilise jamais ces valeurs. `--no-baseline` évite l'import.
