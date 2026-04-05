const KrakenRest = require("./krakenRest");

// Monitor the most liquid pairs for alerts
const PAIRS_REST = [
  "XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOTUSD",
  "XRPUSD", "LINKUSD", "LTCUSD", "AVAXUSD", "NEARUSD",
];

// Simple RSI from close prices array
function calcRSI(closes, period = 14) {
  if (closes.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) gains += diff;
    else losses -= diff;
  }
  const rs = gains / (losses || 0.0001);
  return 100 - 100 / (1 + rs);
}

class AlertMonitor {
  constructor(whatsappService) {
    this.wa = whatsappService;
    this.alerts = [];
    this.phone = process.env.WHATSAPP_PHONE;
    this.lastPrices = {};
    this.lastAlertTimes = {}; // debounce: prevent alert spam
    this.DEBOUNCE_MS = 15 * 60 * 1000; // 15 min between same alert
    this._interval = null;
  }

  start(krakenRest) {
    this.kraken = krakenRest;
    // Check every 60 seconds
    this._interval = setInterval(() => this._check(), 60_000);
    console.log("[AlertMonitor] Started");
  }

  stop() {
    clearInterval(this._interval);
  }

  async _check() {
    try {
      const ticker = await this.kraken.getTicker(PAIRS_REST);
      for (const [pairKey, data] of Object.entries(ticker)) {
        const price = parseFloat(data.c[0]);
        const open = parseFloat(data.o);
        const change24h = open ? ((price - open) / open) * 100 : 0;

        // RSI approximation from 24h change (same formula as frontend)
        const rsi = this._rsiFromChange(change24h);

        // Price drop > 5% in last poll cycle (vs last known price)
        const last = this.lastPrices[pairKey];
        if (last) {
          const drop = ((price - last) / last) * 100;
          if (drop <= -5) {
            this._fire({
              type: "warn",
              pair: pairKey,
              title: `🔴 Chute rapide ${pairKey}`,
              body: `Prix: $${price.toLocaleString("fr")} (${drop.toFixed(2)}%)`,
              urgent: true,
            });
          }
        }
        this.lastPrices[pairKey] = price;

        // RSI alerts
        if (rsi > 75) {
          this._fire({
            type: "warn",
            pair: pairKey,
            title: `⚠️ Surachat ${pairKey}`,
            body: `RSI: ${rsi.toFixed(0)} — Envisager de réduire la position.`,
          });
        } else if (rsi < 30) {
          this._fire({
            type: "buy",
            pair: pairKey,
            title: `📉 Survente ${pairKey}`,
            body: `RSI: ${rsi.toFixed(0)} — Opportunité d'accumulation potentielle.`,
          });
        }
      }
    } catch (err) {
      console.error("[AlertMonitor] Check error:", err.message);
    }
  }

  _rsiFromChange(change) {
    if (change > 3) return Math.min(78 + change * 2, 88);
    if (change > 0) return 50 + change * 5;
    if (change > -2) return 50 + change * 4;
    return Math.max(25 + change * 3, 18);
  }

  _fire({ type, pair, title, body, urgent = false }) {
    const key = `${pair}:${type}`;
    const now = Date.now();
    if (this.lastAlertTimes[key] && now - this.lastAlertTimes[key] < this.DEBOUNCE_MS) return;
    this.lastAlertTimes[key] = now;

    const alert = {
      id: now,
      type,
      pair,
      title,
      body,
      time: new Date().toISOString(),
    };
    this.alerts.unshift(alert);
    if (this.alerts.length > 100) this.alerts.pop();

    // Send WhatsApp
    if (this.phone && this.wa) {
      this.wa.sendAlert(this.phone, { title, body, urgent }).catch((e) =>
        console.error("[AlertMonitor] WA send error:", e.message)
      );
    }
  }

  // Called by trade route to add a manual alert
  addManual(alert) {
    this.alerts.unshift({ ...alert, id: Date.now(), time: new Date().toISOString() });
    if (this.alerts.length > 100) this.alerts.pop();
  }

  getAll() {
    return this.alerts;
  }
}

module.exports = AlertMonitor;
