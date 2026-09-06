"""
Laboratoire de recherche momentum — backtest pur, hors ligne par défaut.

Ce paquet est totalement isolé du moteur live :
- aucune clé API n'est lue ni utilisée ;
- aucun ordre n'est envoyé ;
- les modules `app.services.indicator_engine` et `app.services.scoring_engine`
  sont importés en LECTURE SEULE (uniquement pour reproduire le score « baseline »).

Modules :
- data_sources  : chargement d'OHLC journalier (Kraken, CoinGecko, CSV) + cache disque
- momentum_lab  : indicateurs purs (ROC, force relative, SMA, ATR) et score momentum 0..100
- backtest      : moteur long-only journalier + split chronologique, grid search anti-overfitting, régimes
- report        : rapport markdown dans research/results/
"""
