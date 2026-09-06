# Tableau de bord Netlify — `cryptomind-etoro`

- Site : https://cryptomind-etoro.netlify.app
- Admin Netlify : https://app.netlify.com/projects/cryptomind-etoro
- Code : `dashboard/` (page statique + 2 fonctions serverless TypeScript)

## Pourquoi Netlify + Railway

Le moteur de trading est en Python et doit tourner en continu (boucle de l'Agent Portfolio, surveillance
des positions, circuit breaker). Netlify n'exécute pas de Python et limite chaque fonction à 26 s : il ne
peut pas héberger le moteur. Il héberge donc **l'interface** :

```
Navigateur ──► Netlify (page + fonctions) ──► Railway (module eToro, API /etoro/*) ──► eToro
                     │
                     └── garde ETORO_KILL_SWITCH_TOKEN côté serveur, jamais dans le navigateur
```

## Ce que montre la page

- Mode DEMO / REAL, connexion au moteur (point vert/rouge)
- Agent actif ou coupé (et par qui), circuit breaker et fin de pause
- PnL du jour (montant et % de l'equity de départ)
- Positions ouvertes lues chez eToro : instrument, sens, montant, prix d'ouverture, SL, TP, PnL latent, durée
- Bouton **Couper l'agent** / **Relancer l'agent**, protégé par mot de passe
- Rafraîchissement automatique toutes les 30 s

## Variables à poser sur Netlify

Netlify › Site configuration › Environment variables (scope Functions suffit) :

| Variable | Valeur |
|---|---|
| `ETORO_API_URL` | URL publique du service Render, ex. `https://cryptomind-etoro.onrender.com` |
| `ETORO_KILL_SWITCH_TOKEN` | exactement la même valeur que sur Render |
| `DASHBOARD_PASSWORD` | mot de passe que vous taperez sur la page pour couper/relancer |

Après ajout ou modification d'une variable, relancer un déploiement (push sur main ou « Trigger deploy ») : les fonctions embarquent l'environnement au moment du build.
Collez ces valeurs vous-même dans Netlify ; l'assistant IA ne doit jamais les recevoir.

## Endpoints Netlify

| Route | Méthode | Rôle |
|---|---|---|
| `/api/status` | GET | agrège `/etoro/health`, `/etoro/status`, `/etoro/positions` du moteur |
| `/api/kill` | POST + header `X-Dashboard-Password` | relaie `POST /etoro/kill` avec le token serveur |
| `/api/resume` | POST + header `X-Dashboard-Password` | relaie `POST /etoro/resume` |

Tant que `ETORO_API_URL` n'est pas posée, `/api/status` renvoie `{"configured": false}` et la page affiche le guide.

## Redéployer après une modification

Depuis `dashboard/` :

```bash
npx -y @netlify/mcp@latest --site-id 2d27eb34-ca55-492f-87db-5f29c9e4ef7d
```

ou, avec la CLI Netlify installée (`npm i -g netlify-cli`, puis `netlify login`) :

```bash
netlify deploy --prod --site 2d27eb34-ca55-492f-87db-5f29c9e4ef7d
```

## Sécurité

- Le token d'arrêt eToro ne quitte jamais Netlify ; le navigateur ne connaît que le mot de passe du tableau de bord.
- Comparaison du mot de passe en temps constant ; 401 si faux, 503 si une variable manque.
- La page est `noindex`, servie avec `Cache-Control: no-store` et `X-Frame-Options: DENY`.
- Pour restreindre encore l'accès, activez « Site protection » (mot de passe visiteur) dans Netlify, plan Pro requis, déjà le cas sur ce compte.


## Maintien en éveil du moteur Render

`netlify/functions/keepalive.mts` est une fonction planifiée (`*/10 * * * *`) qui appelle
`${ETORO_API_URL}/etoro/health` toutes les 10 minutes. Render Free met un service en veille après
15 minutes sans requête ; cet appel l'en empêche. Sans `ETORO_API_URL`, la fonction ne fait rien.
Les fonctions planifiées ne tournent que sur le déploiement publié.
