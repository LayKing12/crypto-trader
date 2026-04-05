import { useState, useEffect } from "react";
import { api } from "../api";
import { calcRSI, calcScore, calcExitLevels, fibFromHistory } from "../utils/technical";
import { calcPositionSize, calcStopLoss, validateTrade, logTrade } from "../utils/risk";
import ScoreRing from "../components/ScoreRing";
import { THEME } from "../theme";

const BOT_API = "http://localhost:8000";

const PAIRS = [
  { ws: "XBT/USD",  kraken: "XBTUSD",  name: "BTC",  symbol: "₿",  minVol: 0.0001, decimals: 6 },
  { ws: "ETH/USD",  kraken: "ETHUSD",  name: "ETH",  symbol: "Ξ",  minVol: 0.002,  decimals: 4 },
  { ws: "SOL/USD",  kraken: "SOLUSD",  name: "SOL",  symbol: "◎",  minVol: 0.5,    decimals: 2 },
  { ws: "ADA/USD",  kraken: "ADAUSD",  name: "ADA",  symbol: "₳",  minVol: 10,     decimals: 0 },
  { ws: "DOT/USD",  kraken: "DOTUSD",  name: "DOT",  symbol: "●",  minVol: 1,      decimals: 2 },
  { ws: "XRP/USD",  kraken: "XRPUSD",  name: "XRP",  symbol: "✕",  minVol: 10,     decimals: 0 },
  { ws: "LINK/USD", kraken: "LINKUSD", name: "LINK", symbol: "⬡",  minVol: 0.1,    decimals: 2 },
  { ws: "LTC/USD",  kraken: "LTCUSD",  name: "LTC",  symbol: "Ł",  minVol: 0.1,    decimals: 2 },
  { ws: "AVAX/USD", kraken: "AVAXUSD", name: "AVAX", symbol: "△",  minVol: 0.1,    decimals: 2 },
  { ws: "NEAR/USD", kraken: "NEARUSD", name: "NEAR", symbol: "Ν",  minVol: 1,      decimals: 1 },
];

function inputStyle(accent) {
  return {
    width: "100%", background: "rgba(255,255,255,0.04)",
    border: `1px solid ${accent || THEME.border}`,
    borderRadius: "12px", padding: "14px 16px",
    color: THEME.text, fontSize: "16px", fontFamily: "inherit",
    outline: "none", appearance: "none",
  };
}

function badge(val, color) {
  return (
    <span style={{
      fontSize: "10px", padding: "2px 10px", borderRadius: "20px",
      background: color + "18", color, border: `1px solid ${color}35`,
      fontWeight: "600",
    }}>{val}</span>
  );
}

function getStoredPortfolio() {
  try { return parseFloat(localStorage.getItem("portfolio_eur") || "0") || 0; }
  catch { return 0; }
}

const WIN_RATE_TARGET = 60;   // % objectif pour passer en live
const TRADES_TARGET   = 100;  // nombre de trades minimum

function TrainingDashboard({ stats, recentTrades }) {
  if (!stats) return null;

  // win_rate vient du backend (0–100)
  const winRatePct   = stats.win_rate ?? 0;
  const totalTrades  = stats.total_trades ?? 0;
  const isReady      = winRatePct >= WIN_RATE_TARGET && totalTrades >= TRADES_TARGET;
  const tradesLeft   = Math.max(0, TRADES_TARGET - totalTrades);
  const progressWin  = Math.min(100, (winRatePct / WIN_RATE_TARGET) * 100);
  const progressTrades = Math.min(100, (totalTrades / TRADES_TARGET) * 100);

  return (
    <div style={{
      marginTop: "14px",
      background: "rgba(255,255,255,0.02)",
      border: `1px solid ${THEME.border}`,
      borderRadius: "16px", padding: "14px",
    }}>
      {/* Titre + badge PRÊT POUR LE LIVE */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <span style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "2px", fontWeight: "700" }}>
          📊 ENTRAÎNEMENT DU BOT
        </span>
        {isReady ? (
          <span style={{
            fontSize: "9px", padding: "4px 10px", borderRadius: "20px",
            background: "rgba(16,185,129,0.15)", color: THEME.green,
            border: "1px solid rgba(16,185,129,0.4)", fontWeight: "800",
            letterSpacing: "1px",
            boxShadow: "0 0 12px rgba(16,185,129,0.3)",
          }}>
            ✓ PRÊT POUR LE LIVE
          </span>
        ) : (
          <span style={{
            fontSize: "9px", padding: "4px 10px", borderRadius: "20px",
            background: "rgba(245,158,11,0.1)", color: THEME.yellow,
            border: "1px solid rgba(245,158,11,0.3)", fontWeight: "700",
          }}>
            EN ENTRAÎNEMENT
          </span>
        )}
      </div>

      {/* Barre win rate */}
      <div style={{ marginBottom: "10px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "5px" }}>
          <span style={{ fontSize: "10px", color: THEME.muted }}>WIN RATE</span>
          <span style={{ fontSize: "10px", fontWeight: "700",
            color: winRatePct >= WIN_RATE_TARGET ? THEME.green : winRatePct >= 50 ? THEME.yellow : THEME.red,
          }}>
            {winRatePct.toFixed(0)}% → objectif {WIN_RATE_TARGET}%
          </span>
        </div>
        <div style={{ height: "8px", borderRadius: "4px", background: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
          <div style={{
            height: "100%", borderRadius: "4px",
            width: `${progressWin}%`,
            background: winRatePct >= WIN_RATE_TARGET
              ? "linear-gradient(90deg, #10b981, #06b6d4)"
              : winRatePct >= 50
              ? "linear-gradient(90deg, #f59e0b, #eab308)"
              : "linear-gradient(90deg, #ef4444, #f97316)",
            transition: "width 0.6s ease",
          }} />
        </div>
      </div>

      {/* Barre trades */}
      <div style={{ marginBottom: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "5px" }}>
          <span style={{ fontSize: "10px", color: THEME.muted }}>TRADES EFFECTUÉS</span>
          <span style={{ fontSize: "10px", fontWeight: "700", color: THEME.text }}>
            {totalTrades}/{TRADES_TARGET}
            {tradesLeft > 0 && (
              <span style={{ color: THEME.muted, fontWeight: "400" }}> ({tradesLeft} restants)</span>
            )}
          </span>
        </div>
        <div style={{ height: "8px", borderRadius: "4px", background: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
          <div style={{
            height: "100%", borderRadius: "4px",
            width: `${progressTrades}%`,
            background: "linear-gradient(90deg, #8b5cf6, #3b82f6)",
            transition: "width 0.6s ease",
          }} />
        </div>
      </div>

      {/* Derniers 3 trades */}
      {recentTrades?.length > 0 && (
        <div>
          <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "6px" }}>
            DERNIERS TRADES
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            {recentTrades.slice(0, 3).map((t) => {
              const isWin = t.result === "win";
              const isOpen = t.result === "open";
              const color = isOpen ? THEME.yellow : isWin ? THEME.green : THEME.red;
              const icon  = isOpen ? "⟳" : isWin ? "✓" : "✗";
              return (
                <div key={t.id} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "7px 10px", borderRadius: "8px",
                  background: `${color}0d`, border: `1px solid ${color}25`,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "7px" }}>
                    <span style={{ fontSize: "11px", color }}>{icon}</span>
                    <span style={{ fontSize: "11px", fontWeight: "700", color: THEME.text }}>
                      {t.symbol.replace("USD", "")}
                    </span>
                    <span style={{ fontSize: "9px", color: THEME.muted }}>
                      @${t.entry_price?.toLocaleString("en", { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                  <span style={{ fontSize: "12px", fontWeight: "800", color }}>
                    {isOpen
                      ? "OUVERT"
                      : t.pnl_pct != null
                        ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(1)}%`
                        : "—"
                    }
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {isReady && (
        <div style={{
          marginTop: "12px", padding: "10px", borderRadius: "10px",
          background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.25)",
          fontSize: "10px", color: THEME.green, textAlign: "center", fontWeight: "700",
        }}>
          🚀 Objectif atteint ! Mets PAPER_TRADING=false dans le .env pour passer en live.
        </div>
      )}
    </div>
  );
}

function BotControlPanel() {
  const [botStatus, setBotStatus]       = useState(null);
  const [loading, setLoading]           = useState(false);
  const [stats, setStats]               = useState(null);
  const [recentTrades, setRecentTrades] = useState([]);

  const fetchStatus = async () => {
    try {
      const res  = await fetch(`${BOT_API}/api/bot/status`);
      const data = await res.json();
      setBotStatus(data.running);
      setStats((prev) => ({ ...prev, ...data }));
    } catch { setBotStatus(false); }
  };

  const fetchPerf = async () => {
    try {
      const res  = await fetch(`${BOT_API}/api/performance`);
      const data = await res.json();
      setStats((prev) => ({ ...prev, ...data }));
    } catch {}
  };

  const fetchRecentTrades = async () => {
    try {
      const res  = await fetch(`${BOT_API}/api/trades?limit=3`);
      const data = await res.json();
      setRecentTrades(Array.isArray(data) ? data : []);
    } catch {}
  };

  useEffect(() => {
    fetchStatus(); fetchPerf(); fetchRecentTrades();
    const iv = setInterval(() => { fetchStatus(); fetchPerf(); fetchRecentTrades(); }, 15000);
    return () => clearInterval(iv);
  }, []);

  const toggleBot = async () => {
    setLoading(true);
    try {
      const endpoint = botStatus ? "/api/bot/stop" : "/api/bot/start";
      const res  = await fetch(`${BOT_API}${endpoint}`, { method: "POST" });
      const data = await res.json();
      setBotStatus(data.running);
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const isRunning = botStatus === true;

  return (
    <div style={{
      background: isRunning
        ? "linear-gradient(135deg, rgba(16,185,129,0.08), rgba(6,182,212,0.05))"
        : "linear-gradient(135deg, rgba(239,68,68,0.06), rgba(245,158,11,0.04))",
      border: `1px solid ${isRunning ? "rgba(16,185,129,0.25)" : "rgba(239,68,68,0.2)"}`,
      borderRadius: "20px", padding: "18px", marginBottom: "16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "6px" }}>
            🤖 ROBOT DE TRADING
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div style={{
              width: "10px", height: "10px", borderRadius: "50%",
              background: botStatus === null ? THEME.yellow : isRunning ? THEME.green : THEME.red,
              boxShadow: isRunning ? `0 0 10px ${THEME.green}` : "none",
              animation: isRunning ? "pulse 2s infinite" : "none",
            }} />
            <span style={{
              fontSize: "18px", fontWeight: "800",
              color: botStatus === null ? THEME.yellow : isRunning ? THEME.green : THEME.red,
            }}>
              {botStatus === null ? "Connexion…" : isRunning ? "EN MARCHE" : "ARRÊTÉ"}
            </span>
          </div>
        </div>
        <button onClick={toggleBot} disabled={loading || botStatus === null} style={{
          padding: "14px 28px", borderRadius: "14px", cursor: "pointer",
          fontFamily: "inherit", fontSize: "14px", fontWeight: "800",
          letterSpacing: "1px", border: "none", transition: "all 0.2s",
          background: isRunning ? "rgba(239,68,68,0.2)" : THEME.gradP,
          color: isRunning ? THEME.red : "#fff",
          boxShadow: isRunning ? "0 0 20px rgba(239,68,68,0.2)" : "0 0 24px rgba(139,92,246,0.4)",
          opacity: (loading || botStatus === null) ? 0.6 : 1,
        }}>
          {loading ? "⟳" : isRunning ? "⏹ STOP" : "▶ START"}
        </button>
      </div>

      {stats && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "8px" }}>
          {[
            { label: "TRADES",   val: stats.total_trades ?? "—",  color: THEME.text },
            { label: "WIN RATE", val: stats.win_rate != null ? `${(stats.win_rate).toFixed(0)}%` : "—", color: THEME.green },
            { label: "P&L",      val: stats.total_pnl_usd != null ? `$${stats.total_pnl_usd.toFixed(2)}` : "—", color: stats.total_pnl_usd >= 0 ? THEME.green : THEME.red },
          ].map((s) => (
            <div key={s.label} style={{
              background: "rgba(255,255,255,0.04)", borderRadius: "12px",
              padding: "10px", textAlign: "center", border: `1px solid ${THEME.border}`,
            }}>
              <div style={{ fontSize: "9px", color: THEME.muted, marginBottom: "4px", letterSpacing: "1px" }}>{s.label}</div>
              <div style={{ fontSize: "17px", fontWeight: "800", color: s.color }}>{s.val}</div>
            </div>
          ))}
        </div>
      )}

      <TrainingDashboard stats={stats} recentTrades={recentTrades} />

      {stats?.paper_trading && (
        <div style={{
          marginTop: "10px", fontSize: "10px", color: THEME.yellow,
          textAlign: "center", padding: "8px",
          background: "rgba(245,158,11,0.08)", borderRadius: "10px",
          border: "1px solid rgba(245,158,11,0.2)",
        }}>
          📝 MODE PAPER TRADING — Argent fictif, aucun risque réel
        </div>
      )}

      {isRunning && (
        <button onClick={async () => {
          if (!window.confirm("⚠️ Fermer TOUS les trades ouverts ?")) return;
          await fetch(`${BOT_API}/api/panic`, { method: "POST" });
          alert("🚨 Tous les trades fermés !");
        }} style={{
          width: "100%", marginTop: "10px", padding: "10px",
          borderRadius: "10px", border: "1px solid rgba(239,68,68,0.3)",
          background: "rgba(239,68,68,0.08)", color: THEME.red,
          fontFamily: "inherit", fontSize: "11px", cursor: "pointer",
          letterSpacing: "1px", fontWeight: "700",
        }}>
          🚨 PANIC — FERMER TOUS LES TRADES
        </button>
      )}
    </div>
  );
}

export default function Trade({ prices }) {
  const [selectedPair, setSelectedPair] = useState(PAIRS[0]);
  const [side, setSide]           = useState("buy");
  const [orderType, setOrderType] = useState("market");
  const [volume, setVolume]       = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [euros, setEuros]         = useState("");
  const [result, setResult]       = useState(null);
  const [error, setError]         = useState("");
  const [loading, setLoading]     = useState(false);
  const [confirm, setConfirm]     = useState(false);
  const [showRisk, setShowRisk]   = useState(true);
  const [showExit, setShowExit]   = useState(false);
  const [portfolioEUR, setPortfolioEUR] = useState(getStoredPortfolio);

  const price    = prices[selectedPair.ws];
  const rsi      = price ? calcRSI(price.change24h) : 50;
  const score    = price ? calcScore(price.change24h, rsi) : 50;
  const signal   = score > 65 ? "ACHETER" : score < 40 ? "VENDRE" : "ATTENDRE";
  const sigColor = score > 65 ? THEME.green : score < 40 ? THEME.red : THEME.yellow;

  const riskCalc     = price && portfolioEUR ? calcPositionSize(portfolioEUR, price.price, 0.05, 0.07) : null;
  const stopLossCalc = price ? calcStopLoss(price.price, { hard: 0.07, trailing: 0.05 }) : null;
  const fib          = price?.history ? fibFromHistory(price.history, 30) : null;
  const exitLevels   = price ? calcExitLevels(price.price) : [];
  const validation   = euros && portfolioEUR
    ? validateTrade({ volumeEUR: parseFloat(euros) || 0, portfolioEUR, rsi, concentrationPct: 0 })
    : null;

  const handleEuros  = (val) => { setEuros(val); setVolume(price && val ? (parseFloat(val) / price.price).toFixed(selectedPair.decimals) : ""); };
  const handleVolume = (val) => { setVolume(val); setEuros(price && val ? (parseFloat(val) * price.price).toFixed(2) : ""); };

  const validate = () => {
    if (!volume || parseFloat(volume) <= 0) return "Volume invalide";
    if (parseFloat(volume) < selectedPair.minVol) return `Min: ${selectedPair.minVol} ${selectedPair.name}`;
    if (orderType === "limit" && (!limitPrice || parseFloat(limitPrice) <= 0)) return "Prix limite requis";
    return null;
  };

  const submit = async () => {
    const err = validate();
    if (err) return setError(err);
    if (!confirm) return setConfirm(true);
    setLoading(true); setError(""); setResult(null); setConfirm(false);
    try {
      const order = { pair: selectedPair.kraken, type: side, ordertype: orderType, volume, ...(orderType === "limit" ? { price: limitPrice } : {}) };
      const data  = await api.placeTrade(order);
      setResult(data);
      logTrade({ pair: selectedPair.ws, name: selectedPair.name, side, volume: parseFloat(volume), price: price?.price, euros: parseFloat(euros), stopLoss: stopLossCalc?.hard, exitLevels, txid: data.txid?.join(",") });
      setVolume(""); setEuros(""); setLimitPrice("");
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  return (
    <div style={{ padding: "16px", paddingBottom: "80px" }}>
      <BotControlPanel />

      {/* Pair selector */}
      <div style={{ display: "flex", gap: "8px", overflowX: "auto", paddingBottom: "4px", marginBottom: "16px", scrollbarWidth: "none" }}>
        {PAIRS.map((p) => {
          const pr     = prices[p.ws];
          const active = p.ws === selectedPair.ws;
          return (
            <button key={p.ws} onClick={() => setSelectedPair(p)} style={{
              flexShrink: 0, padding: "8px 12px", borderRadius: "12px",
              border: active ? `1px solid ${THEME.purple}` : `1px solid ${THEME.border}`,
              background: active ? "rgba(139,92,246,0.12)" : THEME.glass,
              color: active ? THEME.purple : THEME.muted,
              cursor: "pointer", fontFamily: "inherit", fontSize: "12px",
              minWidth: "72px", textAlign: "center",
              boxShadow: active ? "0 0 16px rgba(139,92,246,0.2)" : "none",
            }}>
              <div style={{ fontWeight: "700" }}>{p.symbol} {p.name}</div>
              {pr && (
                <div style={{ fontSize: "10px", marginTop: "2px", color: pr.change24h >= 0 ? THEME.green : THEME.red }}>
                  {pr.change24h >= 0 ? "▲" : "▼"}{Math.abs(pr.change24h || 0).toFixed(1)}%
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* Price card */}
      {price && (
        <div style={{
          background: "linear-gradient(135deg, rgba(139,92,246,0.08), rgba(59,130,246,0.05))",
          border: `1px solid ${THEME.borderP}`,
          borderRadius: "16px", padding: "16px", marginBottom: "14px",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "4px" }}>PRIX ACTUEL</div>
              <div style={{ fontSize: "28px", fontWeight: "800", color: THEME.text, letterSpacing: "-1px" }}>
                €{price.price.toLocaleString("fr", { maximumFractionDigits: price.price < 1 ? 4 : 2 })}
              </div>
              <div style={{ fontSize: "12px", color: price.change24h >= 0 ? THEME.green : THEME.red, marginTop: "4px" }}>
                {price.change24h >= 0 ? "▲" : "▼"} {Math.abs(price.change24h || 0).toFixed(2)}% (24h)
              </div>
              <div style={{ marginTop: "8px", display: "flex", gap: "6px", flexWrap: "wrap" }}>
                {badge(`RSI ${rsi.toFixed(0)}`, rsi > 70 ? THEME.red : rsi < 30 ? THEME.green : THEME.yellow)}
                {fib && badge(`Fib 61.8%: €${fib.r618 > 100 ? fib.r618.toLocaleString("fr", { maximumFractionDigits: 0 }) : fib.r618.toFixed(4)}`, THEME.cyan)}
              </div>
            </div>
            <div style={{ textAlign: "center" }}>
              <ScoreRing score={score} size={60} />
              <div style={{ fontSize: "10px", color: sigColor, marginTop: "4px", letterSpacing: "1px", fontWeight: "700" }}>{signal}</div>
            </div>
          </div>
        </div>
      )}

      {/* Risk panel */}
      <div style={{ background: "rgba(245,158,11,0.05)", border: "1px solid rgba(245,158,11,0.2)", borderRadius: "14px", marginBottom: "14px", overflow: "hidden" }}>
        <button onClick={() => setShowRisk(!showRisk)} style={{ width: "100%", padding: "14px 16px", display: "flex", justifyContent: "space-between", background: "transparent", border: "none", color: THEME.yellow, cursor: "pointer", fontFamily: "inherit", fontSize: "11px", letterSpacing: "1px", fontWeight: "700" }}>
          <span>🛡️ GESTION DU RISQUE</span>
          <span style={{ transform: showRisk ? "rotate(180deg)" : "none", transition: "0.2s" }}>▼</span>
        </button>
        {showRisk && (
          <div style={{ padding: "0 16px 16px" }}>
            <div style={{ marginBottom: "12px" }}>
              <label style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "1px", display: "block", marginBottom: "6px" }}>CAPITAL TOTAL (€)</label>
              <input type="number" value={portfolioEUR || ""} inputMode="decimal"
                onChange={(e) => { const v = parseFloat(e.target.value) || 0; setPortfolioEUR(v); try { localStorage.setItem("portfolio_eur", String(v)); } catch {} }}
                placeholder="Ex: 1000" style={inputStyle("rgba(245,158,11,0.4)")} />
            </div>
            {riskCalc && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "12px" }}>
                <div style={{ background: "rgba(245,158,11,0.08)", borderRadius: "12px", padding: "12px", border: "1px solid rgba(245,158,11,0.2)" }}>
                  <div style={{ fontSize: "9px", color: THEME.muted, marginBottom: "4px" }}>MAX PAR TRADE (5%)</div>
                  <div style={{ fontSize: "18px", color: THEME.yellow, fontWeight: "800" }}>€{riskCalc.maxEUR}</div>
                </div>
                <div style={{ background: "rgba(239,68,68,0.08)", borderRadius: "12px", padding: "12px", border: "1px solid rgba(239,68,68,0.2)" }}>
                  <div style={{ fontSize: "9px", color: THEME.muted, marginBottom: "4px" }}>RISQUE MAX (7%)</div>
                  <div style={{ fontSize: "18px", color: THEME.red, fontWeight: "800" }}>€{riskCalc.riskEUR}</div>
                </div>
              </div>
            )}
            {riskCalc && (
              <button onClick={() => handleEuros(riskCalc.maxEUR.toFixed(2))} style={{ padding: "10px 16px", borderRadius: "10px", background: "rgba(245,158,11,0.1)", color: THEME.yellow, border: "1px solid rgba(245,158,11,0.3)", fontFamily: "inherit", fontSize: "11px", cursor: "pointer", fontWeight: "700" }}>
                ↑ Utiliser €{riskCalc.maxEUR} (max recommandé)
              </button>
            )}
          </div>
        )}
      </div>

      {/* Buy/Sell */}
      <div style={{ display: "flex", background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "14px", marginBottom: "14px", overflow: "hidden" }}>
        {["buy", "sell"].map((s) => (
          <button key={s} onClick={() => setSide(s)} style={{
            flex: 1, padding: "14px", border: "none", cursor: "pointer",
            fontFamily: "inherit", fontSize: "14px", fontWeight: "800", letterSpacing: "1px", textTransform: "uppercase",
            background: side === s ? (s === "buy" ? "linear-gradient(135deg, rgba(16,185,129,0.2), rgba(6,182,212,0.1))" : "linear-gradient(135deg, rgba(239,68,68,0.2), rgba(245,158,11,0.1))") : "transparent",
            color: side === s ? (s === "buy" ? THEME.green : THEME.red) : THEME.muted,
            borderBottom: side === s ? `2px solid ${s === "buy" ? THEME.green : THEME.red}` : "2px solid transparent",
            transition: "all 0.2s",
          }}>
            {s === "buy" ? "▲ ACHETER" : "▼ VENDRE"}
          </button>
        ))}
      </div>

      {/* Order type */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "14px" }}>
        {["market", "limit"].map((t) => (
          <button key={t} onClick={() => setOrderType(t)} style={{
            flex: 1, padding: "12px", borderRadius: "12px", cursor: "pointer",
            fontFamily: "inherit", fontSize: "12px", textTransform: "uppercase", fontWeight: "700",
            background: orderType === t ? "rgba(139,92,246,0.12)" : THEME.glass,
            border: `1px solid ${orderType === t ? THEME.purple : THEME.border}`,
            color: orderType === t ? THEME.purple : THEME.muted,
          }}>
            {t === "market" ? "Au marché" : "Limite"}
          </button>
        ))}
      </div>

      {/* Inputs */}
      <div style={{ marginBottom: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
          <label style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "1px" }}>MONTANT EN EUROS</label>
          {riskCalc && <span style={{ fontSize: "9px", color: THEME.yellow }}>Max: €{riskCalc.maxEUR}</span>}
        </div>
        <input type="number" value={euros} onChange={(e) => handleEuros(e.target.value)} placeholder="Ex: 10" inputMode="decimal" style={inputStyle(side === "buy" ? "rgba(16,185,129,0.4)" : "rgba(239,68,68,0.4)")} />
      </div>

      <div style={{ marginBottom: "12px" }}>
        <label style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "1px", display: "block", marginBottom: "8px" }}>
          VOLUME ({selectedPair.name}) — Min: {selectedPair.minVol}
        </label>
        <input type="number" value={volume} onChange={(e) => handleVolume(e.target.value)} placeholder={`Min ${selectedPair.minVol}`} inputMode="decimal" style={inputStyle(side === "buy" ? "rgba(16,185,129,0.4)" : "rgba(239,68,68,0.4)")} />
      </div>

      {orderType === "limit" && (
        <div style={{ marginBottom: "12px" }}>
          <label style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "1px", display: "block", marginBottom: "8px" }}>PRIX LIMITE (€)</label>
          <input type="number" value={limitPrice} onChange={(e) => setLimitPrice(e.target.value)} placeholder={`Ex: ${price?.price ? Math.round(price.price * 0.99) : ""}`} inputMode="decimal" style={inputStyle("rgba(245,158,11,0.4)")} />
        </div>
      )}

      {validation?.warnings.map((w, i) => (
        <div key={i} style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)", borderRadius: "10px", padding: "10px 14px", marginBottom: "8px", fontSize: "11px", color: THEME.yellow }}>⚠️ {w}</div>
      ))}
      {validation?.errors.map((e, i) => (
        <div key={i} style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: "10px", padding: "10px 14px", marginBottom: "8px", fontSize: "11px", color: THEME.red }}>✗ {e}</div>
      ))}

      {euros && price && (
        <div style={{ background: "rgba(139,92,246,0.06)", border: "1px solid rgba(139,92,246,0.2)", borderRadius: "14px", marginBottom: "14px", overflow: "hidden" }}>
          <button onClick={() => setShowExit(!showExit)} style={{ width: "100%", padding: "12px 16px", display: "flex", justifyContent: "space-between", background: "transparent", border: "none", color: THEME.purple, cursor: "pointer", fontFamily: "inherit", fontSize: "11px", letterSpacing: "1px", fontWeight: "700" }}>
            <span>📈 STRATÉGIE DE SORTIE</span>
            <span style={{ transform: showExit ? "rotate(180deg)" : "none", transition: "0.2s" }}>▼</span>
          </button>
          {showExit && (
            <div style={{ padding: "0 16px 14px" }}>
              {exitLevels.map((lvl) => (
                <div key={lvl.step} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottom: `1px solid ${THEME.border}` }}>
                  <span style={{ fontSize: "11px", color: THEME.purple, fontWeight: "700" }}>+{lvl.gainPct}%</span>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <span style={{ fontSize: "11px", color: THEME.text }}>€{lvl.price > 100 ? lvl.price.toLocaleString("fr", { maximumFractionDigits: 0 }) : lvl.price.toFixed(4)}</span>
                    {badge(`Vendre ${lvl.sellPct}%`, THEME.purple)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {confirm && (
        <div style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.25)", borderRadius: "14px", padding: "16px", marginBottom: "12px" }}>
          <div style={{ fontSize: "10px", color: THEME.yellow, letterSpacing: "1px", marginBottom: "8px", fontWeight: "700" }}>⚠️ CONFIRMER L'ORDRE RÉEL</div>
          <div style={{ fontSize: "13px", color: THEME.text, lineHeight: "1.8" }}>
            {side.toUpperCase()} <strong>{volume} {selectedPair.name}</strong><br />
            Type: {orderType === "market" ? "Au marché" : `Limite à €${limitPrice}`}<br />
            Valeur: ~€{euros}
          </div>
        </div>
      )}

      {error && <div style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: "10px", padding: "12px", marginBottom: "12px", fontSize: "12px", color: THEME.red }}>✗ {error}</div>}
      {result && <div style={{ background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.2)", borderRadius: "10px", padding: "12px", marginBottom: "12px", fontSize: "12px", color: THEME.green }}>✓ Ordre placé — TxID: {result.txid?.join(", ") || JSON.stringify(result)}</div>}

      <button onClick={submit} disabled={loading || (validation && !validation.valid)} style={{
        width: "100%", padding: "18px", borderRadius: "14px",
        background: side === "buy" ? "linear-gradient(135deg, rgba(16,185,129,0.25), rgba(6,182,212,0.15))" : "linear-gradient(135deg, rgba(239,68,68,0.25), rgba(245,158,11,0.15))",
        color: side === "buy" ? THEME.green : THEME.red,
        border: `1px solid ${side === "buy" ? "rgba(16,185,129,0.4)" : "rgba(239,68,68,0.4)"}`,
        fontSize: "15px", fontWeight: "800", fontFamily: "inherit",
        cursor: (loading || (validation && !validation.valid)) ? "not-allowed" : "pointer",
        opacity: (loading || (validation && !validation.valid)) ? 0.5 : 1,
        letterSpacing: "1px", minHeight: "56px",
      }}>
        {loading ? "⟳ Envoi en cours…" : confirm ? "✓ CONFIRMER L'ORDRE RÉEL" : `${side === "buy" ? "▲ ACHETER" : "▼ VENDRE"} ${selectedPair.name}`}
      </button>

      {confirm && (
        <button onClick={() => setConfirm(false)} style={{ width: "100%", padding: "14px", marginTop: "8px", borderRadius: "12px", background: "transparent", border: `1px solid ${THEME.border}`, color: THEME.muted, fontFamily: "inherit", fontSize: "13px", cursor: "pointer" }}>
          Annuler
        </button>
      )}
    </div>
  );
}