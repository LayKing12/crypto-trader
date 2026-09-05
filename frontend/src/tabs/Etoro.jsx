import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { THEME } from "../theme";

const BOT_API = "https://crypto-trader-production-8ef4.up.railway.app";

const POLL_MS           = 5000;   // status + décisions
const POSITIONS_POLL_MS = 30000;  // positions ouvertes
const DECISIONS_LIMIT   = 100;
const HIGHLIGHT_MS      = 2500;
const MAX_POSITIONS_DEFAULT = 3;  // ETORO_MAX_OPEN_POSITIONS côté moteur

// ── Libellés ───────────────────────────────────────────────────────────────
export const REASON_LABELS = {
  ok: "Conditions remplies",
  kill_switch: "Kill switch activé",
  breaker_active: "Circuit breaker actif",
  real_mode_without_rotation: "Mode réel sans rotation des identifiants",
  score_below_min: "Score sous le minimum",
  missing_sl_tp: "Stop loss / take profit manquant",
  invalid_sl_tp: "Stop loss / take profit invalide",
  leverage_too_high: "Levier trop élevé",
  amount_too_large: "Montant trop élevé",
  instrument_not_in_universe: "Instrument hors univers",
  max_positions_reached: "Nombre max de positions atteint",
  position_already_open: "Position déjà ouverte",
  cooldown_active: "Délai de refroidissement actif",
  rankings_not_confirmed: "Non confirmé par les rankings",
  negative_sentiment: "Sentiment négatif",
  execution_error: "Erreur d'exécution",
  received: "Signal reçu",
  opened: "Position ouverte",
  closed: "Position fermée",
  breaker_tripped: "Circuit breaker déclenché",
};

const KIND_LABELS = {
  signal: "Signal",
  skip: "Ignoré",
  open: "Ouverture",
  close: "Fermeture",
  breaker: "Breaker",
  kill_switch: "Kill switch",
  error: "Erreur",
  sync: "Sync",
};

const KIND_OPTIONS = [
  ["", "Tous les types"],
  ["signal", "Signal"],
  ["skip", "Ignoré"],
  ["open", "Ouverture"],
  ["close", "Fermeture"],
  ["breaker", "Breaker"],
  ["kill_switch", "Kill switch"],
  ["error", "Erreur"],
];

function kindColor(kind) {
  switch (kind) {
    case "open":        return THEME.green;
    case "close":       return THEME.blue;
    case "skip":        return THEME.muted;
    case "signal":      return THEME.yellow;
    case "breaker":
    case "kill_switch":
    case "error":       return THEME.red;
    default:            return THEME.text2;
  }
}

// ── Formatage ──────────────────────────────────────────────────────────────
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleTimeString("fr", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("fr", { day: "2-digit", month: "2-digit" })
    + " " + d.toLocaleTimeString("fr", { hour: "2-digit", minute: "2-digit" });
}

function isNum(v) {
  return v !== null && v !== undefined && !Number.isNaN(Number(v));
}

function fmtNum(v, digits = 2) {
  return isNum(v) ? Number(v).toFixed(digits) : "—";
}

function fmtMoney(v, digits = 2) {
  if (!isNum(v)) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)} $`;
}

function fmtPct(v) {
  if (!isNum(v)) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)} %`;
}

function pnlColor(v) {
  const n = Number(v);
  if (!isNum(v) || n === 0) return THEME.text2;
  return n > 0 ? THEME.green : THEME.red;
}

function reasonLabel(reason) {
  if (!reason) return "—";
  return REASON_LABELS[reason] || String(reason).replace(/_/g, " ");
}

function actionDetails(d) {
  const parts = [];
  if (isNum(d.amount))           parts.push(`montant ${fmtNum(d.amount)} $`);
  if (isNum(d.entry_rate))       parts.push(`entrée ${fmtNum(d.entry_rate, 4)}`);
  if (isNum(d.stop_loss_rate))   parts.push(`SL ${fmtNum(d.stop_loss_rate, 4)}`);
  if (isNum(d.take_profit_rate)) parts.push(`TP ${fmtNum(d.take_profit_rate, 4)}`);
  if (isNum(d.realized_pnl))     parts.push(`PnL ${fmtMoney(d.realized_pnl)}`);
  return parts;
}

async function fetchJson(url, signal, options = {}) {
  const res = await fetch(url, { signal, headers: { Accept: "application/json" }, ...options });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Briques de style CryptoMind ────────────────────────────────────────────
function badge(val, color) {
  return (
    <span style={{
      fontSize: "10px", padding: "2px 10px", borderRadius: "20px",
      background: color + "18", color, border: `1px solid ${color}35`,
      fontWeight: "600",
    }}>{val}</span>
  );
}

function sectionTitle(text, right) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      marginBottom: "10px",
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
    borderRadius: "12px", padding: "12px 14px",
    color: THEME.text, fontSize: "14px", fontFamily: "inherit",
    outline: "none", appearance: "none",
  };
}

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{
      background: THEME.glass, borderRadius: "12px", padding: "12px",
      border: `1px solid ${THEME.border}`,
    }}>
      <div style={{ fontSize: "9px", color: THEME.muted, marginBottom: "5px", letterSpacing: "1px" }}>{label}</div>
      <div style={{ fontSize: "17px", fontWeight: "800", color: color || THEME.text, lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: "10px", color: THEME.text2, marginTop: "4px" }}>{sub}</div>}
    </div>
  );
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

// ── Cartes ─────────────────────────────────────────────────────────────────
function PositionCard({ p }) {
  const isBuy = String(p.side || "").toUpperCase() === "BUY";
  const sideColor = isBuy ? THEME.green : THEME.red;
  const color = pnlColor(p.unrealized_pnl);
  return (
    <div style={{
      background: THEME.glass, border: `1px solid ${THEME.border}`,
      borderLeft: `3px solid ${color}`, borderRadius: "16px",
      padding: "12px 14px", marginBottom: "10px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "14px", fontWeight: "700", color: THEME.text }}>{p.symbol || "—"}</span>
          {p.side && badge(isBuy ? "▲ BUY" : "▼ SELL", sideColor)}
        </div>
        <span style={{ fontSize: "14px", fontWeight: "800", color }}>{fmtMoney(p.unrealized_pnl)}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "6px" }}>
        {[
          { l: "MONTANT", v: isNum(p.amount) ? `${fmtNum(p.amount)} $` : "—" },
          { l: "OUVERTURE", v: fmtNum(p.open_rate, 4) },
          { l: "SL", v: fmtNum(p.stop_loss_rate, 4), c: THEME.red },
          { l: "TP", v: fmtNum(p.take_profit_rate, 4), c: THEME.green },
        ].map((x) => (
          <div key={x.l}>
            <div style={{ fontSize: "8px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>{x.l}</div>
            <div style={{ fontSize: "11px", fontWeight: "600", color: x.c || THEME.text }}>{x.v}</div>
          </div>
        ))}
      </div>
      {p.opened_at && (
        <div style={{ fontSize: "9px", color: THEME.muted, marginTop: "6px" }}>Ouverte le {fmtDateTime(p.opened_at)}</div>
      )}
    </div>
  );
}

function DecisionCard({ d, isNew }) {
  const color = kindColor(d.kind);
  const side = d.side ? String(d.side).toUpperCase() : "";
  const sideColor = side === "BUY" ? THEME.green : THEME.red;
  const details = actionDetails(d);
  return (
    <div style={{
      background: isNew ? color + "14" : THEME.glass,
      border: `1px solid ${isNew ? color + "60" : THEME.border}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: "16px", padding: "12px 14px", marginBottom: "10px",
      boxShadow: isNew ? `0 0 16px ${color}30` : "none",
      transition: "background 0.6s ease, border-color 0.6s ease, box-shadow 0.6s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{
            width: "8px", height: "8px", borderRadius: "50%", background: color,
            boxShadow: `0 0 8px ${color}`, flexShrink: 0,
          }} />
          <span style={{ fontSize: "10px", fontWeight: "700", color, letterSpacing: "1px", textTransform: "uppercase" }}>
            {KIND_LABELS[d.kind] || d.kind || "—"}
          </span>
          {isNew && badge("NOUVEAU", color)}
        </div>
        <span style={{ fontSize: "10px", color: THEME.muted }} title={fmtDateTime(d.at)}>{fmtTime(d.at)}</span>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "14px", fontWeight: "700", color: THEME.text }}>{d.symbol || "—"}</span>
          {side && badge(side === "BUY" ? "▲ BUY" : "▼ SELL", sideColor)}
        </div>
        <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", justifyContent: "flex-end" }}>
          {isNum(d.market_score) && badge(`Score ${fmtNum(d.market_score, 0)}`, THEME.purple)}
          {isNum(d.rankings_confirmation) && badge(`Conf. ${fmtNum(d.rankings_confirmation, 2)}`, THEME.cyan)}
          {isNum(d.sentiment) && badge(`Sent. ${fmtNum(d.sentiment, 2)}`, Number(d.sentiment) < 0 ? THEME.red : THEME.green)}
        </div>
      </div>

      <div style={{ fontSize: "11px", color: THEME.text2, marginBottom: details.length || d.action ? "4px" : 0 }}>
        <span style={{ color: THEME.muted }}>Raison · </span>{reasonLabel(d.reason)}
      </div>
      {d.action && (
        <div style={{ fontSize: "11px", color: THEME.text }}>
          <span style={{ color: THEME.muted }}>Action · </span>{d.action}
        </div>
      )}
      {details.length > 0 && (
        <div style={{ fontSize: "10px", color: THEME.muted, marginTop: "4px" }}>{details.join(" · ")}</div>
      )}
    </div>
  );
}

// ── Onglet ─────────────────────────────────────────────────────────────────
export default function Etoro() {
  const [status, setStatus]             = useState(null);
  const [decisions, setDecisions]       = useState([]);
  const [logAvailable, setLogAvailable] = useState(true);
  const [positions, setPositions]       = useState([]);
  const [posAvailable, setPosAvailable] = useState(true);
  const [posDetail, setPosDetail]       = useState("");
  const [error, setError]               = useState(null);
  const [lastUpdate, setLastUpdate]     = useState(null);
  const [paused, setPaused]             = useState(false);
  const [kindFilter, setKindFilter]     = useState("");
  const [symbolInput, setSymbolInput]   = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [highlighted, setHighlighted]   = useState(() => new Set());
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMsg, setActionMsg]       = useState(null); // { color, text }

  const knownIds        = useRef(new Set());
  const firstLoad       = useRef(true);
  const highlightTimers = useRef([]);

  // Debounce du filtre symbole
  useEffect(() => {
    const t = setTimeout(() => setSymbolFilter(symbolInput.trim().toUpperCase()), 300);
    return () => clearTimeout(t);
  }, [symbolInput]);

  // Changement de filtre : les lignes reçues ne sont plus « nouvelles »
  useEffect(() => {
    firstLoad.current = true;
    knownIds.current = new Set();
  }, [kindFilter, symbolFilter]);

  const decisionsUrl = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", String(DECISIONS_LIMIT));
    params.set("kind", kindFilter);
    params.set("symbol", symbolFilter);
    return `${BOT_API}/etoro/decisions?${params.toString()}`;
  }, [kindFilter, symbolFilter]);

  const refresh = useCallback(async (signal) => {
    try {
      const [st, dec] = await Promise.all([
        fetchJson(`${BOT_API}/etoro/status`, signal),
        fetchJson(decisionsUrl, signal),
      ]);
      if (signal?.aborted) return;

      const list = Array.isArray(dec?.decisions) ? dec.decisions : [];
      list.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());

      const fresh = [];
      for (const d of list) {
        if (d?.id && !knownIds.current.has(d.id)) {
          knownIds.current.add(d.id);
          if (!firstLoad.current) fresh.push(d.id);
        }
      }
      firstLoad.current = false;

      setStatus(st);
      setDecisions(list);
      setLogAvailable(dec?.available !== false);
      setError(null);
      setLastUpdate(new Date());

      if (fresh.length > 0) {
        setHighlighted((prev) => { const n = new Set(prev); fresh.forEach((id) => n.add(id)); return n; });
        const timer = setTimeout(() => {
          setHighlighted((prev) => { const n = new Set(prev); fresh.forEach((id) => n.delete(id)); return n; });
        }, HIGHLIGHT_MS);
        highlightTimers.current.push(timer);
      }
    } catch (err) {
      if (err?.name === "AbortError") return;
      setError(err?.message || String(err));
    }
  }, [decisionsUrl]);

  const refreshPositions = useCallback(async (signal) => {
    try {
      const data = await fetchJson(`${BOT_API}/etoro/positions`, signal);
      if (signal?.aborted) return;
      setPosAvailable(data?.available !== false);
      setPosDetail(data?.detail || "");
      setPositions(Array.isArray(data?.positions) ? data.positions : []);
    } catch (err) {
      if (err?.name === "AbortError") return;
      // On garde la dernière liste connue : l'erreur réseau est déjà affichée via le statut.
    }
  }, []);

  // Polling status + décisions (5 s), pause possible
  useEffect(() => {
    if (paused) return undefined;
    const controller = new AbortController();
    refresh(controller.signal);
    const iv = setInterval(() => refresh(controller.signal), POLL_MS);
    return () => { clearInterval(iv); controller.abort(); };
  }, [refresh, paused]);

  // Polling positions (30 s)
  useEffect(() => {
    if (paused) return undefined;
    const controller = new AbortController();
    refreshPositions(controller.signal);
    const iv = setInterval(() => refreshPositions(controller.signal), POSITIONS_POLL_MS);
    return () => { clearInterval(iv); controller.abort(); };
  }, [refreshPositions, paused]);

  // Purge des timers de surlignage au démontage
  useEffect(() => {
    const timers = highlightTimers.current;
    return () => { timers.forEach((t) => clearTimeout(t)); };
  }, []);

  // ── Kill switch ──
  const agentActive = Boolean(status && status.agent_enabled && !status.kill_switch_active);

  const toggleAgent = async () => {
    if (!status) return;
    const killing = agentActive;
    if (killing && !window.confirm("⚠️ Couper l'agent eToro ?\nAucune nouvelle position ne sera ouverte (les positions existantes restent ouvertes).")) return;
    const token = window.prompt(killing ? "Token de sécurité (X-Kill-Token) pour couper l'agent :" : "Token de sécurité (X-Kill-Token) pour relancer l'agent :");
    if (token === null) return;
    if (!token.trim()) { setActionMsg({ color: THEME.red, text: "✗ Token vide, action annulée." }); return; }

    setActionLoading(true); setActionMsg(null);
    try {
      const res = await fetch(`${BOT_API}${killing ? "/etoro/kill" : "/etoro/resume"}`, {
        method: "POST",
        headers: { "X-Kill-Token": token.trim(), "X-Kill-Actor": "cryptomind-app", Accept: "application/json" },
      });
      if (!res.ok) {
        const msg = res.status === 401 || res.status === 403 ? "Token refusé" : `HTTP ${res.status}`;
        throw new Error(msg);
      }
      const data = await res.json();
      setActionMsg({
        color: data.kill_switch ? THEME.red : THEME.green,
        text: data.kill_switch ? "⏹ Agent coupé — kill switch activé." : "▶ Agent relancé — kill switch désactivé.",
      });
      await refresh();
    } catch (e) {
      setActionMsg({ color: THEME.red, text: `✗ Échec : ${e.message || e}` });
    }
    setActionLoading(false);
  };

  // ── Dérivés du statut ──
  const snapshot     = status?.snapshot || {};
  const isReal       = status?.mode === "real";
  const modeLabel    = status ? String(status.mode || "demo").toUpperCase() : "—";
  const breakerActive = Boolean(status?.breaker_active);
  const dailyPnl     = snapshot.daily_pnl;
  const equityStart  = Number(snapshot.equity_start_of_day);
  const dailyPnlPct  = isNum(dailyPnl) && equityStart > 0 ? (Number(dailyPnl) / equityStart) * 100 : null;
  const openCount    = isNum(snapshot.open_positions_count)
    ? Number(snapshot.open_positions_count)
    : snapshot.open_positions_by_instrument
      ? Object.keys(snapshot.open_positions_by_instrument).length
      : (posAvailable ? positions.length : null);
  const maxPositions = isNum(status?.max_open_positions) ? Number(status.max_open_positions) : MAX_POSITIONS_DEFAULT;
  const realLocked   = Boolean(status?.real_mode_locked);
  const engineDown   = Boolean(error);
  const statusColor  = !status ? THEME.yellow : agentActive ? THEME.green : THEME.red;

  return (
    <div style={{ padding: "16px 16px 90px" }}>

      {/* ── En-tête agent ── */}
      <div style={{
        background: agentActive
          ? "linear-gradient(135deg, rgba(16,185,129,0.08), rgba(6,182,212,0.05))"
          : "linear-gradient(135deg, rgba(239,68,68,0.06), rgba(245,158,11,0.04))",
        border: `1px solid ${agentActive ? "rgba(16,185,129,0.25)" : "rgba(239,68,68,0.2)"}`,
        borderRadius: "20px", padding: "18px", marginBottom: "16px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "14px" }}>
          <div>
            <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "6px" }}>
              🥇 AGENT ETORO
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
              <div style={{
                width: "10px", height: "10px", borderRadius: "50%", background: statusColor,
                boxShadow: agentActive ? `0 0 10px ${THEME.green}` : "none",
                animation: agentActive ? "pulse 2s infinite" : "none",
              }} />
              <span style={{ fontSize: "18px", fontWeight: "800", color: statusColor }}>
                {!status ? (engineDown ? "INJOIGNABLE" : "Connexion…") : agentActive ? "EN MARCHE" : "COUPÉ"}
              </span>
            </div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {status && badge(modeLabel, isReal ? THEME.red : THEME.blue)}
              {status && badge(agentActive ? "ACTIF" : "COUPÉ", agentActive ? THEME.green : THEME.red)}
              {status && realLocked && badge("🔒 RÉEL VERROUILLÉ", THEME.yellow)}
            </div>
            {status?.kill_switch_active && status.kill_switch_actor && (
              <div style={{ fontSize: "10px", color: THEME.muted, marginTop: "6px" }}>
                Kill switch par <span style={{ color: THEME.text2 }}>{status.kill_switch_actor}</span>
              </div>
            )}
            {status && !status.agent_enabled && (
              <div style={{ fontSize: "10px", color: THEME.muted, marginTop: "6px" }}>
                ETORO_AGENT_ENABLED=false côté moteur : relance impossible depuis l'app.
              </div>
            )}
          </div>
          <div style={{ textAlign: "right", fontSize: "9px", color: THEME.muted, lineHeight: 1.6 }}>
            {lastUpdate ? `MAJ ${lastUpdate.toLocaleTimeString("fr")}` : "En attente…"}
            {paused && <div style={{ color: THEME.yellow }}>⏸ EN PAUSE</div>}
          </div>
        </div>

        <button onClick={toggleAgent} disabled={actionLoading || !status || (!agentActive && !status.agent_enabled)} style={{
          width: "100%", padding: "14px", borderRadius: "14px", cursor: "pointer",
          fontFamily: "inherit", fontSize: "14px", fontWeight: "800",
          letterSpacing: "1px", border: "none", transition: "all 0.2s",
          background: agentActive ? "rgba(239,68,68,0.2)" : THEME.gradP,
          color: agentActive ? THEME.red : "#fff",
          boxShadow: agentActive ? "0 0 20px rgba(239,68,68,0.2)" : "0 0 24px rgba(139,92,246,0.4)",
          opacity: (actionLoading || !status || (!agentActive && !status.agent_enabled)) ? 0.6 : 1,
          minHeight: "50px",
        }}>
          {actionLoading ? "⟳ Envoi…" : agentActive ? "⏹ COUPER L'AGENT" : "▶ RELANCER"}
        </button>

        {actionMsg && (
          <div style={{
            marginTop: "10px", padding: "8px 12px", borderRadius: "10px", fontSize: "11px",
            background: actionMsg.color + "14", border: `1px solid ${actionMsg.color}35`, color: actionMsg.color,
          }}>{actionMsg.text}</div>
        )}
      </div>

      {engineDown && (
        <InfoBox color={THEME.red}>
          <strong>Moteur eToro injoignable</strong> — {error}. Nouvelle tentative toutes les 5 s.
        </InfoBox>
      )}
      {status && !isReal && (
        <InfoBox color={THEME.blue}>📝 MODE DEMO — compte virtuel eToro, aucun risque réel</InfoBox>
      )}
      {status && isReal && realLocked && (
        <InfoBox color={THEME.yellow}>🔒 Mode réel verrouillé : identifiants non rotés, aucune ouverture possible.</InfoBox>
      )}

      {/* ── Stats ── */}
      <div style={{
        background: "rgba(255,255,255,0.02)", border: `1px solid ${THEME.border}`,
        borderRadius: "16px", padding: "14px", marginBottom: "16px",
      }}>
        {sectionTitle("📊 État de l'agent", snapshot.daily_pnl_date && (
          <span style={{ fontSize: "9px", color: THEME.muted }}>jour {snapshot.daily_pnl_date}</span>
        ))}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
          <StatCard
            label="PNL DU JOUR"
            value={fmtMoney(dailyPnl)}
            sub={dailyPnlPct === null ? undefined : fmtPct(dailyPnlPct)}
            color={pnlColor(dailyPnl)}
          />
          <StatCard
            label="CIRCUIT BREAKER"
            value={!status ? "—" : breakerActive ? "DÉCLENCHÉ" : "OK"}
            sub={breakerActive && status?.breaker_until ? `jusqu'à ${fmtDateTime(status.breaker_until)}` : (status ? "seuil −3 % / jour" : undefined)}
            color={!status ? THEME.muted : breakerActive ? THEME.red : THEME.green}
          />
          <StatCard
            label="POSITIONS OUVERTES"
            value={openCount === null || openCount === undefined ? "—" : `${openCount} / ${maxPositions}`}
            sub={openCount !== null && openCount >= maxPositions ? "limite atteinte" : undefined}
            color={openCount !== null && openCount >= maxPositions ? THEME.yellow : THEME.text}
          />
          <StatCard
            label="MODE RÉEL"
            value={!status ? "—" : isReal ? (realLocked ? "VERROUILLÉ" : "ACTIF") : "INACTIF"}
            sub={!status ? undefined : isReal ? (realLocked ? "rotation requise" : "argent réel") : "compte demo"}
            color={!status ? THEME.muted : isReal ? (realLocked ? THEME.yellow : THEME.red) : THEME.blue}
          />
        </div>
      </div>

      {/* ── Positions ── */}
      <div style={{ marginBottom: "16px" }}>
        {sectionTitle("💼 Positions ouvertes", badge(`${positions.length}`, THEME.purple))}
        {!posAvailable ? (
          <InfoBox color={THEME.yellow}>Positions indisponibles{posDetail ? ` — ${posDetail}` : ""}.</InfoBox>
        ) : positions.length === 0 ? (
          <div style={{
            background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "16px",
            padding: "18px", textAlign: "center", fontSize: "11px", color: THEME.muted,
          }}>Aucune position ouverte.</div>
        ) : (
          positions.map((p) => <PositionCard key={p.position_id || `${p.symbol}-${p.opened_at}`} p={p} />)
        )}
      </div>

      {/* ── Décisions en direct ── */}
      <div style={{ marginBottom: "16px" }}>
        {sectionTitle("⚡ Décisions en direct", (
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            {badge(`${decisions.length} décision${decisions.length > 1 ? "s" : ""}`, THEME.cyan)}
            <button onClick={() => setPaused((p) => !p)} style={{
              padding: "3px 10px", borderRadius: "20px", cursor: "pointer", fontFamily: "inherit",
              fontSize: "10px", fontWeight: "700",
              background: paused ? THEME.yellow + "18" : THEME.glass,
              color: paused ? THEME.yellow : THEME.muted,
              border: `1px solid ${paused ? THEME.yellow + "35" : THEME.border}`,
            }}>{paused ? "▶ Reprendre" : "⏸ Pause"}</button>
          </div>
        ))}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "12px" }}>
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)} style={{
            ...inputStyle(kindFilter ? THEME.purple : undefined),
            color: kindFilter ? THEME.purple : THEME.text2,
            backgroundImage: "none",
          }}>
            {KIND_OPTIONS.map(([value, label]) => (
              <option key={value || "all"} value={value} style={{ background: THEME.bg2, color: THEME.text }}>{label}</option>
            ))}
          </select>
          <input
            type="text" value={symbolInput} onChange={(e) => setSymbolInput(e.target.value)}
            placeholder="Symbole (AAPL…)" autoComplete="off" spellCheck={false} autoCapitalize="characters"
            style={inputStyle(symbolFilter ? THEME.purple : undefined)}
          />
        </div>

        {!logAvailable ? (
          <InfoBox color={THEME.yellow}>Journal des décisions indisponible côté moteur (available = false).</InfoBox>
        ) : decisions.length === 0 ? (
          <div style={{
            background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "16px",
            padding: "18px", textAlign: "center", fontSize: "11px", color: THEME.muted,
          }}>
            {status ? "Aucune décision pour ces filtres." : engineDown ? "Journal inaccessible : moteur injoignable." : "Chargement…"}
          </div>
        ) : (
          decisions.map((d, i) => <DecisionCard key={d.id || `${d.at}-${i}`} d={d} isNew={highlighted.has(d.id)} />)
        )}
      </div>

      {/* ── Garde-fous ── */}
      <div style={{
        background: "rgba(245,158,11,0.05)", border: "1px solid rgba(245,158,11,0.2)",
        borderRadius: "14px", padding: "14px",
      }}>
        <div style={{ fontSize: "9px", color: THEME.yellow, letterSpacing: "2px", fontWeight: "700", marginBottom: "8px" }}>
          🛡️ GARDE-FOUS DE L'AGENT
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 10px", fontSize: "10px", color: THEME.text2, lineHeight: 1.5 }}>
          <div>• Max <strong style={{ color: THEME.text }}>{maxPositions} positions</strong>, 1 par instrument</div>
          <div>• Cooldown <strong style={{ color: THEME.text }}>4 h</strong> par instrument</div>
          <div>• <strong style={{ color: THEME.text }}>SL / TP obligatoires</strong> sur chaque ordre</div>
          <div>• Breaker à <strong style={{ color: THEME.red }}>−3 % du jour</strong> → pause 24 h</div>
        </div>
      </div>
    </div>
  );
}
