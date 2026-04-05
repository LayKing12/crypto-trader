# Variables d'environnement — Railway.app

Configure ces variables dans Railway > ton projet > Variables.

## Obligatoires au démarrage

| Variable | Exemple | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` | URL Supabase PostgreSQL (mode Transaction Pooler) |
| `REDIS_URL` | `redis://default:pass@host:port` | Railway Redis add-on ou Upstash gratuit |
| `PAPER_TRADING` | `true` | Garder `true` jusqu'à 60% win rate sur 100 trades |
| `TOTAL_CAPITAL_USD` | `1000.0` | Capital simulé en paper trading |

## Anthropic Claude (IA stratégique)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Clé API Anthropic (console.anthropic.com) |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` (défaut) |

## Kraken (exécution live — ne remplir qu'après 100 paper trades)

| Variable | Description |
|----------|-------------|
| `KRAKEN_API_KEY` | Clé API Kraken Pro |
| `KRAKEN_SECRET_KEY` | Secret Kraken Pro |

## Twilio WhatsApp (alertes)

| Variable | Exemple | Description |
|----------|---------|-------------|
| `TWILIO_ACCOUNT_SID` | `ACxxxxx` | Account SID Twilio |
| `TWILIO_AUTH_TOKEN` | `xxxxxx` | Auth Token Twilio |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` | Numéro sandbox Twilio |
| `WHATSAPP_RECIPIENT` | `whatsapp:+33612345678` | Ton numéro WhatsApp |

## Optionnelles

| Variable | Défaut | Description |
|----------|--------|-------------|
| `ETHERSCAN_API_KEY` | *(vide)* | Etherscan gratuit pour whale score ETH on-chain |
| `COINGECKO_API_KEY` | *(vide)* | CoinGecko (plan gratuit sans clé suffit) |
| `LOG_LEVEL` | `INFO` | Niveau de log (`DEBUG`, `INFO`, `WARNING`) |
| `APP_ENV` | `production` | Mettre `production` sur Railway |
| `MAX_POSITION_SIZE_PCT` | `5.0` | Max 5% du capital par trade (ne pas modifier) |
| `STOP_LOSS_PCT` | `7.0` | Stop-loss -7% (ne pas modifier) |
| `DRAWDOWN_DISABLE_PCT` | `12.0` | Désactivation si drawdown > 12% |

## Procédure de déploiement Railway (étape par étape)

```bash
# 1. Installer Railway CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. Depuis le dossier python-backend/
cd python-backend
railway init          # Crée le projet Railway
railway add redis     # Ajoute Redis (gratuit jusqu'à 25 MB)

# 4. Configurer les variables
railway variables set DATABASE_URL="postgresql+asyncpg://..."
railway variables set PAPER_TRADING="true"
# ... (répéter pour chaque variable)

# 5. Déployer
railway up

# 6. Vérifier
railway logs          # Voir les logs en temps réel
```

## URL Supabase — format correct

Dans Supabase > Settings > Database > Connection string > **Transaction Pooler** :
```
postgresql+asyncpg://postgres.[ref]:[password]@aws-0-eu-west-3.pooler.supabase.com:6543/postgres
```
**Important** : utilise le **Transaction Pooler** (port 6543), pas le Direct Connection (port 5432)
car Railway est serverless et les connexions directes s'épuisent rapidement.

## Redis gratuit (Upstash)

Si tu veux éviter l'add-on payant Railway Redis :
1. upstash.com → créer un database Redis gratuit (10k commandes/jour)
2. Copier l'URL Redis → variable `REDIS_URL`

## Vérification post-déploiement

```bash
curl https://ton-projet.railway.app/health
# Doit retourner : {"status":"ok","paper_trading":true,...}

curl https://ton-projet.railway.app/api/onchain
# Doit retourner le whale score BTC/ETH
```
