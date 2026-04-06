import { useState, useEffect } from "react";
import { AreaChart, Area, ResponsiveContainer } from "recharts";
import { calcRSI, calcScore, calcExitLevels, currentExitStep } from "../utils/technical";
import { getCagnotte, addToCagnotte } from "../utils/risk";
import ScoreRing from "../components/ScoreRing";
import { api } from "../api";
import { THEME } from "../theme";

// ── Métadonnées des paires Kraken ──────────────────────────────────────────
const SYMBOL_META = {
  BTCUSD:  { name: "Bitcoin",   short: "BTC",  symbol: "₿",  color: "#f7931a" },
  XBTUSD:  { name: "Bitcoin",   short: "BTC",  symbol: "₿",  color: "#f7931a" },
  ETHUSD:  { name: "Ethereum",  short: "ETH",  symbol: "Ξ",  color: "#627eea" },
  SOLUSD:  { name: "Solana",    short: "SOL",  symbol: "◎",  color: "#9945ff" },
  ADAUSD:  { name: "Cardano",   short: "ADA",  symbol: "₳",  color: "#0033ad" },
  DOTUSD:  { name: "Polkadot",  short: "DOT",  symbol: "●",  color: "#e6007a" },
  XRPUSD:  { name: "Ripple",    short: "XRP",  symbol: "✕",  color: "#00aae4" },
  LINKUSD: { name: "Chainlink", short: "LINK", symbol: "⬡",  color: "#2a5ada" },
  LTCUSD:  { name: "Litecoin",  short: "LTC",  symbol: "Ł",  color: "#bfbbbb" },
  BCHUSD:  { name: "Bitcoin Cash", short: "BCH", symbol: "Ƀ", color: "#8dc351" },
  XLMUSD:  { name: "Stellar",   short: "XLM",  symbol: "✷",  color: "#7d9bcc" },
  AVAXUSD: { name: "Avalanche", short: "AVAX", symbol: "△",  color: "#e84142" },
  ATOMUSD: { name: "Cosmos",    short: "ATOM", symbol: "⚛",  color: "#6f7390" },
  ALGOUSD: { name: "Algorand",  short: "ALGO", symbol: "◈",  color: "#00d190" },
  NEARUSD: { name: "NEAR",      short: "NEAR", symbol: "Ν",  color: "#00c08b" },
  TRXUSD:  { name: "TRON",      short: "TRX",  symbol: "T",  color: "#ff0013" },
  UNIUSD:  { name: "Uniswap",   short: "UNI",  symbol: "♦",  color: "#ff007a" },
};

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("fr", { day: "2-digit", month: "2-digit" })
    + " " + d.toLocaleTimeString("fr", { hour: "2-digit", minute: "2-digit" });
}

function fmtDuration(openedAt, closedAt) {
  if (!openedAt || !closedAt) return null;
  const ms = new Date(closedAt) - new Date(openedAt);
  const h  = Math.floor(ms / 3600000);
  const m  = Math.floor((ms % 3600000) / 60000);
  if (h === 0) return `${m}min`;
  return `${h}h ${m}min`;
}

function fmtPrice(p) {
  if (p == null) return "—";
  return p > 100
    ? p.toLocaleString("fr", { maximumFractionDigits: 2 })
    : p.toFixed(p < 0.01 ? 6 : 4);
}

function TradeCard({ t }) {
  const meta        = SYMBOL_META[t.symbol] || { name: t.symbol, short: t.symbol, symbol: "◈", color: THEME.purple };
  const isOpen      = t.result === "open";
  const resultColor = t.result === "win" ? THEME.green : t.result === "loss" ? THEME.red : THEME.yellow;
  const volume      = t.position_size_usd && t.entry_price ? t.position_size_usd / t.entry_price : null;
  const duration    = fmtDuration(t.opened_at, t.closed_at);

  // Take-profit progress bar for open trades
  const tpLevels = t.take_profit_structure
    ? Object.entries(t.take_profit_structure)
        .map(([label, v]) => ({ label, target: v.target_price, sell: v.sell_pct }))
        .sort((a, b) => a.target - b.target)
    : [];

  return (
    <div style={{
      background: THEME.glass,
      border: `1px solid ${resultColor}28`,
      borderLeft: `3px solid ${resultColor}`,
      borderRadius: "16px", marginBottom: "12px", overflow: "hidden",
    }}>
      {/* ── En-tête ── */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "12px 14px 10px",
        borderBottom: `1px solid ${THEME.border}`,
        background: `linear-gradient(135deg, ${meta.color}0d, transparent)`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div style={{
            width: "38px", height: "38px", borderRadius: "12px",
            background: `${meta.color}20`, border: `1px solid ${meta.color}40`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "18px", color: meta.color, fontWeight: "700",
          }}>{meta.symbol}</div>
          <div>
            <div style={{ fontSize: "14px", fontWeight: "700", color: THEME.text }}>{meta.name}</div>
            <div style={{ display: "flex", gap: "6px", marginTop: "3px", alignItems: "center" }}>
              <span style={{
                fontSize: "9px", padding: "1px 7px", borderRadius: "20px", fontWeight: "700",
                background: THEME.green + "18", color: THEME.green, border: `1px solid ${THEME.green}30`,
              }}>▲ ACHAT</span>
              {t.is_paper && (
                <span style={{
                  fontSize: "9px", padding: "1px 7px", borderRadius: "20px", fontWeight: "600",
                  background: THEME.purple + "18", color: THEME.purple, border: `1px solid ${THEME.purple}30`,
                }}>PAPER</span>
              )}
            </div>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          {/* Badge résultat */}
          <span style={{
            display: "inline-block", fontSize: "11px", padding: "3px 10px",
            borderRadius: "20px", fontWeight: "800",
            background: resultColor + "20", color: resultColor,
            border: `1px solid ${resultColor}40`,
          }}>
            {isOpen ? "⏳ EN COURS" : t.result === "win" ? "✓ WIN" : "✗ LOSS"}
          </span>
          <div style={{ fontSize: "10px", color: THEME.muted, marginTop: "4px" }}>
            {fmtDateTime(t.opened_at)}
          </div>
          {duration && (
            <div style={{ fontSize: "9px", color: THEME.muted }}>⏱ {duration}</div>
          )}
        </div>
      </div>

      {/* ── Corps ── */}
      <div style={{ padding: "12px 14px" }}>
        {/* Ligne prix */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "10px" }}>
          <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px 10px" }}>
            <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>ENTRÉE</div>
            <div style={{ fontSize: "14px", fontWeight: "700", color: THEME.text }}>${fmtPrice(t.entry_price)}</div>
          </div>
          <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px 10px" }}>
            <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>
              {isOpen ? "STOP-LOSS" : "SORTIE"}
            </div>
            <div style={{ fontSize: "14px", fontWeight: "700", color: isOpen ? THEME.red : THEME.text }}>
              ${isOpen ? fmtPrice(t.stop_loss_price) : fmtPrice(t.exit_price)}
            </div>
          </div>
        </div>

        {/* Montant investi + volume */}
        <div style={{ display: "flex", gap: "8px", marginBottom: t.pnl_pct != null || isOpen ? "10px" : "0" }}>
          {t.position_size_usd != null && (
            <div style={{ flex: 1, background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px 10px" }}>
              <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>INVESTI</div>
              <div style={{ fontSize: "13px", fontWeight: "700", color: THEME.blue }}>${t.position_size_usd.toFixed(2)}</div>
            </div>
          )}
          {volume != null && (
            <div style={{ flex: 1, background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px 10px" }}>
              <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>VOLUME</div>
              <div style={{ fontSize: "13px", fontWeight: "700", color: meta.color }}>
                {volume < 0.001 ? volume.toFixed(6) : volume.toFixed(4)} {meta.short}
              </div>
            </div>
          )}
        </div>

        {/* P&L si fermé */}
        {!isOpen && t.pnl_pct != null && (
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            background: resultColor + "10", borderRadius: "10px", padding: "10px 12px",
            border: `1px solid ${resultColor}25`, marginBottom: "10px",
          }}>
            <div>
              <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>P&L</div>
              <div style={{ fontSize: "20px", fontWeight: "800", color: resultColor }}>
                {t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
              </div>
            </div>
            {t.pnl_usd != null && (
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "2px" }}>USD</div>
                <div style={{ fontSize: "16px", fontWeight: "700", color: resultColor }}>
                  {t.pnl_usd > 0 ? "+" : ""}${t.pnl_usd.toFixed(2)}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Stop-loss + régime pour trades fermés */}
        {!isOpen && (
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            <span style={{
              fontSize: "9px", padding: "2px 8px", borderRadius: "20px",
              background: THEME.red + "15", color: THEME.red, border: `1px solid ${THEME.red}25`,
            }}>SL ${fmtPrice(t.stop_loss_price)}</span>
            {t.regime_at_entry && (
              <span style={{
                fontSize: "9px", padding: "2px 8px", borderRadius: "20px",
                background: THEME.purple + "15", color: THEME.purple, border: `1px solid ${THEME.purple}25`,
              }}>{t.regime_at_entry}</span>
            )}
            {t.confidence_at_entry != null && (
              <span style={{
                fontSize: "9px", padding: "2px 8px", borderRadius: "20px",
                background: THEME.blue + "15", color: THEME.blue, border: `1px solid ${THEME.blue}25`,
              }}>confiance {t.confidence_at_entry.toFixed(0)}%</span>
            )}
          </div>
        )}

        {/* Take-profits pour trades ouverts */}
        {isOpen && tpLevels.length > 0 && (
          <div style={{
            background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "10px 12px",
            marginBottom: "8px",
          }}>
            <div style={{ fontSize: "9px", color: THEME.purple, letterSpacing: "1px", marginBottom: "8px" }}>
              TAKE-PROFITS
            </div>
            {tpLevels.map((tp, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "5px" }}>
                <div style={{
                  width: "6px", height: "6px", borderRadius: "50%",
                  background: THEME.purple, flexShrink: 0,
                }} />
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "3px" }}>
                    <span style={{ fontSize: "10px", color: THEME.text2 }}>${fmtPrice(tp.target)}</span>
                    <span style={{ fontSize: "9px", color: THEME.muted }}>vendre {(tp.sell * 100).toFixed(0)}%</span>
                  </div>
                  <div style={{ height: "3px", background: "rgba(139,92,246,0.15)", borderRadius: "2px" }}>
                    <div style={{
                      height: "100%", borderRadius: "2px",
                      width: `${Math.min(100, ((t.entry_price - t.stop_loss_price) / (tp.target - t.stop_loss_price)) * 100)}%`,
                      background: `linear-gradient(90deg, ${THEME.purple}, ${THEME.blue})`,
                    }} />
                  </div>
                </div>
              </div>
            ))}
            <div style={{ fontSize: "9px", color: THEME.red, marginTop: "6px" }}>
              SL ${fmtPrice(t.stop_loss_price)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
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
            ) : tradeLog.map((t) => <TradeCard key={t.id} t={t} />)}
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