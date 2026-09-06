# Rapports du Momentum Lab

Chaque exécution de `python -m research.momentum_lab` écrit ici un fichier
`<date>_<actif>.md` (par ex. `2026-09-06_XBTUSD.md`) contenant :

- le **verdict** explicite `ROBUSTE` / `NON ROBUSTE` et le détail des vérifications
  (out-of-sample + chaque régime) ;
- la **config retenue** par le grid search (config momentum, ou baseline si aucune
  candidate ne bat le baseline out-of-sample) ;
- le tableau rendement / CAGR / drawdown max / Sharpe / trades / win rate / exposition
  pour **momentum, baseline et buy & hold** sur les mêmes fenêtres : in-sample,
  out-of-sample, et chaque régime haussier / baissier ;
- le top 10 de la grille (in-sample vs out-of-sample) et les limites du backtest.

Lecture rapide : un momentum qui gagne en in-sample mais perd en out-of-sample est
sur-optimisé ; seul un verdict `ROBUSTE` justifie d'envisager une suite (paper trading),
jamais un passage direct en live.

Les rapports sont des artefacts de recherche : ils peuvent être committés pour tracer les
décisions, ou supprimés. Le cache de données (`../data_cache/`) est gitignoré.
