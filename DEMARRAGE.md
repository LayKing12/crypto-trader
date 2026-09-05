# Crypto Trader — Guide de démarrage

Application de trading réel sur Kraken avec alertes WhatsApp et analyse IA.

---

## Structure du projet

```
crypto-trader/
├── backend/     ← Serveur Node.js (API Kraken, WhatsApp, IA)
└── frontend/    ← Interface mobile React (Vite)
```

---

## 1. Prérequis

- **Node.js 18+** : https://nodejs.org/
- **Compte Kraken** : https://www.kraken.com/ (vérification KYC requise)
- **Compte Twilio** (optionnel, pour WhatsApp) : https://www.twilio.com/
- **Clé API Anthropic** (optionnel, pour l'analyse IA) : https://console.anthropic.com/

---

## 2. Configuration

### Clés API Kraken

1. Connectez-vous à Kraken → **Sécurité → API**
2. Créez une nouvelle clé avec ces permissions uniquement :
   - ✅ Query Funds
   - ✅ Query Open Orders & Trades
   - ✅ Create & Modify Orders
   - ❌ **Ne jamais cocher "Withdraw" !**
3. Copiez la clé et le secret

### Fichier .env backend

```bash
cd backend
cp .env.example .env
```

Éditez `.env` avec vos vraies valeurs :

```env
KRAKEN_API_KEY=votre_clé_kraken
KRAKEN_API_SECRET=votre_secret_kraken
TWILIO_ACCOUNT_SID=ACxxxxxxxx        # Optionnel
TWILIO_AUTH_TOKEN=xxxxxxxx           # Optionnel
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
WHATSAPP_PHONE=+32470000000          # Votre numéro
ANTHROPIC_API_KEY=sk-ant-...         # Optionnel
PORT=3001
```

### Configuration WhatsApp (Twilio Sandbox)

1. Créez un compte gratuit sur twilio.com
2. Allez dans **Messaging → Try it out → Send a WhatsApp message**
3. Envoyez le code d'activation depuis votre téléphone au **+1 415 523 8886**
4. Remplissez les variables TWILIO dans `.env`

---

## 3. Installation

### Backend

```bash
cd backend
npm install
```

### Frontend

```bash
cd frontend
npm install
```

---

## 4. Lancement

### Terminal 1 — Backend

```bash
cd backend
npm run dev
# → http://localhost:3001
```

### Terminal 2 — Frontend

```bash
cd frontend
npm run dev
# → http://localhost:5173
```

Ouvrez **http://localhost:5173** dans votre navigateur ou sur votre téléphone (même réseau Wi-Fi).

---

## 5. Accès mobile

Pour accéder depuis votre téléphone sur le même réseau Wi-Fi :

1. Trouvez l'IP de votre PC : `ipconfig` (Windows) → cherchez l'adresse IPv4
2. Dans `frontend/vite.config.js`, ajoutez `host: true` dans `server:`
3. Accédez à `http://192.168.x.x:5173` depuis votre téléphone
4. Sur mobile : **Partager → Ajouter à l'écran d'accueil** pour l'installer comme app

---

## 6. Montants minimaux Kraken

| Crypto | Minimum | Prix approx. |
|--------|---------|--------------|
| BTC    | 0.0001  | ~8€          |
| ETH    | 0.002   | ~4€          |
| SOL    | 0.5     | ~70€         |
| ADA    | 10      | ~4€          |
| DOT    | 1       | ~7€          |

**Conseil débutant** : Commencez avec BTC ou ADA (petits montants possibles).

---

## 7. Sécurité

- Le fichier `.env` ne doit **jamais** être partagé ou mis sur GitHub
- Les clés API restent côté serveur (backend), jamais dans le frontend
- Utilisez des clés API avec permissions minimales
- Activez la 2FA sur votre compte Kraken

---

## 8. Architecture technique

```
Téléphone/PC (browser)
    ↕ WebSocket (prix temps réel)
    ↕ REST (trades, portfolio, IA)
Backend Express (port 3001)
    ↕ HMAC-SHA512 auth
    ↕ WebSocket wss://ws.kraken.com
Kraken API
```

---

## Dépannage

| Problème | Solution |
|----------|----------|
| "API key not configured" | Vérifiez le fichier `.env` |
| Prix affichés mais pas mis à jour | WebSocket Kraken déconnecté, attendre reconnexion |
| WhatsApp non reçu | Vérifiez que vous avez rejoint le sandbox Twilio |
| Ordre refusé | Vérifiez le volume minimum et le solde disponible |

---

## Module eToro — actions + or (Agent Portfolio)

Second moteur, à côté du bot Kraken, dans `python-backend/etoro/`. Il applique les mêmes garde-fous
que le fix d'avril (max 3 positions, 1 par instrument, cooldown 4 h, SL/TP obligatoires, circuit
breaker à −3 % du jour, `market_score >= 70`) sur un univers restreint : AAPL, MSFT, NVDA, AMZN,
GOOGL, SPY et l'or (XAU/USD).

- **Inactif tant que `ETORO_API_KEY` est vide** : CryptoMind démarre comme avant.
- Clés : etoro.com → Settings → Trading → API Key Management, environnement **Demo** d'abord.
- Variables : voir `python-backend/.env.example` et `python-backend/RAILWAY_ENV.md`.
- Onglet **eToro** dans l'app mobile : état de l'agent, positions, décisions en direct, kill switch.
- Endpoints : `GET /etoro/status`, `/etoro/positions`, `/etoro/decisions`, `POST /etoro/kill` (header `X-Kill-Token`).
- Test de lecture seule du compte démo : `cd python-backend && python etoro_smoke_demo.py`.
- Passage en argent réel : `docs/etoro/checklist_demo_vers_reel.md` (étape bloquante : rotation des identifiants).
