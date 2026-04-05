import { useState, useEffect } from "react";
import { api } from "../api";

const C = {
  green: "#00ff88", red: "#ff4d6d", yellow: "#f0b429", blue: "#00b4ff",
  text: "#e2e8f0", muted: "#4a5568", card: "rgba(255,255,255,0.03)",
  border: "rgba(255,255,255,0.06)", wa: "#25d366",
};

const ALERT_COLORS = { profit: C.green, buy: C.blue, sell: C.red, warn: C.yellow, summary: C.muted };

function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "À l'instant";
  if (mins < 60) return `Il y a ${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `Il y a ${hrs}h`;
  return `Il y a ${Math.floor(hrs / 24)}j`;
}

const TRIGGERS = [
  { label: "Profit > seuil défini", icon: "🚀", key: "profit" },
  { label: "RSI > 75 (surachat)", icon: "⚠️", key: "rsiHigh" },
  { label: "RSI < 30 (survente)", icon: "📉", key: "rsiLow" },
  { label: "Chute > 5% rapide", icon: "🔴", key: "drop" },
  { label: "Résumé quotidien 20h", icon: "📊", key: "daily" },
  { label: "Opportunité arbitrage", icon: "💰", key: "arb", off: true },
];

export default function Alertes({ prices }) {
  const [alerts, setAlerts] = useState([]);
  const [phone, setPhone] = useState("+32 470 000 000");
  const [tab, setTab] = useState("feed");
  const [sending, setSending] = useState(false);
  const [sentOk, setSentOk] = useState(false);
  const [triggers, setTriggers] = useState(
    Object.fromEntries(TRIGGERS.map((t) => [t.key, !t.off]))
  );

  useEffect(() => {
    api.getAlerts()
      .then(setAlerts)
      .catch(() => {});
    const iv = setInterval(() => {
      api.getAlerts().then(setAlerts).catch(() => {});
    }, 30_000);
    return () => clearInterval(iv);
  }, []);

  const topPair = Object.values(prices).sort((a, b) => Math.abs(b.change24h) - Math.abs(a.change24h))[0];

  const sendTest = async () => {
    setSending(true);
    try {
      const msg =
        `📣 TEST CRYPTO TRADER\n\n` +
        `💼 Marché actuel:\n` +
        (topPair ? `${topPair.symbol} ${topPair.name}: $${topPair.price.toLocaleString("fr", { maximumFractionDigits: 2 })} (${topPair.change24h >= 0 ? "+" : ""}${topPair.change24h}%)\n` : "") +
        `\n_Envoyé depuis Crypto Trader_`;
      await api.sendWhatsApp(phone, msg);
      setSentOk(true);
      setTimeout(() => setSentOk(false), 3000);
    } catch (e) {
      alert("Erreur: " + e.message);
    }
    setSending(false);
  };

  return (
    <div style={{ paddingBottom: "80px" }}>
      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: `1px solid ${C.border}` }}>
        {[{ id: "feed", label: "📣 Flux" }, { id: "config", label: "💬 WhatsApp" }].map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            flex: 1, padding: "14px", border: "none", background: "transparent",
            color: tab === t.id ? C.green : C.muted,
            borderBottom: tab === t.id ? `2px solid ${C.green}` : "2px solid transparent",
            fontFamily: "inherit", fontSize: "12px", cursor: "pointer",
            letterSpacing: "0.5px",
          }}>{t.label}</button>
        ))}
      </div>

      {tab === "feed" && (
        <div style={{ padding: "12px" }}>
          <div style={{ fontSize: "10px", color: C.muted, letterSpacing: "1px", marginBottom: "10px" }}>
            FLUX D'ALERTES EN TEMPS RÉEL
          </div>
          {alerts.length === 0 && (
            <div style={{ textAlign: "center", color: C.muted, fontSize: "12px", padding: "40px 20px" }}>
              Aucune alerte pour l'instant.<br />Les alertes apparaissent ici automatiquement.
            </div>
          )}
          {alerts.map((a) => {
            const color = ALERT_COLORS[a.type] || C.muted;
            return (
              <div key={a.id} style={{
                padding: "12px 14px", marginBottom: "8px", borderRadius: "8px",
                background: C.card, border: `1px solid ${C.border}`,
                borderLeft: `3px solid ${color}`,
              }}>
                <div style={{ fontSize: "10px", color, letterSpacing: "1px", marginBottom: "4px" }}>
                  {a.pair || ""} {a.type?.toUpperCase()}
                </div>
                <div style={{ fontSize: "12px", color: "#94a3b8", lineHeight: "1.5" }}>
                  <strong style={{ color: C.text }}>{a.title}</strong><br />
                  {a.body}
                </div>
                <div style={{ fontSize: "9px", color: C.muted, marginTop: "6px" }}>
                  {timeAgo(a.time)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {tab === "config" && (
        <div style={{ padding: "16px" }}>
          <div style={{ fontSize: "9px", color: C.muted, letterSpacing: "1px", marginBottom: "14px" }}>
            CONFIGURATION WHATSAPP
          </div>

          <div style={{ marginBottom: "14px" }}>
            <label style={{ fontSize: "10px", color: C.muted, letterSpacing: "1px", display: "block", marginBottom: "6px" }}>
              VOTRE NUMÉRO WHATSAPP
            </label>
            <input value={phone} onChange={(e) => setPhone(e.target.value)}
              placeholder="+32 470 000 000" type="tel"
              style={{
                width: "100%", background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.12)", borderRadius: "8px",
                padding: "12px 14px", color: C.text, fontSize: "16px", fontFamily: "inherit",
              }} />
          </div>

          {/* Triggers */}
          <div style={{ background: "rgba(37,211,102,0.05)", border: "1px solid rgba(37,211,102,0.15)", borderRadius: "10px", padding: "14px", marginBottom: "14px" }}>
            <div style={{ fontSize: "10px", color: C.wa, letterSpacing: "1px", marginBottom: "10px" }}>DÉCLENCHEURS</div>
            {TRIGGERS.map((t) => (
              <div key={t.key} onClick={() => setTriggers((p) => ({ ...p, [t.key]: !p[t.key] }))}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottom: `1px solid ${C.border}`, cursor: "pointer" }}>
                <span style={{ fontSize: "12px", color: triggers[t.key] ? "#94a3b8" : C.muted }}>
                  {t.icon} {t.label}
                </span>
                <span style={{
                  fontSize: "10px", padding: "2px 8px", borderRadius: "3px",
                  background: triggers[t.key] ? C.green + "20" : C.muted + "20",
                  color: triggers[t.key] ? C.green : C.muted,
                  border: `1px solid ${triggers[t.key] ? C.green + "40" : C.muted + "40"}`,
                }}>
                  {triggers[t.key] ? "ON" : "OFF"}
                </span>
              </div>
            ))}
          </div>

          {/* Preview message */}
          <div style={{ background: "rgba(0,180,255,0.04)", border: "1px solid rgba(0,180,255,0.12)", borderRadius: "10px", padding: "14px", marginBottom: "14px" }}>
            <div style={{ fontSize: "10px", color: C.blue, letterSpacing: "1px", marginBottom: "8px" }}>APERÇU DU MESSAGE</div>
            <div style={{ fontSize: "11px", color: "#4a5568", lineHeight: "1.7", fontFamily: "monospace" }}>
              📣 TEST CRYPTO TRADER{"\n\n"}
              💼 Marché actuel:{"\n"}
              {topPair ? `${topPair.symbol} ${topPair.name}: $${topPair.price?.toLocaleString("fr", { maximumFractionDigits: 2 })} (${topPair.change24h >= 0 ? "+" : ""}${topPair.change24h?.toFixed(2)}%)` : "Chargement…"}
            </div>
          </div>

          <button onClick={sendTest} disabled={sending} style={{
            width: "100%", padding: "14px", borderRadius: "10px",
            background: sentOk ? "rgba(37,211,102,0.25)" : "rgba(37,211,102,0.12)",
            color: C.wa, border: `1px solid ${C.wa}40`,
            fontFamily: "inherit", fontSize: "14px", cursor: sending ? "not-allowed" : "pointer",
            opacity: sending ? 0.6 : 1, minHeight: "52px",
          }}>
            {sentOk ? "✓ Message envoyé !" : sending ? "⟳ Envoi…" : "📲 Envoyer message test"}
          </button>

          <div style={{ fontSize: "10px", color: "#1f2937", marginTop: "12px", lineHeight: "1.6" }}>
            ⚙️ Prérequis: configurez TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN dans le fichier .env du backend.<br />
            Rejoignez le sandbox WhatsApp Twilio en envoyant le code d'activation au +1 415 523 8886.
          </div>
        </div>
      )}
    </div>
  );
}
