import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { THEME } from "../theme";

const BOT_API = "https://cryptomind-etoro.onrender.com";

const ALERTS_POLL_MS = 30000;   // file d'alertes
const SLOW_POLL_MS   = 300000;  // règles, historique, allocation, sentiment
const ALERTS_LIMIT   = 50;
const MAX_TP_LEVELS  = 3;

// ── Libellés ───────────────────────────────────────────────────────────────
const FAMILY_LABELS = {
  take_profit: "Take profit",
  allocation_drift: "Allocation",
  sentiment_zone: "Sentiment",
};
const FAMILY_COLORS = {
  take_profit: THEME.green,
  allocation_drift: THEME.blue,
  sentiment_zone: THEME.purple,
};
const FAMILY_OPTIONS = [
  ["take_profit", "Take profit"],
  ["allocation_drift", "Allocation"],
  ["sentiment_zone", "Sentiment"],
];
const STATUS_LABELS = {
  pending: "En attente",
  executed: "Exécuté",
  ignored: "Ignoré",
  postponed: "Reporté",
};
const STATUS_COLORS = {
  pending: THEME.yellow,
  executed: THEME.green,
  ignored: THEME.text2,
  postponed: THEME.yellow,
};
const CATEGORY_LABELS = {
  crypto: "Crypto",
  stocks: "Actions",
  gold_miners: "Minières or",
  cash: "Cash",
};
const CATEGORY_OPTIONS = Object.entries(CATEGORY_LABELS);
const ZONE_LABELS = {
  extreme_fear: "Extreme Fear",
  fear: "Fear",
  greed: "Greed",
  extreme_greed: "Extreme Greed",
};
const ZONE_OPTIONS = Object.entries(ZONE_LABELS);
const PERIODS = [[7, "7j"], [30, "30j"], [90, "90j"], [365, "1an"]];

// ── Formatage ──────────────────────────────────────────────────────────────
function isNum(v) {
  return v !== null && v !== undefined && v !== "" && !Number.isNaN(Number(v));
}

function fmtNum(v, digits = 2) {
  return isNum(v) ? Number(v).toFixed(digits) : "—";
}

function fmtUsd(v, digits = 0) {
  if (!isNum(v)) return "—";
  return `${Number(v).toLocaleString("fr", { minimumFractionDigits: digits, maximumFractionDigits: digits })} $`;
}

function fmtSigned(v, digits = 1, suffix = "") {
  if (!isNum(v)) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}${suffix}`;
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("fr", { day: "2-digit", month: "2-digit" })
    + " " + d.toLocaleTimeString("fr", { hour: "2-digit", minute: "2-digit" });
}

function fmtDay(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("fr", { day: "2-digit", month: "2-digit" });
}

function relTime(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const diff = Math.round((Date.now() - t) / 1000);
  const abs = Math.abs(diff);
  const s = abs < 60 ? `${abs} s`
    : abs < 3600 ? `${Math.floor(abs / 60)} min`
    : abs < 86400 ? `${Math.floor(abs / 3600)} h`
    : `${Math.floor(abs / 86400)} j`;
  return diff >= 0 ? `il y a ${s}` : `dans ${s}`;
}

// ── HTTP ───────────────────────────────────────────────────────────────────
class ApiError extends Error {
  constructor(status, detail) {
    super(`HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function fetchJson(url, signal, options = {}) {
  const headers = { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
  const res = await fetch(url, { signal, ...options, headers });
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json())?.detail ?? null; } catch { /* corps non JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

function formatApiError(err) {
  if (err instanceof ApiError) {
    const d = err.detail;
    if (err.status === 422) {
      if (Array.isArray(d)) {
        const items = d.map((e) => {
          const loc = Array.isArray(e?.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
          return loc ? `${loc} : ${e?.msg || "invalide"}` : (e?.msg || "invalide");
        });
        return `Paramètres invalides — ${items.join(" ; ")}`;
      }
      if (typeof d === "string") return `Paramètres invalides — ${d}`;
      if (d && typeof d === "object") return `Paramètres invalides — ${JSON.stringify(d)}`;
      return "Paramètres invalides (422)";
    }
    if (err.status === 409) return typeof d === "string" ? d : "Alerte déjà traitée (409)";
    if (err.status === 404) return typeof d === "string" ? d : "Introuvable (404)";
    if (typeof d === "string") return `HTTP ${err.status} — ${d}`;
    return `HTTP ${err.status}`;
  }
  if (err?.name === "TypeError") return "réseau injoignable";
  return err?.message || String(err);
}

// Polling générique : premier appel immédiat + intervalle, abort au démontage.
function usePolling(fn, intervalMs, deps = []) {
  useEffect(() => {
    const controller = new AbortController();
    fn(controller.signal);
    const iv = setInterval(() => fn(controller.signal), intervalMs);
    return () => { clearInterval(iv); controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fn, intervalMs, ...deps]);
}

function useMounted() {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  return mounted;
}

// ── Briques de style CryptoMind ────────────────────────────────────────────
function badge(val, color) {
  return (
    <span style={{
      fontSize: "10px", padding: "2px 10px", borderRadius: "20px",
      background: color + "18", color, border: `1px solid ${color}35`,
      fontWeight: "600", whiteSpace: "nowrap",
    }}>{val}</span>
  );
}

function sectionTitle(text, right) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      marginBottom: "10px", gap: "8px",
    }}>
      <span style={{
        fontSize: "9px", color: THEME.muted, letterSpacing: "2px",
        fontWeight: "700", textTransform: "uppercase",
      }}>{text}</span>
      {right}
    </div>
  );
}

function inputStyle(accent) {
  return {
    width: "100%", background: "rgba(255,255,255,0.04)",
    border: `1px solid ${accent || THEME.border}`,
    borderRadius: "12px", padding: "10px 12px",
    color: THEME.text, fontSize: "13px", fontFamily: "inherit",
    outline: "none", appearance: "none",
  };
}

function InfoBox({ color, children }) {
  return (
    <div style={{
      background: color + "14", border: `1px solid ${color}35`,
      borderRadius: "10px", padding: "10px 14px", marginBottom: "12px",
      fontSize: "11px", color, lineHeight: 1.5,
    }}>{children}</div>
  );
}

function EmptyBox({ children }) {
  return (
    <div style={{
      background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "16px",
      padding: "18px", textAlign: "center", fontSize: "11px", color: THEME.muted,
    }}>{children}</div>
  );
}

function pillStyle(active, color) {
  return {
    padding: "5px 12px", borderRadius: "20px", cursor: "pointer", fontFamily: "inherit",
    fontSize: "10px", fontWeight: "700", transition: "all 0.2s",
    background: active ? color + "22" : THEME.glass,
    color: active ? color : THEME.muted,
    border: `1px solid ${active ? color + "55" : THEME.border}`,
  };
}

function actionBtnStyle(color, disabled) {
  return {
    flex: 1, minHeight: "40px", padding: "8px 6px", borderRadius: "12px",
    border: `1px solid ${color}45`, background: color + "1f", color,
    fontWeight: "700", fontSize: "11px", fontFamily: "inherit", letterSpacing: "0.5px",
    cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1,
    transition: "all 0.2s",
  };
}

function Field({ label, children }) {
  return (
    <label style={{ display: "block" }}>
      <div style={{ fontSize: "8px", color: THEME.muted, letterSpacing: "1px", marginBottom: "4px", textTransform: "uppercase" }}>{label}</div>
      {children}
    </label>
  );
}

function Select({ value, onChange, options, accent }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={{ ...inputStyle(accent), backgroundImage: "none" }}>
      {options.map(([v, l]) => (
        <option key={v} value={v} style={{ background: THEME.bg2, color: THEME.text }}>{l}</option>
      ))}
    </select>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. File d'alertes
// ═══════════════════════════════════════════════════════════════════════════
function AlertCard({ a, busy, onAct }) {
  const pending = a.status === "pending";
  const famColor = FAMILY_COLORS[a.family] || THEME.text2;
  const stColor = STATUS_COLORS[a.status] || THEME.muted;
  return (
    <div style={{
      background: pending ? famColor + "0d" : THEME.glass,
      border: `1px solid ${pending ? famColor + "50" : THEME.border}`,
      borderLeft: `3px solid ${pending ? famColor : stColor}`,
      borderRadius: "16px", padding: "12px 14px", marginBottom: "10px",
      opacity: pending ? 1 : 0.55,
      boxShadow: pending ? `0 0 14px ${famColor}22` : "none",
      transition: "opacity 0.4s ease, background 0.4s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
          {badge(FAMILY_LABELS[a.family] || a.family || "—", famColor)}
          {a.rule_name && badge(a.rule_name, THEME.text2)}
        </div>
        <span style={{ fontSize: "10px", color: THEME.muted, whiteSpace: "nowrap" }} title={fmtDateTime(a.created_at)}>
          {relTime(a.created_at)}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: "8px", marginBottom: pending ? "10px" : "6px" }}>
        {a.symbol && <span style={{ fontSize: "14px", fontWeight: "800", color: THEME.text, flexShrink: 0 }}>{a.symbol}</span>}
        <span style={{ fontSize: "12px", fontWeight: "600", color: THEME.text, lineHeight: 1.4 }}>{a.title || "—"}</span>
      </div>

      {pending ? (
        <div style={{ display: "flex", gap: "6px" }}>
          <button disabled={busy} onClick={() => onAct(a, "executed")} style={actionBtnStyle(THEME.green, busy)}>✓ Exécuté</button>
          <button disabled={busy} onClick={() => onAct(a, "ignored")} style={actionBtnStyle(THEME.text2, busy)}>✕ Ignoré</button>
          <button disabled={busy} onClick={() => onAct(a, "postponed")} style={actionBtnStyle(THEME.yellow, busy)}>⏰ Reporté</button>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          {badge(STATUS_LABELS[a.status] || a.status, stColor)}
          {a.acted_at && <span style={{ fontSize: "10px", color: THEME.muted }}>le {fmtDateTime(a.acted_at)}</span>}
          {a.status === "postponed" && a.postponed_until && (
            <span style={{ fontSize: "10px", color: THEME.yellow }}>→ rappel {relTime(a.postponed_until)}</span>
          )}
        </div>
      )}
    </div>
  );
}

function AlertsSection() {
  const [alerts, setAlerts]         = useState([]);
  const [error, setError]           = useState(null);
  const [loaded, setLoaded]         = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [msg, setMsg]               = useState(null);
  const [busy, setBusy]             = useState(() => new Set());
  const mounted = useMounted();

  const refresh = useCallback(async (signal) => {
    try {
      const data = await fetchJson(`${BOT_API}/watch/alerts?status=all&limit=${ALERTS_LIMIT}`, signal);
      if (signal?.aborted || !mounted.current) return;
      setAlerts(Array.isArray(data?.alerts) ? data.alerts : []);
      setError(null);
      setLoaded(true);
      setLastUpdate(new Date());
    } catch (err) {
      if (err?.name === "AbortError" || !mounted.current) return;
      setError(formatApiError(err));
    }
  }, [mounted]);

  usePolling(refresh, ALERTS_POLL_MS);

  const act = async (alert, action) => {
    let hours = 24;
    if (action === "postponed") {
      const raw = window.prompt("Reporter cette alerte de combien d'heures ?", "24");
      if (raw === null) return;
      hours = Number(String(raw).replace(",", "."));
      if (!Number.isFinite(hours) || hours <= 0) {
        setMsg({ color: THEME.red, text: "✗ Nombre d'heures invalide, report annulé." });
        return;
      }
    }
    const previous = alerts;
    const nowIso = new Date().toISOString();
    setBusy((s) => { const n = new Set(s); n.add(alert.id); return n; });
    setMsg(null);
    // Mise à jour optimiste
    setAlerts((list) => list.map((a) => (a.id === alert.id ? {
      ...a, status: action, acted_at: nowIso,
      postponed_until: action === "postponed" ? new Date(Date.now() + hours * 3600 * 1000).toISOString() : a.postponed_until,
    } : a)));
    try {
      await fetchJson(`${BOT_API}/watch/alerts/${encodeURIComponent(alert.id)}/action`, undefined, {
        method: "POST",
        body: JSON.stringify({ action, postpone_hours: hours }),
      });
      if (!mounted.current) return;
      setMsg({
        color: STATUS_COLORS[action],
        text: action === "postponed"
          ? `⏰ Alerte reportée de ${hours} h.`
          : `✓ Alerte marquée « ${STATUS_LABELS[action]} ».`,
      });
    } catch (err) {
      if (!mounted.current) return;
      setAlerts(previous);
      setMsg({ color: THEME.red, text: `✗ Échec : ${formatApiError(err)}` });
    }
    setBusy((s) => { const n = new Set(s); n.delete(alert.id); return n; });
    await refresh();
  };

  const { pending, done } = useMemo(() => {
    const byDate = (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    const p = alerts.filter((a) => a.status === "pending").sort(byDate);
    const d = alerts.filter((a) => a.status !== "pending").sort(byDate);
    return { pending: p, done: d };
  }, [alerts]);

  const down = Boolean(error);

  return (
    <div style={{
      background: pending.length
        ? "linear-gradient(135deg, rgba(245,158,11,0.07), rgba(139,92,246,0.04))"
        : "rgba(255,255,255,0.02)",
      border: `1px solid ${pending.length ? "rgba(245,158,11,0.25)" : THEME.border}`,
      borderRadius: "20px", padding: "16px", marginBottom: "16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
        <div>
          <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "6px" }}>👁️ VEILLE · FILE D'ALERTES</div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div style={{
              width: "10px", height: "10px", borderRadius: "50%",
              background: down ? THEME.red : pending.length ? THEME.yellow : THEME.green,
              boxShadow: down ? "none" : `0 0 10px ${pending.length ? THEME.yellow : THEME.green}`,
              animation: pending.length && !down ? "pulse 2s infinite" : "none",
            }} />
            <span style={{ fontSize: "18px", fontWeight: "800", color: down ? THEME.red : pending.length ? THEME.yellow : THEME.green }}>
              {down && !loaded ? "INJOIGNABLE" : !loaded ? "Connexion…" : pending.length ? `${pending.length} À VALIDER` : "RIEN À VALIDER"}
            </span>
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: "9px", color: THEME.muted, lineHeight: 1.6 }}>
          {lastUpdate ? `MAJ ${lastUpdate.toLocaleTimeString("fr")}` : "En attente…"}
          <div>toutes les 30 s</div>
        </div>
      </div>

      {down && (
        <InfoBox color={THEME.red}>
          <strong>Backend de veille injoignable</strong> — {error}. Nouvelle tentative toutes les 30 s.
        </InfoBox>
      )}
      {msg && (
        <div style={{
          marginBottom: "10px", padding: "8px 12px", borderRadius: "10px", fontSize: "11px",
          background: msg.color + "14", border: `1px solid ${msg.color}35`, color: msg.color,
        }}>{msg.text}</div>
      )}

      {loaded && pending.length === 0 && (
        <EmptyBox>Aucune alerte en attente.</EmptyBox>
      )}
      {!loaded && !down && <EmptyBox>Chargement…</EmptyBox>}
      {pending.map((a) => <AlertCard key={a.id} a={a} busy={busy.has(a.id)} onAct={act} />)}

      {done.length > 0 && (
        <div style={{ marginTop: "14px" }}>
          {sectionTitle("Alertes traitées", badge(`${done.length}`, THEME.text2))}
          {done.map((a) => <AlertCard key={a.id} a={a} busy={false} onAct={act} />)}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. Règles
// ═══════════════════════════════════════════════════════════════════════════
function ruleSummary(r) {
  const p = r?.params || {};
  switch (r?.family) {
    case "take_profit": {
      const levels = Array.isArray(p.levels) ? p.levels : [];
      const lv = levels.map((l, i) => {
        const trig = isNum(l?.price) ? `${fmtNum(l.price)} $` : isNum(l?.multiple_of_pru) ? `×${fmtNum(l.multiple_of_pru)} PRU` : "?";
        return `P${i + 1} ${trig} → vendre ${fmtNum(l?.sell_pct, 0)} %`;
      });
      return `PRU ${fmtNum(p.pru)} $ · ${lv.join(" · ") || "aucun palier"}`;
    }
    case "allocation_drift":
      return `${CATEGORY_LABELS[p.category] || p.category || "?"} · cible ${fmtNum(p.target_pct, 0)} % · seuil ${fmtNum(p.threshold_points, 0)} pts · min ${fmtUsd(p.min_rebalance_usd)}`;
    case "sentiment_zone":
      return `${ZONE_LABELS[p.zone] || p.zone || "?"} pendant ${fmtNum(p.consecutive_days, 0)} jour(s) consécutif(s)`;
    default:
      return JSON.stringify(p);
  }
}

function RuleCard({ r, busy, onToggle, onDelete }) {
  const color = FAMILY_COLORS[r.family] || THEME.text2;
  const on = Boolean(r.enabled);
  const st = r.state || {};
  return (
    <div style={{
      background: THEME.glass, border: `1px solid ${THEME.border}`,
      borderLeft: `3px solid ${on ? color : THEME.muted}`, borderRadius: "16px",
      padding: "12px 14px", marginBottom: "10px", opacity: on ? 1 : 0.6,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap", minWidth: 0 }}>
          {badge(FAMILY_LABELS[r.family] || r.family, color)}
          {r.symbol && <span style={{ fontSize: "13px", fontWeight: "800", color: THEME.text }}>{r.symbol}</span>}
          <span style={{ fontSize: "12px", fontWeight: "600", color: THEME.text }}>{r.name || "—"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", flexShrink: 0 }}>
          <button disabled={busy} onClick={() => onToggle(r)} title={on ? "Désactiver" : "Activer"} style={{
            width: "40px", height: "22px", borderRadius: "11px", border: "none", cursor: busy ? "default" : "pointer",
            background: on ? THEME.green : "rgba(255,255,255,0.12)", position: "relative", transition: "background 0.2s",
            opacity: busy ? 0.5 : 1, padding: 0,
          }}>
            <span style={{
              position: "absolute", top: "3px", left: on ? "21px" : "3px", width: "16px", height: "16px",
              borderRadius: "50%", background: "#fff", transition: "left 0.2s",
            }} />
          </button>
          <button disabled={busy} onClick={() => onDelete(r)} title="Supprimer" style={{
            width: "30px", height: "30px", borderRadius: "10px", cursor: busy ? "default" : "pointer",
            background: THEME.red + "14", border: `1px solid ${THEME.red}35`, color: THEME.red,
            fontSize: "13px", fontFamily: "inherit", opacity: busy ? 0.5 : 1,
          }}>🗑</button>
        </div>
      </div>
      <div style={{ fontSize: "11px", color: THEME.text2, lineHeight: 1.5 }}>{ruleSummary(r)}</div>
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "6px", fontSize: "9px", color: THEME.muted }}>
        <span>{on ? "active" : "désactivée"}</span>
        {st.condition_active && <span style={{ color: THEME.yellow }}>· condition active</span>}
        {st.last_fired_at && <span>· dernière alerte {relTime(st.last_fired_at)}</span>}
        {isNum(st.consecutive_days) && Number(st.consecutive_days) > 0 && <span>· {st.consecutive_days} j consécutif(s)</span>}
      </div>
    </div>
  );
}

const EMPTY_LEVEL = { mode: "price", value: "", sell_pct: "" };

function RuleForm({ onSubmit, onCancel }) {
  const [family, setFamily]   = useState("take_profit");
  const [name, setName]       = useState("");
  const [symbol, setSymbol]   = useState("");
  const [pru, setPru]         = useState("");
  const [levels, setLevels]   = useState([{ ...EMPTY_LEVEL }]);
  const [category, setCategory] = useState("crypto");
  const [targetPct, setTargetPct] = useState("");
  const [threshold, setThreshold] = useState("5");
  const [minUsd, setMinUsd]   = useState("100");
  const [zone, setZone]       = useState("extreme_fear");
  const [days, setDays]       = useState("3");
  const [error, setError]     = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const color = FAMILY_COLORS[family];

  const num = (v) => Number(String(v).replace(",", "."));

  const buildPayload = () => {
    if (family === "take_profit") {
      const sym = symbol.trim().toUpperCase();
      if (!sym) throw new Error("Le symbole est requis.");
      if (!isNum(pru) || num(pru) <= 0) throw new Error("Le PRU doit être un nombre positif.");
      const filled = levels.filter((l) => String(l.value).trim() !== "" || String(l.sell_pct).trim() !== "");
      if (filled.length === 0) throw new Error("Renseigne au moins un palier.");
      const out = filled.map((l, i) => {
        if (!isNum(l.value) || num(l.value) <= 0) throw new Error(`Palier ${i + 1} : ${l.mode === "price" ? "prix" : "multiple du PRU"} invalide.`);
        if (!isNum(l.sell_pct) || num(l.sell_pct) <= 0 || num(l.sell_pct) > 100) throw new Error(`Palier ${i + 1} : % à vendre entre 1 et 100.`);
        return l.mode === "price"
          ? { price: num(l.value), sell_pct: num(l.sell_pct) }
          : { multiple_of_pru: num(l.value), sell_pct: num(l.sell_pct) };
      });
      return {
        family, enabled: true, symbol: sym,
        name: name.trim() || `TP ${sym}`,
        params: { pru: num(pru), levels: out },
      };
    }
    if (family === "allocation_drift") {
      if (!isNum(targetPct) || num(targetPct) < 0 || num(targetPct) > 100) throw new Error("La cible doit être entre 0 et 100 %.");
      if (!isNum(threshold) || num(threshold) <= 0) throw new Error("Le seuil (points) doit être positif.");
      if (!isNum(minUsd) || num(minUsd) < 0) throw new Error("Le montant minimum doit être ≥ 0.");
      return {
        family, enabled: true, symbol: null,
        name: name.trim() || `Allocation ${CATEGORY_LABELS[category]}`,
        params: { category, target_pct: num(targetPct), threshold_points: num(threshold), min_rebalance_usd: num(minUsd) },
      };
    }
    if (!isNum(days) || num(days) < 1 || !Number.isInteger(num(days))) throw new Error("Le nombre de jours doit être un entier ≥ 1.");
    return {
      family, enabled: true, symbol: null,
      name: name.trim() || `${ZONE_LABELS[zone]} ${num(days)} j`,
      params: { zone, consecutive_days: num(days) },
    };
  };

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    let payload;
    try { payload = buildPayload(); } catch (err) { setError(err.message); return; }
    setSubmitting(true);
    try {
      await onSubmit(payload);
    } catch (err) {
      setError(formatApiError(err));
    }
    setSubmitting(false);
  };

  const setLevel = (i, patch) => setLevels((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const grid2 = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" };

  return (
    <form onSubmit={submit} style={{
      background: color + "0a", border: `1px solid ${color}35`, borderRadius: "16px",
      padding: "14px", marginBottom: "12px",
    }}>
      <div style={{ display: "flex", gap: "6px", marginBottom: "12px" }}>
        {FAMILY_OPTIONS.map(([v, l]) => (
          <button key={v} type="button" onClick={() => { setFamily(v); setError(null); }} style={{ ...pillStyle(family === v, FAMILY_COLORS[v]), flex: 1 }}>{l}</button>
        ))}
      </div>

      <div style={{ display: "grid", gap: "8px" }}>
        <Field label="Nom de la règle (optionnel)">
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nom affiché dans les alertes" style={inputStyle()} />
        </Field>

        {family === "take_profit" && (
          <>
            <div style={grid2}>
              <Field label="Symbole">
                <input type="text" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="AAPL" autoCapitalize="characters" spellCheck={false} style={inputStyle(symbol ? color : undefined)} />
              </Field>
              <Field label="PRU ($)">
                <input type="number" step="any" inputMode="decimal" value={pru} onChange={(e) => setPru(e.target.value)} placeholder="190" style={inputStyle()} />
              </Field>
            </div>
            {levels.map((l, i) => (
              <div key={i} style={{ background: "rgba(255,255,255,0.02)", border: `1px solid ${THEME.border}`, borderRadius: "12px", padding: "10px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <span style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px" }}>PALIER {i + 1}</span>
                  <div style={{ display: "flex", gap: "4px" }}>
                    <button type="button" onClick={() => setLevel(i, { mode: "price" })} style={pillStyle(l.mode === "price", color)}>Prix</button>
                    <button type="button" onClick={() => setLevel(i, { mode: "multiple" })} style={pillStyle(l.mode === "multiple", color)}>× PRU</button>
                    {levels.length > 1 && (
                      <button type="button" onClick={() => setLevels((ls) => ls.filter((_, j) => j !== i))} style={pillStyle(false, THEME.red)}>✕</button>
                    )}
                  </div>
                </div>
                <div style={grid2}>
                  <Field label={l.mode === "price" ? "Prix ($)" : "Multiple du PRU"}>
                    <input type="number" step="any" inputMode="decimal" value={l.value} onChange={(e) => setLevel(i, { value: e.target.value })} placeholder={l.mode === "price" ? "250" : "1.5"} style={inputStyle()} />
                  </Field>
                  <Field label="% à vendre">
                    <input type="number" step="any" inputMode="decimal" min="1" max="100" value={l.sell_pct} onChange={(e) => setLevel(i, { sell_pct: e.target.value })} placeholder="25" style={inputStyle()} />
                  </Field>
                </div>
              </div>
            ))}
            {levels.length < MAX_TP_LEVELS && (
              <button type="button" onClick={() => setLevels((ls) => [...ls, { ...EMPTY_LEVEL }])} style={{ ...pillStyle(false, color), alignSelf: "flex-start" }}>
                + Ajouter un palier ({levels.length}/{MAX_TP_LEVELS})
              </button>
            )}
          </>
        )}

        {family === "allocation_drift" && (
          <>
            <div style={grid2}>
              <Field label="Catégorie">
                <Select value={category} onChange={setCategory} options={CATEGORY_OPTIONS} accent={color} />
              </Field>
              <Field label="Cible (%)">
                <input type="number" step="any" inputMode="decimal" min="0" max="100" value={targetPct} onChange={(e) => setTargetPct(e.target.value)} placeholder="40" style={inputStyle()} />
              </Field>
            </div>
            <div style={grid2}>
              <Field label="Seuil (points)">
                <input type="number" step="any" inputMode="decimal" value={threshold} onChange={(e) => setThreshold(e.target.value)} placeholder="5" style={inputStyle()} />
              </Field>
              <Field label="Montant minimum ($)">
                <input type="number" step="any" inputMode="decimal" value={minUsd} onChange={(e) => setMinUsd(e.target.value)} placeholder="100" style={inputStyle()} />
              </Field>
            </div>
          </>
        )}

        {family === "sentiment_zone" && (
          <div style={grid2}>
            <Field label="Zone">
              <Select value={zone} onChange={setZone} options={ZONE_OPTIONS} accent={color} />
            </Field>
            <Field label="Jours consécutifs">
              <input type="number" step="1" inputMode="numeric" min="1" value={days} onChange={(e) => setDays(e.target.value)} placeholder="3" style={inputStyle()} />
            </Field>
          </div>
        )}
      </div>

      {error && (
        <div style={{
          marginTop: "10px", padding: "8px 12px", borderRadius: "10px", fontSize: "11px",
          background: THEME.red + "14", border: `1px solid ${THEME.red}35`, color: THEME.red, lineHeight: 1.5,
        }}>✗ {error}</div>
      )}

      <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
        <button type="button" onClick={onCancel} disabled={submitting} style={{ ...actionBtnStyle(THEME.text2, submitting), flex: "0 0 auto", padding: "8px 16px" }}>Annuler</button>
        <button type="submit" disabled={submitting} style={{
          ...actionBtnStyle(color, submitting), background: THEME.gradP, color: "#fff", border: "none",
          boxShadow: "0 0 18px rgba(139,92,246,0.35)",
        }}>{submitting ? "⟳ Envoi…" : "＋ Créer la règle"}</button>
      </div>
    </form>
  );
}

function RulesSection() {
  const [rules, setRules]     = useState([]);
  const [error, setError]     = useState(null);
  const [loaded, setLoaded]   = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [msg, setMsg]         = useState(null);
  const [busyIds, setBusyIds] = useState(() => new Set());
  const mounted = useMounted();

  const refresh = useCallback(async (signal) => {
    try {
      const data = await fetchJson(`${BOT_API}/watch/rules`, signal);
      if (signal?.aborted || !mounted.current) return;
      setRules(Array.isArray(data?.rules) ? data.rules : []);
      setError(null);
      setLoaded(true);
    } catch (err) {
      if (err?.name === "AbortError" || !mounted.current) return;
      setError(formatApiError(err));
    }
  }, [mounted]);

  usePolling(refresh, SLOW_POLL_MS);

  const withBusy = async (id, fn) => {
    setBusyIds((s) => { const n = new Set(s); n.add(id); return n; });
    try { await fn(); } finally {
      if (mounted.current) setBusyIds((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  };

  const toggle = (r) => withBusy(r.id, async () => {
    const previous = rules;
    const enabled = !r.enabled;
    setRules((ls) => ls.map((x) => (x.id === r.id ? { ...x, enabled } : x)));
    setMsg(null);
    try {
      await fetchJson(`${BOT_API}/watch/rules/${encodeURIComponent(r.id)}`, undefined, { method: "PUT", body: JSON.stringify({ enabled }) });
      if (!mounted.current) return;
      setMsg({ color: enabled ? THEME.green : THEME.text2, text: `${enabled ? "▶" : "⏸"} Règle « ${r.name} » ${enabled ? "activée" : "désactivée"}.` });
    } catch (err) {
      if (!mounted.current) return;
      setRules(previous);
      setMsg({ color: THEME.red, text: `✗ Échec : ${formatApiError(err)}` });
    }
    await refresh();
  });

  const remove = (r) => {
    if (!window.confirm(`Supprimer la règle « ${r.name} » ?\nLes alertes déjà émises sont conservées.`)) return;
    return withBusy(r.id, async () => {
      const previous = rules;
      setRules((ls) => ls.filter((x) => x.id !== r.id));
      setMsg(null);
      try {
        await fetchJson(`${BOT_API}/watch/rules/${encodeURIComponent(r.id)}`, undefined, { method: "DELETE" });
        if (!mounted.current) return;
        setMsg({ color: THEME.text2, text: `🗑 Règle « ${r.name} » supprimée.` });
      } catch (err) {
        if (!mounted.current) return;
        setRules(previous);
        setMsg({ color: THEME.red, text: `✗ Échec : ${formatApiError(err)}` });
      }
      await refresh();
    });
  };

  // Lève l'erreur pour que le formulaire l'affiche (422 inclus)
  const create = async (payload) => {
    const created = await fetchJson(`${BOT_API}/watch/rules`, undefined, { method: "POST", body: JSON.stringify(payload) });
    if (!mounted.current) return;
    setShowForm(false);
    setMsg({ color: THEME.green, text: `✓ Règle « ${created?.name || payload.name} » créée.` });
    await refresh();
  };

  const active = rules.filter((r) => r.enabled).length;

  return (
    <div style={{ marginBottom: "16px" }}>
      {sectionTitle("📐 Règles de veille", (
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          {badge(`${active} / ${rules.length} active${active > 1 ? "s" : ""}`, THEME.purple)}
          <button onClick={() => { setShowForm((v) => !v); setMsg(null); }} style={pillStyle(showForm, THEME.purple)}>
            {showForm ? "Fermer" : "＋ Nouvelle"}
          </button>
        </div>
      ))}

      {error && <InfoBox color={THEME.red}>Règles inaccessibles — {error}.</InfoBox>}
      {msg && (
        <div style={{
          marginBottom: "10px", padding: "8px 12px", borderRadius: "10px", fontSize: "11px",
          background: msg.color + "14", border: `1px solid ${msg.color}35`, color: msg.color,
        }}>{msg.text}</div>
      )}

      {showForm && <RuleForm onSubmit={create} onCancel={() => setShowForm(false)} />}

      {loaded && rules.length === 0 ? (
        <EmptyBox>Aucune règle. Crée-en une avec « ＋ Nouvelle ».</EmptyBox>
      ) : !loaded && !error ? (
        <EmptyBox>Chargement…</EmptyBox>
      ) : (
        rules.map((r) => <RuleCard key={r.id} r={r} busy={busyIds.has(r.id)} onToggle={toggle} onDelete={remove} />)
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. Courbe de valeur du portefeuille
// ═══════════════════════════════════════════════════════════════════════════
function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload;
  return (
    <div style={{
      background: THEME.bg2, border: `1px solid ${THEME.border}`, borderRadius: "10px",
      padding: "6px 10px", fontSize: "11px", color: THEME.text,
    }}>
      <div style={{ color: THEME.muted, fontSize: "9px" }}>{fmtDateTime(p?.at)}</div>
      <div style={{ fontWeight: "700" }}>{fmtUsd(p?.value, 2)}</div>
    </div>
  );
}

function PortfolioSection() {
  const [days, setDays]     = useState(30);
  const [points, setPoints] = useState([]);
  const [error, setError]   = useState(null);
  const [loaded, setLoaded] = useState(false);
  const mounted = useMounted();

  const refresh = useCallback(async (signal) => {
    try {
      const data = await fetchJson(`${BOT_API}/watch/portfolio/history?days=${days}`, signal);
      if (signal?.aborted || !mounted.current) return;
      const pts = (Array.isArray(data?.points) ? data.points : [])
        .filter((p) => isNum(p?.value_usd))
        .map((p) => ({ at: p.at, value: Number(p.value_usd) }))
        .sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
      setPoints(pts);
      setError(null);
      setLoaded(true);
    } catch (err) {
      if (err?.name === "AbortError" || !mounted.current) return;
      setError(formatApiError(err));
    }
  }, [days, mounted]);

  usePolling(refresh, SLOW_POLL_MS);

  const first = points[0]?.value;
  const current = points[points.length - 1]?.value;
  const delta = isNum(first) && isNum(current) ? current - first : null;
  const deltaPct = isNum(delta) && first > 0 ? (delta / first) * 100 : null;
  const color = delta === null || delta === 0 ? THEME.blue : delta > 0 ? THEME.green : THEME.red;
  const gradId = "veilleValueGrad";

  return (
    <div style={{
      background: "rgba(255,255,255,0.02)", border: `1px solid ${THEME.border}`,
      borderRadius: "16px", padding: "14px", marginBottom: "16px",
    }}>
      {sectionTitle("📈 Valeur du portefeuille", (
        <div style={{ display: "flex", gap: "4px" }}>
          {PERIODS.map(([d, l]) => (
            <button key={d} onClick={() => setDays(d)} style={pillStyle(days === d, THEME.purple)}>{l}</button>
          ))}
        </div>
      ))}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: "8px" }}>
        <div>
          <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px" }}>VALEUR ACTUELLE</div>
          <div style={{ fontSize: "22px", fontWeight: "800", color: THEME.text, lineHeight: 1.1 }}>{fmtUsd(current, 2)}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px" }}>SUR {PERIODS.find(([d]) => d === days)?.[1] || `${days} j`}</div>
          <div style={{ fontSize: "14px", fontWeight: "800", color }}>
            {fmtSigned(delta, 2, " $")}
            {deltaPct !== null && <span style={{ fontSize: "11px", marginLeft: "6px" }}>({fmtSigned(deltaPct, 2, " %")})</span>}
          </div>
        </div>
      </div>

      {error && <InfoBox color={THEME.red}>Historique inaccessible — {error}.</InfoBox>}
      {loaded && points.length < 2 && !error ? (
        <EmptyBox>Pas encore assez de points sur cette période (un snapshot par heure).</EmptyBox>
      ) : points.length >= 2 ? (
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={points} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="at" hide />
            <YAxis hide domain={["auto", "auto"]} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: THEME.border }} />
            <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill={`url(#${gradId})`} dot={false} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      ) : !error ? (
        <EmptyBox>Chargement…</EmptyBox>
      ) : null}
      {points.length >= 2 && (
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "9px", color: THEME.muted, marginTop: "4px" }}>
          <span>{fmtDay(points[0].at)}</span>
          <span>{points.length} points</span>
          <span>{fmtDay(points[points.length - 1].at)}</span>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. Allocation réelle vs cible
// ═══════════════════════════════════════════════════════════════════════════
function deltaColor(delta) {
  if (!isNum(delta)) return THEME.muted;
  const a = Math.abs(Number(delta));
  return a < 3 ? THEME.green : a < 6 ? THEME.yellow : THEME.red;
}

function AllocationRow({ c }) {
  const actual = isNum(c.actual_pct) ? Math.max(0, Number(c.actual_pct)) : 0;
  const target = isNum(c.target_pct) ? Number(c.target_pct) : null;
  const delta = isNum(c.delta_points) ? Number(c.delta_points) : (target !== null ? actual - target : null);
  const dColor = deltaColor(delta);
  return (
    <div style={{ marginBottom: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "5px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
          <span style={{ fontSize: "12px", fontWeight: "700", color: THEME.text }}>{CATEGORY_LABELS[c.category] || c.category}</span>
          <span style={{ fontSize: "10px", color: THEME.muted }}>{fmtUsd(c.value_usd)}</span>
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
          <span style={{ fontSize: "12px", fontWeight: "700", color: THEME.text }}>{fmtNum(actual, 1)} %</span>
          {target !== null ? (
            <>
              <span style={{ fontSize: "10px", color: THEME.muted }}>cible {fmtNum(target, 0)} %</span>
              {badge(fmtSigned(delta, 1, " pt"), dColor)}
            </>
          ) : (
            <span style={{ fontSize: "9px", color: THEME.muted }}>sans cible</span>
          )}
        </div>
      </div>
      <div style={{ position: "relative", height: "8px", borderRadius: "4px", background: "rgba(255,255,255,0.06)", overflow: "visible" }}>
        <div style={{
          width: `${Math.min(actual, 100)}%`, height: "100%", borderRadius: "4px",
          background: target === null ? THEME.text2 : dColor, transition: "width 0.6s ease",
          boxShadow: `0 0 8px ${(target === null ? THEME.text2 : dColor)}55`,
        }} />
        {target !== null && (
          <div title={`Cible ${fmtNum(target, 0)} %`} style={{
            position: "absolute", top: "-4px", left: `calc(${Math.min(Math.max(target, 0), 100)}% - 1px)`,
            width: "2px", height: "16px", borderRadius: "1px", background: THEME.text,
            boxShadow: "0 0 6px rgba(255,255,255,0.6)",
          }} />
        )}
      </div>
    </div>
  );
}

function AllocationSection() {
  const [data, setData]     = useState(null);
  const [error, setError]   = useState(null);
  const mounted = useMounted();

  const refresh = useCallback(async (signal) => {
    try {
      const d = await fetchJson(`${BOT_API}/watch/allocation`, signal);
      if (signal?.aborted || !mounted.current) return;
      setData(d);
      setError(null);
    } catch (err) {
      if (err?.name === "AbortError" || !mounted.current) return;
      setError(formatApiError(err));
    }
  }, [mounted]);

  usePolling(refresh, SLOW_POLL_MS);

  const cats = Array.isArray(data?.categories) ? data.categories : [];

  return (
    <div style={{
      background: "rgba(255,255,255,0.02)", border: `1px solid ${THEME.border}`,
      borderRadius: "16px", padding: "14px", marginBottom: "16px",
    }}>
      {sectionTitle("🧭 Allocation réelle vs cible", data && badge(`Total ${fmtUsd(data.total_usd)}`, THEME.cyan))}
      {error && <InfoBox color={THEME.red}>Allocation inaccessible — {error}.</InfoBox>}
      {!data && !error ? (
        <EmptyBox>Chargement…</EmptyBox>
      ) : data && cats.length === 0 ? (
        <EmptyBox>Aucune catégorie (pas encore de snapshot de portefeuille).</EmptyBox>
      ) : (
        cats.map((c) => <AllocationRow key={c.category} c={c} />)
      )}
      {cats.length > 0 && (
        <div style={{ fontSize: "9px", color: THEME.muted, lineHeight: 1.5 }}>
          Barre = part réelle · repère blanc = cible · écart en points :
          <span style={{ color: THEME.green }}> &lt; 3</span> ·
          <span style={{ color: THEME.yellow }}> &lt; 6</span> ·
          <span style={{ color: THEME.red }}> ≥ 6</span>.
          Les cibles viennent des règles « Allocation » actives.
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. Sentiment Fear & Greed
// ═══════════════════════════════════════════════════════════════════════════
function fngColor(value) {
  const v = Number(value);
  if (!isNum(value)) return THEME.muted;
  if (v < 25) return THEME.red;
  if (v < 45) return THEME.yellow;
  if (v < 55) return THEME.text2;
  if (v < 75) return THEME.green;
  return THEME.cyan;
}

function fngLabelFr(classification, value) {
  const map = {
    "extreme fear": "Peur extrême", fear: "Peur", neutral: "Neutre",
    greed: "Avidité", "extreme greed": "Avidité extrême",
  };
  const k = String(classification || "").toLowerCase();
  if (map[k]) return map[k];
  const v = Number(value);
  if (!isNum(value)) return "—";
  return v < 25 ? "Peur extrême" : v < 45 ? "Peur" : v < 55 ? "Neutre" : v < 75 ? "Avidité" : "Avidité extrême";
}

function FngSection() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  const mounted = useMounted();

  const refresh = useCallback(async (signal) => {
    try {
      const d = await fetchJson(`${BOT_API}/watch/fng`, signal);
      if (signal?.aborted || !mounted.current) return;
      setData(d);
      setError(null);
    } catch (err) {
      if (err?.name === "AbortError" || !mounted.current) return;
      setError(formatApiError(err));
    }
  }, [mounted]);

  usePolling(refresh, SLOW_POLL_MS);

  const history = useMemo(() => {
    const h = Array.isArray(data?.history) ? data.history.filter((x) => isNum(x?.value)) : [];
    h.sort((a, b) => String(a.date).localeCompare(String(b.date)));
    return h.slice(-14);
  }, [data]);

  const value = data?.value;
  const color = fngColor(value);

  return (
    <div style={{
      background: "rgba(255,255,255,0.02)", border: `1px solid ${THEME.border}`,
      borderRadius: "16px", padding: "14px", marginBottom: "16px",
    }}>
      {sectionTitle("🌡️ Sentiment Fear & Greed", data?.date && <span style={{ fontSize: "9px", color: THEME.muted }}>{data.date}</span>)}
      {error && <InfoBox color={THEME.red}>Sentiment inaccessible — {error}.</InfoBox>}
      {!data && !error ? (
        <EmptyBox>Chargement…</EmptyBox>
      ) : data ? (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "12px" }}>
            <div style={{
              width: "64px", height: "64px", borderRadius: "50%", flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: color + "18", border: `2px solid ${color}`, boxShadow: `0 0 18px ${color}40`,
              fontSize: "22px", fontWeight: "800", color,
            }}>{isNum(value) ? Math.round(Number(value)) : "—"}</div>
            <div>
              <div style={{ fontSize: "16px", fontWeight: "800", color }}>{fngLabelFr(data.classification, value)}</div>
              <div style={{ fontSize: "10px", color: THEME.muted, marginTop: "2px" }}>
                {data.classification || "—"} · échelle 0 (peur) → 100 (avidité)
              </div>
            </div>
          </div>

          {history.length > 0 ? (
            <>
              <div style={{ fontSize: "8px", color: THEME.muted, letterSpacing: "1px", marginBottom: "4px" }}>14 DERNIERS JOURS</div>
              <div style={{ display: "flex", alignItems: "flex-end", gap: "3px", height: "44px" }}>
                {history.map((h) => {
                  const c = fngColor(h.value);
                  const pct = Math.max(6, Math.min(100, Number(h.value)));
                  return (
                    <div key={h.date} title={`${h.date} · ${h.value} · ${h.classification || ""}`} style={{
                      flex: 1, height: `${pct}%`, borderRadius: "3px 3px 0 0",
                      background: c, opacity: h.date === data.date ? 1 : 0.7,
                    }} />
                  );
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "9px", color: THEME.muted, marginTop: "3px" }}>
                <span>{fmtDay(history[0].date)}</span>
                <span>{fmtDay(history[history.length - 1].date)}</span>
              </div>
            </>
          ) : (
            <div style={{ fontSize: "10px", color: THEME.muted }}>Historique indisponible.</div>
          )}
        </>
      ) : null}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Onglet
// ═══════════════════════════════════════════════════════════════════════════
export default function Veille() {
  return (
    <div style={{ padding: "16px 16px 90px" }}>
      <AlertsSection />
      <RulesSection />
      <PortfolioSection />
      <AllocationSection />
      <FngSection />

      <div style={{
        background: "rgba(245,158,11,0.05)", border: "1px solid rgba(245,158,11,0.2)",
        borderRadius: "14px", padding: "14px",
      }}>
        <div style={{ fontSize: "9px", color: THEME.yellow, letterSpacing: "2px", fontWeight: "700", marginBottom: "6px" }}>
          🛡️ RAPPEL
        </div>
        <div style={{ fontSize: "11px", color: THEME.text2, lineHeight: 1.5 }}>
          Les alertes sont à valider manuellement, aucun ordre automatique.
        </div>
      </div>
    </div>
  );
}
