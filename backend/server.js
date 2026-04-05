require("dotenv").config();
const express = require("express");
const cors = require("cors");
const http = require("http");
const WebSocket = require("ws");
const fetch = require("node-fetch");

const KrakenRest = require("./services/krakenRest");
const krakenWS = require("./services/krakenWS");    // singleton
const WhatsAppService = require("./services/whatsapp");
const AlertMonitor = require("./services/alertMonitor");

const app = express();
app.use(cors());
app.use(express.json());

// ── Service instances ────────────────────────────────────────────────────────
const kraken = new KrakenRest(
  process.env.KRAKEN_API_KEY,
  process.env.KRAKEN_API_SECRET
);
const whatsapp = new WhatsAppService(
  process.env.TWILIO_ACCOUNT_SID,
  process.env.TWILIO_AUTH_TOKEN,
  process.env.TWILIO_WHATSAPP_FROM
);
const alertMonitor = new AlertMonitor(whatsapp);

// ── Public price routes ──────────────────────────────────────────────────────
app.get("/api/prices", (req, res) => {
  const snapshot = krakenWS.getSnapshot();
  if (Object.keys(snapshot).length) return res.json(snapshot);
  // Fallback to REST if WS not warmed up yet
  kraken
    .getTicker(["XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOTUSD"])
    .then((data) => res.json(data))
    .catch((err) => res.status(500).json({ error: err.message }));
});

app.get("/api/ohlc/:pair", async (req, res) => {
  try {
    const interval = parseInt(req.query.interval) || 60;
    const data = await kraken.getOHLC(req.params.pair, interval);
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Private account routes ───────────────────────────────────────────────────
app.get("/api/balance", async (req, res) => {
  try {
    const balance = await kraken.getBalance();
    res.json(balance);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/portfolio", async (req, res) => {
  try {
    const [balance, tradeBalance] = await Promise.all([
      kraken.getBalance(),
      kraken.getTradeBalance(),
    ]);
    const prices = krakenWS.getSnapshot();
    res.json({ balance, tradeBalance, prices });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/trades", async (req, res) => {
  try {
    const history = await kraken.getTradeHistory();
    res.json(history);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/orders", async (req, res) => {
  try {
    const orders = await kraken.getOpenOrders();
    res.json(orders);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Trade execution ──────────────────────────────────────────────────────────
app.post("/api/trade", async (req, res) => {
  try {
    const { pair, type, ordertype, volume, price } = req.body;
    if (!pair || !type || !ordertype || !volume) {
      return res.status(400).json({ error: "pair, type, ordertype, volume requis" });
    }
    if (!["buy", "sell"].includes(type)) {
      return res.status(400).json({ error: "type doit être 'buy' ou 'sell'" });
    }
    const result = await kraken.addOrder({ pair, type, ordertype, volume, price });

    // Alert + WhatsApp
    alertMonitor.addManual({
      type: type === "buy" ? "buy" : "sell",
      pair,
      title: `✅ Ordre ${type.toUpperCase()} exécuté`,
      body: `${volume} ${pair} @ ${price || "marché"}`,
    });
    if (process.env.WHATSAPP_PHONE) {
      whatsapp
        .sendTradeConfirm(process.env.WHATSAPP_PHONE, {
          type,
          pair,
          volume,
          price: price || "marché",
        })
        .catch(console.error);
    }

    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete("/api/orders/:txid", async (req, res) => {
  try {
    const result = await kraken.cancelOrder(req.params.txid);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Alerts ───────────────────────────────────────────────────────────────────
app.get("/api/alerts", (req, res) => {
  res.json(alertMonitor.getAll());
});

// ── CoinGecko proxy (logos + sparklines 7j, cache 5 min) ─────────────────────
let cgCache = { data: null, ts: 0 };
const CG_IDS = [
  "bitcoin", "ethereum", "solana", "cardano", "polkadot",
  "ripple", "chainlink", "uniswap", "litecoin", "bitcoin-cash",
  "stellar", "avalanche-2", "cosmos", "algorand", "matic-network",
  "near", "tron", "the-sandbox", "decentraland", "aave",
].join(",");

app.get("/api/coingecko/markets", async (req, res) => {
  try {
    const now = Date.now();
    if (cgCache.data && now - cgCache.ts < 5 * 60_000) {
      return res.json(cgCache.data);
    }
    const url =
      `https://api.coingecko.com/api/v3/coins/markets?vs_currency=eur` +
      `&ids=${CG_IDS}&order=market_cap_desc&per_page=20&sparkline=true` +
      `&price_change_percentage=24h,7d`;
    const resp = await fetch(url, {
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) throw new Error(`CoinGecko HTTP ${resp.status}`);
    const data = await resp.json();
    cgCache = { data, ts: now };
    res.json(data);
  } catch (err) {
    // Return cached data if available, even if stale
    if (cgCache.data) return res.json(cgCache.data);
    res.status(502).json({ error: err.message });
  }
});

// ── WhatsApp manual send ─────────────────────────────────────────────────────
app.post("/api/whatsapp/send", async (req, res) => {
  try {
    const { phone, message, urgent } = req.body;
    if (!phone || !message) {
      return res.status(400).json({ error: "phone et message requis" });
    }
    const result = await whatsapp.send(phone, message, { urgent });
    res.json({ success: true, result });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── AI Analysis proxy — 4-level analysis (API key stays server-side) ─────────
app.post("/api/ai-analysis", async (req, res) => {
  try {
    const {
      coin, price, change24h, rsi, macd, bollinger, score,
      fib, elliottWave, portfolioEUR, assetExposurePct,
      entryPrice, stopLoss, maxTradeEUR, sparkline,
    } = req.body;

    const apiKey = process.env.ANTHROPIC_API_KEY;
    if (!apiKey) {
      return res.status(503).json({ error: "ANTHROPIC_API_KEY non configurée" });
    }

    const fibStr = fib
      ? `Support 61.8%: €${fib.r618} | Support 38.2%: €${fib.r382} | Cible 127.2%: €${fib.ext127} | Cible 161.8%: €${fib.ext162}`
      : "Non disponible";

    const waveStr = elliottWave
      ? `Vague estimée: ${elliottWave.wave} | Confiance: ${elliottWave.confidence}% | Cible suivante: €${elliottWave.target} | ${elliottWave.description}`
      : "Non disponible";

    const prompt = `Tu es un analyste crypto institutionnel et pédagogue. Analyse cet actif avec rigueur en 4 niveaux de réflexion, puis donne ta décision finale.

═══════════════════════════════════════════
DONNÉES DE MARCHÉ — ${coin}
═══════════════════════════════════════════
Prix actuel: €${price}
Variation 24h: ${change24h >= 0 ? "+" : ""}${change24h}%
Score opportunité: ${score}/100

NIVEAU 1 — INDICATEURS TECHNIQUES:
RSI 14: ${rsi} ${rsi > 70 ? "⚠️ SURACHAT" : rsi < 30 ? "📉 SURVENTE" : "✅ NEUTRE"}
MACD: ${macd} ${macd > 0 ? "(haussier)" : "(baissier)"}
Bollinger: ${bollinger}

NIVEAU 2 — STRUCTURE & PATTERNS:
Fibonacci: ${fibStr}
Elliott Wave: ${waveStr}

NIVEAU 3 — GESTION DU RISQUE:
Portfolio total: €${portfolioEUR || "?"}
Exposition actuelle sur ${coin}: ${assetExposurePct || 0}% du portfolio
Budget max pour ce trade (règle 5%): €${maxTradeEUR || "?"}
Stop-loss suggéré (-7%): €${stopLoss || "?"}
Prix moyen d'achat actuel: €${entryPrice || "non encore en position"}

NIVEAU 4 — DÉCISION:
Basée sur les 3 niveaux précédents, donne une analyse structurée.
═══════════════════════════════════════════

Réponds UNIQUEMENT avec ce format exact (en français, direct et actionnable):

🎯 SIGNAL: [ACHETER / VENDRE / ATTENDRE / RENFORCER / RÉDUIRE]

📊 NIVEAU 1 — Technique:
[2 phrases sur RSI, MACD, Bollinger et ce qu'ils signifient ici]

📈 NIVEAU 2 — Structure:
[2 phrases sur Fibonacci (zones de support/résistance clés) et Elliott Wave (position et cible)]

🛡️ NIVEAU 3 — Risque:
[2 phrases sur la gestion de position: taille recommandée, stop-loss exact, règle 5%]

⚡ NIVEAU 4 — Plan d'action:
[3-4 lignes concrètes]:
• Entrée: [prix ou condition]
• Stop-loss: [prix exact]
• Objectif 1 (+10%): [prix] → vendre 10%
• Objectif 2 (+20%): [prix] → vendre 20%
• Objectif 3 (+35%): [prix] → vendre 20%
• Objectif 4 (+50%): [prix] → vendre 40%
• Taille recommandée: [€ et % du portfolio]

💡 Leçon du marché:
[1 phrase pédagogique — qu'apprendre de cette configuration?]

⚠️ Décision finale: OUI JE TRADE / NON J'ATTENDS — [raison en 1 phrase]`;

    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-6",
        max_tokens: 1200,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    const data = await response.json();
    const text = data.content?.[0]?.text || "Analyse indisponible.";
    res.json({ text });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── HTTP + WebSocket server ──────────────────────────────────────────────────
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

// Let KrakenWS broadcast to frontend clients
krakenWS.attachServer(wss);
krakenWS.connect();

// Start alert monitoring
alertMonitor.start(kraken);

// Daily summary at 20:00
function scheduleDailySummary() {
  const now = new Date();
  const next = new Date();
  next.setHours(20, 0, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  setTimeout(async () => {
    try {
      const prices = krakenWS.getSnapshot();
      const entries = Object.values(prices);
      if (!entries.length || !process.env.WHATSAPP_PHONE) return;
      const top = entries.reduce((a, b) =>
        Math.abs(b.change24h) > Math.abs(a.change24h) ? b : a
      );
      await whatsapp.sendDailySummary(process.env.WHATSAPP_PHONE, {
        portfolioValue: 0, // would need real balance here
        pnlPct: top.change24h,
        topCoin: top.name,
        topChange: top.change24h,
      });
    } catch (e) {
      console.error("[DailySummary]", e.message);
    }
    scheduleDailySummary(); // reschedule for tomorrow
  }, next - now);
}
scheduleDailySummary();

const PORT = process.env.PORT || 3001;
server.listen(PORT, () => {
  console.log(`✅ Crypto Trader backend running on http://localhost:${PORT}`);
  console.log(`   WebSocket: ws://localhost:${PORT}`);
});
