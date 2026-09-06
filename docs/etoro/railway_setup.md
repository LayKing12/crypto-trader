# Déploiement sur Railway

Ce document couvre la mise en place du module eToro sur Railway : service, variables, volume persistant,
healthcheck et création de la clé API eToro.

> **Règle absolue sur les secrets** : l'assistant IA ne doit **jamais** recevoir, lire ni saisir un mot de passe,
> une clé API, un token ou un identifiant de connexion. C'est l'utilisateur qui les crée sur le portail eToro
> et qui les colle **lui-même** dans Railway. Aucune clé ne doit être collée dans un chat, un fichier du dépôt,
> un ticket ou un message.

## 1. Choisir le mode de déploiement

Deux options :

| Option | Quand | Comment |
|---|---|---|
| **A. Module dans le service CryptoMind existant** | CryptoMind monte `etoro.api.router` sur sa propre app FastAPI et lance `PortfolioAgent.run_forever` dans son process | Ajouter le paquet `etoro/` au dépôt CryptoMind, fusionner `requirements.txt`, poser les variables sur le service existant. Pas de nouveau service. |
| **B. Service dédié `cryptomind-etoro`** | On veut isoler eToro (logs, redéploiements, kill switch indépendants) | Nouveau service Railway à partir de ce dépôt ; le `Procfile` lance l'API. Le worker doit alors être lancé par CryptoMind (option A pour le worker) ou par un second service `worker`. |

Pour la démo, l'option **B** est recommandée : un plantage du module eToro n'impacte pas Kraken.

### Créer le service (option B)

1. Railway > **New Project** (ou projet CryptoMind existant) > **New Service** > **GitHub Repo** > choisir le dépôt.
2. Railway détecte Python via `requirements.txt` et utilise le `Procfile` (`web: uvicorn etoro.api:create_app --factory ...`).
3. Settings > **Networking** > *Generate Domain* pour obtenir une URL publique (nécessaire pour le kill switch HTTP).

## 2. Installer la CLI Railway (optionnel)

La CLI Railway **n'est pas installée sur ce PC**. Tout peut se faire depuis le dashboard, mais si tu veux la CLI :

```powershell
npm i -g @railway/cli
railway login          # ouvre le navigateur
railway link           # associer le dossier local au projet/service
```

## 3. Poser les variables d'environnement

Liste complète (nom, défaut, description) dans le [README](../README.md#variables-denvironnement)
et dans [`.env.example`](../.env.example).

### Via le dashboard (recommandé)

Service > onglet **Variables** > **Raw Editor** > coller le contenu de `.env.example` en remplissant les valeurs,
ou ajouter les variables une par une. Railway redéploie automatiquement après enregistrement.

### Via la CLI

```powershell
railway variables set ETORO_TRADING_MODE=demo
railway variables set ETORO_CREDENTIALS_ROTATED=false
railway variables set ETORO_STATE_PATH=/data/etoro_state.json
railway variables set ETORO_UNIVERSE=AAPL,MSFT,NVDA,AMZN,GOOGL,SPY,XAUUSD
# Les secrets (ETORO_API_KEY, ETORO_USER_KEY, ETORO_KILL_SWITCH_TOKEN, TWILIO_*) :
# les saisir dans le dashboard plutôt que dans un terminal dont l'historique est conservé.
```

### Variables minimales pour démarrer en démo

| Variable | Valeur |
|---|---|
| `ETORO_API_KEY` | clé API eToro (collée par l'utilisateur) |
| `ETORO_USER_KEY` | user key eToro (collée par l'utilisateur) |
| `ETORO_PORTFOLIO_ID` | id de l'Agent Portfolio |
| `ETORO_TRADING_MODE` | `demo` |
| `ETORO_CREDENTIALS_ROTATED` | `false` |
| `ETORO_KILL_SWITCH_TOKEN` | chaîne aléatoire longue (ex. générée avec `python -c "import secrets;print(secrets.token_urlsafe(32))"`) |
| `ETORO_STATE_PATH` | `/data/etoro_state.json` |
| `TWILIO_*` | facultatif ; sans elles, pas de SMS |

Laisser les autres variables à leur défaut au début : ce sont les garde-fous validés côté Kraken.

## 4. Volume persistant pour l'état

`StateStore` écrit l'état (positions, cooldowns, PnL jour, breaker, kill switch) dans `ETORO_STATE_PATH`.
Sans volume, ce fichier est perdu à chaque redéploiement : le breaker et le kill switch seraient réinitialisés.

1. Service > clic droit (ou **Settings**) > **Add Volume**.
2. **Mount path** : `/data`.
3. Vérifier que `ETORO_STATE_PATH=/data/etoro_state.json`.

L'écriture est atomique (fichier `.tmp` puis `os.replace`) : un crash en cours d'écriture ne corrompt pas l'état.

## 5. Healthcheck

Service > **Settings** > **Deploy** :

- **Healthcheck Path** : `/etoro/health`
- **Healthcheck Timeout** : 300 s (laisser le défaut si Railway en propose un)
- **Restart Policy** : *On Failure*

Railway ne bascule le trafic sur un nouveau déploiement que si `/etoro/health` répond 200.

Vérification après déploiement :

```powershell
curl https://<service>.up.railway.app/etoro/health
curl https://<service>.up.railway.app/etoro/status
```

`/etoro/status` doit renvoyer `mode: "demo"`, `kill_switch: false`, aucun breaker actif.

## 6. Créer la clé API eToro

À faire **par l'utilisateur uniquement**, dans son navigateur.

1. Aller sur le portail développeurs eToro : <https://builders.etoro.com> (référence API : <https://public-api.etoro.com>).
2. Se connecter avec le compte eToro (identifiants jamais partagés).
3. **Créer une application** (nom : `cryptomind-etoro`, par exemple).
4. Récupérer les deux valeurs générées : **API key** et **User key**. Elles ne sont affichées qu'une fois.
5. **Activer le mode démo** (compte virtuel) pour cette application. Le module envoie ses requêtes à
   `ETORO_BASE_URL_DEMO` tant que `ETORO_TRADING_MODE=demo`.
6. Si le portail permet de restreindre les permissions/scopes, limiter à : lecture des cours, lecture et gestion
   des positions de l'**Agent Portfolio** uniquement. Pas de retrait, pas de gestion du compte.
7. Coller `ETORO_API_KEY` et `ETORO_USER_KEY` **directement dans Railway > Variables**. Nulle part ailleurs.
8. Relever l'identifiant de l'Agent Portfolio et le poser dans `ETORO_PORTFOLIO_ID`.

Les en-têtes utilisés par `EtoroService` (`x-api-key`, `x-user-key`, `x-request-id`) et les endpoints
vérifiés sont documentés dans `docs/etoro_api.md`.

### En cas de fuite d'une clé

1. `POST /etoro/kill` immédiatement.
2. Révoquer la clé sur builders.etoro.com.
3. Créer une nouvelle clé, la poser dans Railway, redéployer.
4. Vérifier `/etoro/status` puis `POST /etoro/resume`.

## 7. Logs et surveillance

- Service > **Deployments** > **View Logs** : décisions `RiskDecision` (reason en snake_case), erreurs API, retries.
- Les SMS Twilio couvrent : ouverture, fermeture, résumé quotidien, breaker déclenché, kill switch.
- Un déclenchement de breaker ou un kill switch doit toujours être compris avant tout `resume`.

## 8. Passage en réel

Ne pas modifier `ETORO_TRADING_MODE` ni `ETORO_CREDENTIALS_ROTATED` ici : suivre intégralement la
[checklist démo → réel](checklist_demo_vers_reel.md), qui contient l'étape bloquante de rotation des identifiants.
