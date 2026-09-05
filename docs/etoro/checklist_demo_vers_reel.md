# Checklist : passage de la démo au réel (1000 €)

À imprimer ou copier dans un ticket et cocher **dans l'ordre**. Tant qu'une case de la partie A ou B n'est pas
cochée, `ETORO_TRADING_MODE` reste `demo` et `ETORO_CREDENTIALS_ROTATED` reste `false`. Le code refuse de toute façon
tout ordre en mode `real` sans rotation (`reason = real_mode_without_rotation`).

Rappel : le module trade uniquement sur l'**Agent Portfolio** eToro, jamais sur le compte principal.
Le réel démarre avec **1000 € maximum** sur ce portfolio.

---

## A. Critères de sortie de démo (tous obligatoires)

Remplir les valeurs observées. Source : `GET /etoro/status`, SMS de résumé quotidien, historique eToro du portfolio démo.

- [ ] **Durée en démo** : au moins égale au temps qu'il a fallu pour stabiliser le win rate côté Kraken.
      Durée Kraken de référence : `____ semaines` — Durée démo eToro effectuée : `____ semaines`
- [ ] **Nombre de trades minimum** atteint (un échantillon trop petit ne prouve rien).
      Minimum fixé : `____ trades` (suggestion : 50) — Observé : `____`
- [ ] **Win rate** ≥ objectif, sur l'ensemble de la période démo.
      Objectif : `____ %` (suggestion : au moins le win rate Kraken stabilisé) — Observé : `____ %`
- [ ] **Drawdown max observé** ≤ limite acceptée.
      Limite : `____ %` (suggestion : < 10 % de l'equity de départ) — Observé : `____ %`
- [ ] **Zéro déclenchement du circuit breaker** (`breaker_active`) sur les **2 dernières semaines**.
      Dernier déclenchement : `____-__-__` (ou « jamais »)
- [ ] **Aucune journée anormale** : pas de pic de trades (le max théorique est `ETORO_MAX_OPEN_POSITIONS` positions
      par fenêtre de cooldown). Max trades/jour observé : `____`
- [ ] **PnL démo positif** sur la période, net de spread.
      PnL : `____ € / ____ %`
- [ ] **Univers inchangé** pendant la période (AAPL, MSFT, NVDA, AMZN, GOOGL, SPY, XAUUSD). Tout élargissement
      remet le compteur de durée à zéro.

## B. Tests techniques réussis (à refaire la semaine du passage)

- [ ] **Kill switch HTTP** : `POST /etoro/kill` avec `X-Kill-Token` -> `/etoro/status` montre `kill_switch: true`,
      SMS reçu, aucune ouverture pendant le test, `POST /etoro/resume` rétablit.
- [ ] **Kill switch config** : `ETORO_AGENT_ENABLED=false` + redéploiement -> aucune ouverture ; remis à `true`.
- [ ] **Token invalide refusé** : `POST /etoro/kill` sans/mauvais token -> 401/403.
- [ ] **Persistance** : après un redéploiement, `/etoro/status` conserve positions ouvertes, cooldowns et état
      du kill switch (volume `/data` fonctionnel).
- [ ] **Notifications** : les 5 SMS reçus au moins une fois (ouverture, fermeture, résumé quotidien, breaker, kill switch).
- [ ] **Healthcheck** `/etoro/health` vert sur Railway, aucun redémarrage inexpliqué sur les 2 dernières semaines.
- [ ] **Tests** : `python -m pytest` vert sur la version déployée.
- [ ] **SL/TP visibles côté eToro** sur chaque position ouverte en démo (jamais de position nue).

## C. ÉTAPE BLOQUANTE : rotation des identifiants

Aucune case de la section D ne peut être cochée avant celles-ci. À faire **par l'utilisateur seul**, dans son
navigateur ; aucune clé ne passe par un assistant IA, un chat ou un fichier du dépôt.

- [ ] **Révoquer l'accès aux identifiants de connexion complets** utilisés pendant la démo : supprimer sur
      builders.etoro.com l'application/clé créée pour la démo, et changer le mot de passe eToro si ces identifiants
      ont été utilisés ailleurs que sur le site eToro.
- [ ] **Créer une nouvelle clé API scopée uniquement à l'Agent Portfolio** : nouvelle application sur le portail,
      permissions limitées à lecture des cours + gestion des positions de ce seul portfolio. Pas de retrait,
      pas d'accès au compte principal, pas de permissions de gestion de compte.
- [ ] **Poser la clé sur Railway** (dashboard > Variables) : `ETORO_API_KEY`, `ETORO_USER_KEY`. Nulle part ailleurs.
- [ ] **Poser `ETORO_PORTFOLIO_ID`** = identifiant de l'Agent Portfolio réel (vérifier que ce n'est pas le compte principal).
- [ ] **Vérifier le solde du portfolio réel** = 1000 € maximum.
- [ ] **Poser `ETORO_CREDENTIALS_ROTATED=true`**.
- [ ] **Poser `ETORO_TRADING_MODE=real`**.
- [ ] Vérifier `ETORO_BASE_URL_REAL` et, si eToro utilise une URL distincte pour le réel, la mettre à jour
      (voir `docs/etoro_api.md`).
- [ ] Redéployer et vérifier `/etoro/status` : `mode: "real"`, `kill_switch: false`, pas de breaker.

## D. Mise en réel progressive

Commencer petit, augmenter par paliers seulement si le palier précédent est propre.

| Palier | `ETORO_POSITION_SIZE_PCT` | `ETORO_MAX_OPEN_POSITIONS` | Durée minimale | Condition de passage |
|---|---|---|---|---|
| 1 | `2.5` | `1` | 2 semaines | 0 breaker, PnL ≥ 0, aucune anomalie |
| 2 | `5` | `2` | 2 semaines | idem |
| 3 | `10` (défaut) | `3` (défaut) | — | régime de croisière |

- [ ] Palier 1 posé (`ETORO_POSITION_SIZE_PCT=2.5`, `ETORO_MAX_OPEN_POSITIONS=1`), `ETORO_MAX_LEVERAGE=1`.
- [ ] Premier trade réel vérifié manuellement sur eToro : instrument dans l'univers, SL et TP présents, montant
      cohérent (≈ 2,5 % de 1000 € = 25 €).
- [ ] Palier 1 validé (date : `____-__-__`).
- [ ] Palier 2 posé et validé (date : `____-__-__`).
- [ ] Palier 3 posé (défauts).
- [ ] Élargissement de l'univers : **interdit** avant plusieurs semaines stables au palier 3, puis un instrument à la fois.

Le levier reste à `1` en réel jusqu'à nouvel ordre.

## E. Plan de retour arrière

Déclencheurs (un seul suffit) : breaker déclenché, drawdown > limite de la section A, comportement inattendu
(ordre hors univers, position sans SL/TP, plus de trades que prévu), erreur API répétée, doute.

1. [ ] `POST /etoro/kill` (immédiat, persisté, SMS).
2. [ ] Vérifier sur eToro les positions ouvertes ; fermer manuellement celles qui posent problème
       (le kill switch bloque les ouvertures, il ne ferme pas les positions).
3. [ ] Si le problème vient des identifiants ou d'un accès suspect : révoquer la clé API sur builders.etoro.com.
4. [ ] Repasser `ETORO_TRADING_MODE=demo` sur Railway (le verrou `ETORO_CREDENTIALS_ROTATED` peut rester `true`,
       il ne s'applique qu'au mode `real`).
5. [ ] Redéployer, vérifier `/etoro/status` (`mode: "demo"`).
6. [ ] Analyser les logs Railway (`reason` des `RiskDecision`, erreurs `EtoroApiError`) et l'historique eToro.
7. [ ] Corriger, tester (`python -m pytest`), puis **reprendre la checklist à la section A** avec une nouvelle
       période de démo proportionnée à la gravité de l'incident.
8. [ ] `POST /etoro/resume` seulement une fois la cause comprise et corrigée.

---

Date de validation finale : `____-__-__` — Signature : `__________`
