# Hébergement du moteur sur Render (offre Free)

Remplace Railway (essai expiré). Montage 100 % gratuit :

```
Navigateur ──► Netlify (cryptomind-etoro.netlify.app : page + fonctions)
                   │  /api/status, /api/decisions, /api/kill        keepalive toutes les 10 min
                   ▼
              Render Free (cryptomind-etoro.onrender.com : FastAPI + boucle de l'agent)
                   │                                    ▲
                   ├──► eToro Public API (démo)         │ état + décisions
                   └──► Supabase projet `cryptomind` ◄──┘
```

## Limites de l'offre Free à connaître

- 750 h d'instance par mois et par workspace : un seul service permanent, c'est notre cas.
- Mise en veille après 15 minutes sans requête HTTP, réveil en environ une minute. La fonction
  planifiée Netlify `keepalive.mts` appelle `/etoro/health` toutes les 10 minutes pour l'éviter.
- Pas de disque persistant : l'état du bot (breaker, cooldowns, PnL du jour, journal) est dans
  Supabase, les fichiers JSON locaux ne sont qu'un cache. Voir `docs/supabase_setup.md`.
- Pas de carte bancaire demandée.

## Déploiement pas à pas

1. Le code doit être sur GitHub : `docs/github_setup.md`.
2. Sur https://render.com, **Sign up with GitHub** (compte BBarry1080), puis autoriser l'accès au dépôt
   `cryptomind-etoro`.
3. **New › Blueprint**, choisir le dépôt et la branche `main`. Render lit `render.yaml` à la racine :
   service web Python, `uvicorn etoro.api:app`, healthcheck `/etoro/health`, région Frankfurt.
4. Render demande les variables marquées `sync: false`. À saisir vous-même, jamais via l'assistant :

   | Variable | Valeur |
   |---|---|
   | `ETORO_API_KEY` | Public API Key eToro (Settings › Trading › API Key Management, environnement Demo) |
   | `ETORO_USER_KEY` | User Key eToro du même environnement |
   | `ETORO_KILL_SWITCH_TOKEN` | secret long, identique à celui de Netlify |
   | `SUPABASE_URL` | `https://zawdnmxvtsrnshfkvcoc.supabase.co` |
   | `SUPABASE_SERVICE_KEY` | clé `service_role` du projet Supabase `cryptomind` |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID_TRADING` | bot @BotFather et chat_id, voir notifications/README.md |
   | `NEWS_API_KEY` | optionnel, sentiment news |

   Les autres variables (`ETORO_TRADING_MODE=demo`, `ETORO_RUN_AGENT=true`, univers, garde-fous)
   sont déjà dans `render.yaml`.
5. **Apply**. Le premier build prend 2 à 4 minutes. L'URL publique s'affiche en haut du service.
6. Vérifier :

   ```bash
   curl https://cryptomind-etoro.onrender.com/etoro/health
   ```

   Attendu : `"agent_running": true` et `"supabase": "configured"`.
7. Dans Netlify › Site configuration › Environment variables, poser `ETORO_API_URL` avec cette URL.
   Le tableau de bord passe au vert et le keepalive démarre au prochain déploiement Netlify.

## Exploitation

- **Logs** : Render › service › Logs. Chaque cycle écrit une ligne par signal et par décision.
- **Redéploiement** : automatique à chaque push sur `main` (`autoDeploy: true`). Manuel : bouton
  **Manual Deploy › Deploy latest commit**.
- **Arrêt d'urgence** : bouton « Couper l'agent » du tableau de bord, ou variable
  `ETORO_AGENT_ENABLED=false` sur Render (redémarre le service).
- **Cadence** : `ETORO_CYCLE_INTERVAL_S` (300 s par défaut). Quota eToro market-data 120 req/min,
  8 instruments par cycle : large.
