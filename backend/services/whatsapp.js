const twilio = require("twilio");

const DND_START = 23; // 23h
const DND_END = 7;    // 7h

class WhatsAppService {
  constructor(accountSid, authToken, from) {
    if (accountSid && authToken) {
      this.client = twilio(accountSid, authToken);
    }
    this.from = from || "whatsapp:+14155238886";
  }

  _isInDnd() {
    const h = new Date().getHours();
    return h >= DND_START || h < DND_END;
  }

  async send(to, message, { urgent = false } = {}) {
    if (!this.client) {
      console.warn("[WhatsApp] Twilio not configured — skipping send");
      return { simulated: true };
    }
    if (this._isInDnd() && !urgent) {
      console.info("[WhatsApp] DND active — message suppressed");
      return { suppressed: true, reason: "DND" };
    }

    const formattedTo = to.startsWith("whatsapp:") ? to : `whatsapp:${to}`;
    return this.client.messages.create({
      from: this.from,
      to: formattedTo,
      body: message,
    });
  }

  // Helpers for common alert types
  sendTradeConfirm(to, { type, pair, volume, price }) {
    const icon = type === "buy" ? "🟢" : "🔴";
    const msg =
      `${icon} *ORDRE EXÉCUTÉ*\n` +
      `${type.toUpperCase()} ${volume} ${pair}\n` +
      `Prix: $${parseFloat(price).toLocaleString("fr")}\n` +
      `_${new Date().toLocaleString("fr")}_`;
    return this.send(to, msg, { urgent: true });
  }

  sendAlert(to, { title, body, urgent = false }) {
    const msg = `📣 *${title}*\n${body}\n_${new Date().toLocaleTimeString("fr")}_`;
    return this.send(to, msg, { urgent });
  }

  sendDailySummary(to, { portfolioValue, pnlPct, topCoin, topChange }) {
    const arrow = pnlPct >= 0 ? "▲" : "▼";
    const msg =
      `📊 *RÉSUMÉ QUOTIDIEN*\n\n` +
      `💼 Portfolio: €${portfolioValue.toLocaleString("fr", { maximumFractionDigits: 0 })}\n` +
      `${arrow} Performance: ${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%\n` +
      `🏆 Top: ${topCoin} ${topChange >= 0 ? "+" : ""}${topChange.toFixed(2)}%\n` +
      `_Envoyé à ${new Date().toLocaleTimeString("fr")}_`;
    return this.send(to, msg, { urgent: false });
  }
}

module.exports = WhatsAppService;
