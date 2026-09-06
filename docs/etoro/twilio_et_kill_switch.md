# Twilio et kill switch — procédures opérationnelles

## 1. Régénérer le token Twilio et le mettre à jour sur Railway

À faire si le token a fuité, a été partagé, ou périodiquement (rotation).

### Option A — nouveau Auth Token (rotation sans coupure)

1. Console Twilio : **Account** (menu en haut à droite) → **API keys & tokens**.
2. Section **Auth tokens** → bouton **Request a secondary token**. Twilio crée un second token ; l'ancien reste valide.
3. Copier le token secondaire, le poser sur Railway (voir plus bas), attendre le redéploiement et vérifier qu'un SMS part
   (`/etoro/kill` puis `/etoro/resume` envoient chacun un SMS prioritaire).
4. Revenir dans la console → **Promote to primary** sur le token secondaire. L'ancien token est révoqué à ce moment.

### Option B — API Key restreinte (recommandé pour un bot)

1. **Account → API keys & tokens → Create API key**, type **Standard** (pas *Main*, qui donne les droits de gestion du compte).
2. Noter le **SID** (commence par `SK…`) et le **Secret** : le secret n'est affiché qu'une seule fois.
3. Sur Railway :
   - `TWILIO_ACCOUNT_SID` = `SK…` (le SID de la clé, pas le SID du compte `AC…`)
   - `TWILIO_AUTH_TOKEN` = le secret de la clé

   `twilio.rest.Client(sid, token)` accepte indifféremment (Account SID + Auth Token) ou (API Key SID + Secret).
4. Une clé compromise se supprime individuellement dans la console sans toucher au reste du compte.

### Mise à jour sur Railway

Railway → projet CryptoMind → service → onglet **Variables** → éditer `TWILIO_AUTH_TOKEN` (et `TWILIO_ACCOUNT_SID` si option B)
→ **Deploy**. Railway redéploie le service à chaque modification de variable ; le nouveau token est lu au démarrage
(`get_settings()` est mis en cache par process).

Variables nécessaires pour que `settings.twilio_configured` soit vrai : `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM` (numéro Twilio, format E.164 `+1…`), `TWILIO_TO` (numéro du téléphone qui reçoit, `+33…`).
S'il en manque une, le notifier est en no-op : il logge et n'envoie rien, sans erreur.

Anti-spam intégré : au plus 30 SMS par heure glissante (`MAX_SMS_PER_HOUR` dans `etoro/notifier.py`) ; les messages
breaker et kill switch passent toujours.

## 2. Les trois façons de couper l'agent

### 2.1 Variable `ETORO_AGENT_ENABLED=false` (Railway)

Railway → Variables → `ETORO_AGENT_ENABLED` = `false` → Deploy. `RiskGuard.kill_switch_active()` devient vrai : toutes
les ouvertures sont refusées avec `reason="kill_switch"`.

Attention : Railway **redéploie le service à chaque changement de variable**. Le module ne relit pas l'environnement
en cours de route (settings mis en cache), donc c'est de toute façon le redémarrage qui applique la valeur. Un redéploiement
signifie : arrêt du process, perte des SMS en cours d'envoi, cycle de démarrage complet. C'est fiable mais lent
(30 s à 2 min). **Pour une coupure d'urgence, préférer l'endpoint ci-dessous.** Cette variable est prioritaire :
`POST /etoro/resume` ne relance pas l'agent tant qu'elle vaut `false`.

### 2.2 Endpoint HTTP (immédiat, sans redéploiement)

```bash
curl -X POST https://<app>.up.railway.app/etoro/kill \
     -H "X-Kill-Token: <ETORO_KILL_SWITCH_TOKEN>" \
     -H "X-Kill-Actor: aliou"        # optionnel, apparaît dans le SMS et /status
```

Réponse `{"kill_switch": true, "actor": "aliou", "mode": "demo"}`. L'état est persisté dans le state store
(`store.set_kill_switch`) et survit à un redéploiement. Un SMS de confirmation part si Twilio est configuré.

Codes d'erreur :
- `401` : header `X-Kill-Token` absent ou différent de `ETORO_KILL_SWITCH_TOKEN` (comparaison en temps constant).
- `503` : `ETORO_KILL_SWITCH_TOKEN` non configuré sur Railway, ou store non câblé.

Pour relancer : même commande sur `/etoro/resume`. Pour vérifier : `GET /etoro/status` (sans token) renvoie
`kill_switch`, `kill_switch_active`, `breaker_active`, `breaker_until`, `mode` et le snapshot du store.

Le token : une chaîne aléatoire longue, par ex. `python -c "import secrets; print(secrets.token_urlsafe(32))"`,
posée dans `ETORO_KILL_SWITCH_TOKEN` sur Railway. Ne jamais la mettre dans le code ni dans un lien partagé.

### 2.3 Raccourci téléphone (iOS / Android)

**iOS — app Raccourcis** : nouveau raccourci → action **Obtenir le contenu de l'URL** :
- URL : `https://<app>.up.railway.app/etoro/kill`
- Méthode : `POST`
- En-têtes : `X-Kill-Token` = `<token>`, `X-Kill-Actor` = `iphone`
- Corps : aucun

Ajouter ensuite **Afficher le résultat** pour voir la réponse JSON. Nommer le raccourci « STOP eToro », l'ajouter à
l'écran d'accueil ou le déclencher via Siri (« Dis Siri, STOP eToro »). Dupliquer avec `/etoro/resume` pour « REPRISE eToro ».

**Android — HTTP Shortcuts** (application libre, F-Droid / Play Store) : nouveau raccourci → méthode `POST`, même URL,
mêmes en-têtes → widget sur l'écran d'accueil. Alternative : Tasker / MacroDroid avec une action « HTTP Request ».

Le token est stocké dans le raccourci : protéger le téléphone par code et ne pas partager le raccourci tel quel.

## 3. Ce que fait exactement le kill switch

- **Bloque toute nouvelle ouverture** : `RiskGuard.check_open` refuse immédiatement (`reason="kill_switch"`), avant toute
  autre règle. L'agent continue de tourner, de lire les cours et de synchroniser les positions ; il n'ouvre juste plus rien.
- **Ne ferme PAS les positions existantes.** Elles restent ouvertes chez eToro avec leurs **stop loss et take profit natifs**,
  qui restent actifs côté broker même si le bot est arrêté ou hors ligne. Les fermetures par SL/TP continuent d'être
  détectées par `sync_positions` et notifiées par SMS.
- Pour fermer manuellement une position : application ou site eToro (Portfolio → position → Fermer). Le bot n'a pas
  d'endpoint de fermeture volontaire, par choix : une coupure d'urgence ne doit pas déclencher de ventes en panique.
- Le breaker journalier (perte du jour > `ETORO_DAILY_LOSS_LIMIT_PCT`) est indépendant : il se réarme seul après
  `ETORO_BREAKER_PAUSE_HOURS`, alors que le kill switch reste actif jusqu'à un `resume` explicite (ou tant que
  `ETORO_AGENT_ENABLED=false`).
- En mode `real`, le trading est de toute façon bloqué tant que `ETORO_CREDENTIALS_ROTATED=true` n'est pas posé
  (`real_mode_locked`), indépendamment du kill switch.
