const WebSocket = require("ws");

// 16 most liquid pairs on Kraken (USD base)
const PAIRS = [
  "XBT/USD", "ETH/USD", "SOL/USD", "ADA/USD", "DOT/USD",
  "XRP/USD", "LINK/USD", "LTC/USD", "BCH/USD", "XLM/USD",
  "AVAX/USD", "ATOM/USD", "ALGO/USD", "NEAR/USD", "TRX/USD",
  "UNI/USD",
];

// Canonical display names + colors
const PAIR_META = {
  "XBT/USD":  { symbol: "₿",  name: "BTC",  color: "#f7931a" },
  "ETH/USD":  { symbol: "Ξ",  name: "ETH",  color: "#627eea" },
  "SOL/USD":  { symbol: "◎",  name: "SOL",  color: "#9945ff" },
  "ADA/USD":  { symbol: "₳",  name: "ADA",  color: "#0033ad" },
  "DOT/USD":  { symbol: "●",  name: "DOT",  color: "#e6007a" },
  "XRP/USD":  { symbol: "✕",  name: "XRP",  color: "#00aae4" },
  "LINK/USD": { symbol: "⬡",  name: "LINK", color: "#2a5ada" },
  "LTC/USD":  { symbol: "Ł",  name: "LTC",  color: "#bfbbbb" },
  "BCH/USD":  { symbol: "₿",  name: "BCH",  color: "#8dc351" },
  "XLM/USD":  { symbol: "✷",  name: "XLM",  color: "#7d9bcc" },
  "AVAX/USD": { symbol: "△",  name: "AVAX", color: "#e84142" },
  "ATOM/USD": { symbol: "⚛",  name: "ATOM", color: "#6f7390" },
  "ALGO/USD": { symbol: "◈",  name: "ALGO", color: "#00d190" },
  "NEAR/USD": { symbol: "Ν",  name: "NEAR", color: "#00c08b" },
  "TRX/USD":  { symbol: "T",  name: "TRX",  color: "#ff0013" },
  "UNI/USD":  { symbol: "🦄", name: "UNI",  color: "#ff007a" },
};

class KrakenWS {
  constructor() {
    this.subscribers = new Set(); // frontend WebSocket clients
    this.priceCache = {};         // latest price per pair
    this.krakenWs = null;
    this._reconnectTimer = null;
  }

  // Attach a local WS server so frontend clients can subscribe
  attachServer(wss) {
    wss.on("connection", (ws) => {
      this.subscribers.add(ws);
      // Send current cache immediately on connect
      if (Object.keys(this.priceCache).length) {
        ws.send(JSON.stringify({ type: "snapshot", data: this.priceCache }));
      }
      ws.on("close", () => this.subscribers.delete(ws));
    });
  }

  connect() {
    this.krakenWs = new WebSocket("wss://ws.kraken.com");

    this.krakenWs.on("open", () => {
      console.log("[KrakenWS] Connected");
      clearTimeout(this._reconnectTimer);
      this.krakenWs.send(
        JSON.stringify({
          event: "subscribe",
          pair: PAIRS,
          subscription: { name: "ticker" },
        })
      );
    });

    this.krakenWs.on("message", (raw) => {
      try {
        const msg = JSON.parse(raw);
        // Ticker messages are arrays: [channelID, data, "ticker", "XBT/USD"]
        if (!Array.isArray(msg) || msg[2] !== "ticker") return;

        const pair = msg[3];
        const d = msg[1];
        const price = parseFloat(d.c[0]);  // last trade price
        const ask = parseFloat(d.a[0]);
        const bid = parseFloat(d.b[0]);
        const vol24h = parseFloat(d.v[1]);
        const open = parseFloat(d.o[1]);    // 24h open
        const change24h = open ? ((price - open) / open) * 100 : 0;

        const meta = PAIR_META[pair] || {};
        const tick = {
          pair,
          name: meta.name || pair,
          symbol: meta.symbol || "",
          color: meta.color || "#ffffff",
          price,
          ask,
          bid,
          change24h: parseFloat(change24h.toFixed(2)),
          vol24h: parseFloat((vol24h / 1e6).toFixed(2)), // millions
          ts: Date.now(),
        };

        this.priceCache[pair] = tick;

        const payload = JSON.stringify({ type: "ticker", data: tick });
        this.subscribers.forEach((client) => {
          if (client.readyState === WebSocket.OPEN) client.send(payload);
        });
      } catch {
        // ignore malformed frames
      }
    });

    this.krakenWs.on("close", () => {
      console.log("[KrakenWS] Disconnected — reconnecting in 5s…");
      this._reconnectTimer = setTimeout(() => this.connect(), 5000);
    });

    this.krakenWs.on("error", (err) => {
      console.error("[KrakenWS] Error:", err.message);
    });
  }

  getSnapshot() {
    return this.priceCache;
  }
}

module.exports = new KrakenWS(); // singleton
