import { useState, useEffect } from "react";
import { AreaChart, Area, ResponsiveContainer } from "recharts";
import { calcRSI, calcScore, calcExitLevels, currentExitStep } from "../utils/technical";
import { getCagnotte, addToCagnotte } from "../utils/risk";
import ScoreRing from "../components/ScoreRing";
import { api } from "../api";
import { THEME } from "../theme";

function badge(val, color) {
  return (
    <span style={{
      fontSize: "10px", padding: "2px 10px", borderRadius: "20px",
      background: color + "18", color, border: `1px solid ${color}35`,
      fontWeight: "600",
    }}>{val}</span>
  );
}

function MiniSparkline({ data, color }) {
  if (!data?.length) return null;
  return (
    <ResponsiveContainer width="100%" height={40}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={`sg${color.replace("#","")}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="price" stroke={color} strokeWidth={2}
          fill={`url(#sg${color.replace("#","")})`} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function ExitStrategyMini({ entryPrice, currentPrice }) {
  const levels = calcExitLevels(entryPrice);
  const step   = currentExitStep(entryPrice, currentPrice);
  const next   = levels.find((l) => l.step > step);
  return (
    <div style={{
      marginTop: "10px", background: "rgba(139,92,246,0.06)",
      borderRadius: "10px", padding: "10px",
      border: "1px solid rgba(139,92,246,0.15)",
    }}>
      <div style={{ fontSize: "9px", color: THEME.purple, letterSpacing: "1px", marginBottom: "6px" }}>
        STRATÉGIE DE SORTIE — {step > 0 ? `Étape ${step} atteinte` : "En attente"}
      </div>
      <div style={{ display: "flex", gap: "4px", marginBottom: "6px" }}>
        {levels.map((l) => (
          <div key={l.step} style={{
            flex: 1, height: "4px", borderRadius: "2px",
            background: l.step <= step ? THEME.purple : "rgba(139,92,246,0.15)",
          }} />
        ))}
      </div>
      {next ? (
        <div style={{ fontSize: "10px", color: THEME.text2 }}>
          Prochain: <span style={{ color: THEME.purple, fontWeight: "600" }}>+{next.gainPct}%</span>{" "}
          → €{next.price > 100
            ? next.price.toLocaleString("fr", { maximumFractionDigits: 0 })
            : next.price.toFixed(4)}
          <span style={{ color: THEME.muted }}> (vendre {next.sellPct}%)</span>
        </div>
      ) : (
        <div style={{ fontSize: "10px", color: THEME.green }}>✓ Tous les objectifs dépassés !</div>
      )}
    </div>
  );
}

const DEMO_HOLDINGS = [
  { pair: "XBT/USD", amount: 0.001, avgBuy: 71200 },
  { pair: "ETH/USD", amount: 0.05,  avgBuy: 2450 },
  { pair: "SOL/USD", amount: 1,     avgBuy: 132 },
  { pair: "ADA/USD", amount: 50,    avgBuy: 0.38 },
];

const ACCENT_COLORS = [THEME.purple, THEME.blue, THEME.cyan, THEME.green];

export default function Portfolio({ prices }) {
  const [balance, setBalance]             = useState(null);
  const [cagnotte, setCagnotte]           = useState(getCagnotte());
  const [tradeLog, setTradeLog]           = useState([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [view, setView]                   = useState("positions");

  useEffect(() => {
    api.getBalance().then(setBalance).catch(() => {});
    setCagnotte(getCagnotte());
  }, []);

  useEffect(() => {
    if (view !== "trades") return;
    setTradesLoading(true);
    api.getTrades()
      .then((data) => {
        setTradeLog(Array.isArray(data) ? data : []);
      })
      .catch((e) => {
        console.error("[Portfolio] getTrades error:", e);
        setTradeLog([]);
      })
      .finally(() => setTradesLoading(false));
  }, [view]);

  const totalValue = DEMO_HOLDINGS.reduce((s, h) => s + (prices[h.pair]?.price || 0) * h.amount, 0);
  const totalBase  = DEMO_HOLDINGS.reduce((s, h) => s + h.amount * h.avgBuy, 0);
  const totalPnl   = totalValue - totalBase;
  const totalPct   = totalBase ? (totalPnl / totalBase) * 100 : 0;
  const pnlColor   = totalPnl >= 0 ? THEME.green : THEME.red;

  useEffect(() => {
    try { localStorage.setItem("portfolio_eur", String(Math.round(totalValue))); } catch {}
  }, [totalValue]);

  return (
    <div style={{ paddingBottom: "80px" }}>
      {/* Tabs */}
      <div style={{
        display: "flex", padding: "12px 16px 0",
        borderBottom: `1px solid ${THEME.border}`, gap: "4px",
      }}>
        {[
          { id: "positions", label: "💼 Positions" },
          { id: "trades",    label: "📋 Historique" },
          { id: "cagnotte",  label: "🏦 Cagnotte" },
        ].map((t) => (
          <button key={t.id} onClick={() => setView(t.id)} style={{
            flex: 1, padding: "10px 8px", border: "none",
            background: view === t.id ? "rgba(139,92,246,0.1)" : "transparent",
            color: view === t.id ? THEME.purple : THEME.muted,
            borderBottom: view === t.id ? `2px solid ${THEME.purple}` : "2px solid transparent",
            fontFamily: "inherit", fontSize: "11px", cursor: "pointer",
            borderRadius: "8px 8px 0 0",
            fontWeight: view === t.id ? "700" : "400",
          }}>{t.label}</button>
        ))}
      </div>

      <div style={{ padding: "16px" }}>

        {/* ── POSITIONS ── */}
        {view === "positions" && (
          <>
            <div style={{
              background: "linear-gradient(135deg, rgba(139,92,246,0.12), rgba(59,130,246,0.08))",
              border: `1px solid ${THEME.borderP}`,
              borderRadius: "20px", padding: "24px", marginBottom: "16px",
              boxShadow: "0 8px 32px rgba(139,92,246,0.1)",
            }}>
              <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "8px" }}>
                VALEUR TOTALE DU PORTEFEUILLE
              </div>
              <div style={{ fontSize: "36px", fontWeight: "800", color: THEME.text, letterSpacing: "-1px" }}>
                €{totalValue.toLocaleString("fr", { maximumFractionDigits: 2 })}
              </div>
              <div style={{ marginTop: "10px", display: "flex", gap: "10px", alignItems: "center" }}>
                <span style={{ fontSize: "18px", color: pnlColor, fontWeight: "700" }}>
                  {totalPnl >= 0 ? "+" : ""}€{Math.abs(totalPnl).toFixed(2)}
                </span>
                {badge(`${totalPct >= 0 ? "+" : ""}${totalPct.toFixed(2)}%`, pnlColor)}
              </div>
              <div style={{ marginTop: "16px" }}>
                <div style={{ display: "flex", height: "6px", borderRadius: "3px", overflow: "hidden", gap: "2px" }}>
                  {DEMO_HOLDINGS.map((h, i) => {
                    const val = (prices[h.pair]?.price || 0) * h.amount;
                    const pct = totalValue ? val / totalValue * 100 : 25;
                    return (
                      <div key={h.pair} style={{
                        flex: pct, background: ACCENT_COLORS[i % ACCENT_COLORS.length],
                        borderRadius: "3px", opacity: 0.8,
                      }} />
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: "12px", marginTop: "8px", flexWrap: "wrap" }}>
                  {DEMO_HOLDINGS.map((h, i) => (
                    <span key={h.pair} style={{ fontSize: "9px", color: ACCENT_COLORS[i % ACCENT_COLORS.length] }}>
                      ● {prices[h.pair]?.name || h.pair.split("/")[0]}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {balance && (
              <div style={{
                background: THEME.glass, border: `1px solid ${THEME.border}`,
                borderRadius: "16px", padding: "16px", marginBottom: "14px",
              }}>
                <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "10px" }}>
                  SOLDES KRAKEN RÉELS
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                  {Object.entries(balance).filter(([, v]) => parseFloat(v) > 0).map(([asset, vol]) => (
                    <div key={asset} style={{
                      background: "rgba(255,255,255,0.05)", borderRadius: "10px",
                      padding: "8px 12px", border: `1px solid ${THEME.border}`,
                    }}>
                      <div style={{ fontSize: "9px", color: THEME.muted }}>{asset}</div>
                      <div style={{ fontSize: "13px", color: THEME.text, fontWeight: "600" }}>
                        {parseFloat(vol).toFixed(6)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "12px" }}>
              MES POSITIONS
            </div>

            {DEMO_HOLDINGS.map((h, i) => {
              const p = prices[h.pair];
              if (!p) return null;
              const val    = h.amount * p.price;
              const pnl    = (p.price - h.avgBuy) * h.amount;
              const pct    = ((p.price - h.avgBuy) / h.avgBuy) * 100;
              const color  = pnl >= 0 ? THEME.green : THEME.red;
              const accent = ACCENT_COLORS[i % ACCENT_COLORS.length];
              return (
                <div key={h.pair} style={{
                  background: THEME.glass,
                  border: `1px solid ${THEME.border}`,
                  borderLeft: `3px solid ${accent}`,
                  borderRadius: "16px", padding: "16px", marginBottom: "12px",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <div style={{
                        width: "42px", height: "42px", borderRadius: "14px",
                        background: `${accent}18`, border: `1px solid ${accent}35`,
                        display: "flex", alignItems: "center", justifyContent: "center",
                        fontSize: "20px",
                      }}>{p.symbol}</div>
                      <div>
                        <div style={{ fontSize: "15px", fontWeight: "700", color: THEME.text }}>{p.name}</div>
                        <div style={{ fontSize: "11px", color: THEME.muted, marginTop: "2px" }}>
                          {h.amount} × €{p.price > 100
                            ? p.price.toLocaleString("fr", { maximumFractionDigits: 0 })
                            : p.price.toFixed(4)}
                        </div>
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: "16px", fontWeight: "700", color: THEME.text }}>
                        €{val.toLocaleString("fr", { maximumFractionDigits: 2 })}
                      </div>
                      <div style={{ marginTop: "4px" }}>
                        {badge(`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`, color)}
                      </div>
                      <div style={{ fontSize: "12px", color, marginTop: "4px", fontWeight: "600" }}>
                        {pnl >= 0 ? "+" : ""}€{pnl.toFixed(2)}
                      </div>
                    </div>
                  </div>
                  <div style={{ height: "40px", marginBottom: "10px" }}>
                    <MiniSparkline data={p.history} color={color} />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                      {badge(`RSI ${calcRSI(p.change24h).toFixed(0)}`, THEME.yellow)}
                      {badge(`${p.change24h >= 0 ? "+" : ""}${(p.change24h || 0).toFixed(2)}% 24h`, color)}
                    </div>
                    <ScoreRing score={calcScore(p.change24h, calcRSI(p.change24h))} size={38} />
                  </div>
                  <ExitStrategyMini entryPrice={h.avgBuy} currentPrice={p.price} />
                </div>
              );
            })}
          </>
        )}

        {/* ── HISTORIQUE ── */}
        {view === "trades" && (
          <>
            <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "12px" }}>
              HISTORIQUE DES TRADES
            </div>
            {tradesLoading ? (
              <div style={{
                textAlign: "center", color: THEME.muted, fontSize: "13px",
                padding: "60px 20px", background: THEME.glass,
                borderRadius: "16px", border: `1px solid ${THEME.border}`,
              }}>
                <div style={{ fontSize: "28px", marginBottom: "12px" }}>⟳</div>
                Chargement…
              </div>
            ) : tradeLog.length === 0 ? (
              <div style={{
                textAlign: "center", color: THEME.muted, fontSize: "13px",
                padding: "60px 20px", background: THEME.glass,
                borderRadius: "16px", border: `1px solid ${THEME.border}`,
              }}>
                <div style={{ fontSize: "40px", marginBottom: "12px" }}>📋</div>
                Aucun trade enregistré.
              </div>
            ) : tradeLog.map((t) => {
              const resultColor = t.result === "win" ? THEME.green : t.result === "loss" ? THEME.red : THEME.yellow;
              return (
                <div key={t.id} style={{
                  background: THEME.glass, border: `1px solid ${THEME.border}`,
                  borderLeft: `3px solid ${resultColor}`,
                  borderRadius: "14px", padding: "14px", marginBottom: "10px",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
                    <span style={{ fontSize: "13px", color: THEME.text, fontWeight: "700" }}>
                      {t.symbol}
                    </span>
                    <span style={{ fontSize: "10px", color: THEME.muted }}>
                      {t.opened_at ? new Date(t.opened_at).toLocaleDateString("fr") : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ fontSize: "12px", color: THEME.text2 }}>
                      Entrée €{t.entry_price?.toLocaleString("fr", { maximumFractionDigits: 2 })}
                      {t.exit_price && (
                        <span style={{ color: THEME.muted }}> → €{t.exit_price.toLocaleString("fr", { maximumFractionDigits: 2 })}</span>
                      )}
                    </div>
                    {t.result === "open" ? (
                      <span style={{
                        fontSize: "10px", padding: "2px 8px", borderRadius: "20px",
                        background: THEME.yellow + "18", color: THEME.yellow,
                        border: `1px solid ${THEME.yellow}35`, fontWeight: "700",
                      }}>EN COURS</span>
                    ) : (
                      <span style={{
                        fontSize: "10px", padding: "2px 8px", borderRadius: "20px",
                        background: resultColor + "18", color: resultColor,
                        border: `1px solid ${resultColor}35`, fontWeight: "700",
                      }}>
                        {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(2)}%` : (t.result || "—").toUpperCase()}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </>
        )}

        {/* ── CAGNOTTE ── */}
        {view === "cagnotte" && (
          <>
            <div style={{
              background: "linear-gradient(135deg, rgba(16,185,129,0.1), rgba(6,182,212,0.08))",
              border: "1px solid rgba(16,185,129,0.25)",
              borderRadius: "24px", padding: "36px 24px", marginBottom: "16px",
              textAlign: "center",
            }}>
              <div style={{ fontSize: "40px", marginBottom: "10px" }}>🏦</div>
              <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "10px" }}>
                CAGNOTTE AUTOMATIQUE
              </div>
              <div style={{
                fontSize: "52px", fontWeight: "800",
                background: THEME.gradG,
                WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
                letterSpacing: "-2px",
              }}>
                €{cagnotte.toFixed(2)}
              </div>
              <div style={{ fontSize: "12px", color: THEME.text2, marginTop: "10px" }}>
                10% de chaque profit mis de côté automatiquement
              </div>
            </div>

            <div style={{
              background: THEME.glass, border: `1px solid ${THEME.border}`,
              borderRadius: "16px", padding: "16px",
            }}>
              <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "2px", marginBottom: "12px" }}>
                SIMULER UN PROFIT
              </div>
              <div style={{ display: "flex", gap: "8px" }}>
                {[10, 25, 50, 100].map((amt) => (
                  <button key={amt} onClick={() => {
                    const { contribution, total } = addToCagnotte(amt);
                    setCagnotte(total);
                    alert(`✓ +€${contribution.toFixed(2)} ajouté à la cagnotte`);
                  }} style={{
                    flex: 1, padding: "14px 4px", borderRadius: "12px",
                    background: "rgba(16,185,129,0.1)", color: THEME.green,
                    border: "1px solid rgba(16,185,129,0.25)",
                    fontFamily: "inherit", fontSize: "13px",
                    fontWeight: "700", cursor: "pointer",
                  }}>+€{amt}</button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}