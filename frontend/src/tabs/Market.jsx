import { useState, useEffect, useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { calcRSI, calcMACD, calcBollinger, calcScore, calcRSIFromCloses, calcMACDFromCloses, fibFromHistory, estimateElliottWave } from "../utils/technical";
import ScoreRing from "../components/ScoreRing";
import { api } from "../api";
import { THEME } from "../theme";

// Railway backend is proxied via Vite /api → https://crypto-trader-production-8ef4.up.railway.app

function badge(val, color) {
  return (
    <span style={{
      fontSize: "10px", padding: "2px 10px", borderRadius: "20px",
      background: color + "18", color, border: `1px solid ${color}35`,
      fontWeight: "600", whiteSpace: "nowrap",
    }}>{val}</span>
  );
}

const CG_ID_MAP = {
  "XBT/USD": "bitcoin", "ETH/USD": "ethereum", "SOL/USD": "solana",
  "ADA/USD": "cardano", "DOT/USD": "polkadot", "XRP/USD": "ripple",
  "LINK/USD": "chainlink", "LTC/USD": "litecoin", "BCH/USD": "bitcoin-cash",
  "XLM/USD": "stellar", "AVAX/USD": "avalanche-2", "ATOM/USD": "cosmos",
  "ALGO/USD": "algorand", "NEAR/USD": "near", "TRX/USD": "tron", "UNI/USD": "uniswap",
};

const ALL_PAIRS = Object.keys(CG_ID_MAP);

export default function Market({ prices }) {
  const [selected, setSelected]     = useState("XBT/USD");
  const [ohlcData, setOhlcData]     = useState([]);
  const [cgData, setCgData]         = useState({});
  const [aiAnalysis, setAiAnalysis] = useState(null);
  const [aiLoading, setAiLoading]   = useState(false);
  const [interval, setIntervalVal]  = useState(60);
  const [search, setSearch]         = useState("");
  const [view, setView]             = useState("chart");

  const sel    = prices[selected] || {};
  const closes = ohlcData.map((c) => c.price).filter(Boolean);
  const rsi    = closes.length >= 15 ? calcRSIFromCloses(closes) : calcRSI(sel.change24h || 0);
  const macd   = closes.length >= 27 ? calcMACDFromCloses(closes) : calcMACD(sel.change24h || 0);
  const boll   = calcBollinger(sel.change24h || 0);
  const score  = calcScore(sel.change24h || 0, rsi);

  const chartHistory = ohlcData.length > 0 ? ohlcData : (sel.history || []);
  const fib          = useMemo(() => fibFromHistory(chartHistory, 30), [chartHistory]);
  const elliott      = useMemo(() => estimateElliottWave(chartHistory), [chartHistory]);
  const cgCoin       = cgData[CG_ID_MAP[selected]];
  const logoUrl      = cgCoin?.image;
  const sparkline7d  = cgCoin?.sparkline_in_7d?.price;

  useEffect(() => {
    if (!selected) return;
    const pair = selected.replace("/", "");
    api.getOHLC(pair, interval)
      .then((data) => {
        const key = Object.keys(data)[0];
        if (!key) return;
        setOhlcData(data[key].map((c) => ({
          t: c[0], open: parseFloat(c[1]), high: parseFloat(c[2]),
          low: parseFloat(c[3]), price: parseFloat(c[4]), vol: parseFloat(c[6]),
        })).slice(-80));
      })
      .catch(() => setOhlcData(sel.history || []));
  }, [selected, interval]);

  useEffect(() => {
    api.getCoinGeckoMarkets().then((list) => {
      const map = {};
      list.forEach((coin) => { map[coin.id] = coin; });
      setCgData(map);
    }).catch(() => {});
  }, []);

  const filteredPairs = ALL_PAIRS.filter((p) =>
    !search || (prices[p]?.name || "").toLowerCase().includes(search.toLowerCase()) || p.toLowerCase().includes(search.toLowerCase())
  );

  // ✅ callAI pointe vers Railway
  const callAI = async () => {
    setAiLoading(true);
    setAiAnalysis(null);
    try {
      const symbol = selected.replace("/", "");
      const res = await fetch(`/api/analyze/${symbol}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          whale_score: 50,
          sentiment_score: 50,
        }),
      });
      const data = await res.json();
      setAiAnalysis(data);
    } catch (e) {
      setAiAnalysis({ error: e.message });
    }
    setAiLoading(false);
  };

  const renderAiAnalysis = () => {
    if (!aiAnalysis) return null;
    if (aiAnalysis.error) return <div style={{ color: THEME.red }}>{aiAnalysis.error}</div>;
    return (
      <div style={{ fontSize: "12px", lineHeight: "1.8", color: THEME.text2 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "10px" }}>
          {[
            { label: "PRIX", val: `€${aiAnalysis.price?.toLocaleString("fr", { maximumFractionDigits: 2 })}`, color: THEME.text },
            { label: "RÉGIME", val: aiAnalysis.regime, color: aiAnalysis.regime === "bull_trend" ? THEME.green : THEME.red },
            { label: "DÉCISION", val: aiAnalysis.decision?.toUpperCase(), color: aiAnalysis.decision === "execute" ? THEME.green : THEME.yellow },
            { label: "RSI", val: aiAnalysis.rsi?.toFixed(1), color: aiAnalysis.rsi > 70 ? THEME.red : aiAnalysis.rsi < 30 ? THEME.green : THEME.yellow },
            { label: "MARKET SCORE", val: aiAnalysis.scores?.market_score?.toFixed(1), color: THEME.purple },
            { label: "CONFIANCE", val: aiAnalysis.scores?.confidence_score?.toFixed(1), color: THEME.blue },
          ].map((item) => (
            <div key={item.label} style={{ background: "rgba(0,0,0,0.2)", borderRadius: "8px", padding: "8px" }}>
              <div style={{ fontSize: "9px", color: THEME.muted, marginBottom: "2px" }}>{item.label}</div>
              <div style={{ fontSize: "14px", fontWeight: "700", color: item.color }}>{item.val}</div>
            </div>
          ))}
        </div>
        {aiAnalysis.claude_analysis?.macro_comment && (
          <div style={{ padding: "10px", background: "rgba(139,92,246,0.08)", borderRadius: "8px", border: `1px solid ${THEME.borderP}`, fontStyle: "italic" }}>
            💬 {aiAnalysis.claude_analysis.macro_comment}
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ paddingBottom: "80px" }}>
      {/* Search */}
      <div style={{ padding: "12px 12px 0" }}>
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="🔍 Rechercher BTC, ETH, SOL…"
          style={{
            width: "100%", background: THEME.glass, border: `1px solid ${THEME.border}`,
            borderRadius: "12px", padding: "12px 16px", color: THEME.text,
            fontSize: "14px", fontFamily: "inherit", outline: "none",
          }} />
      </div>

      {/* Pair selector */}
      <div style={{ display: "flex", overflowX: "auto", padding: "10px 12px 4px", gap: "8px", scrollbarWidth: "none" }}>
        {filteredPairs.map((pair) => {
          const p = prices[pair]; const cg = cgData[CG_ID_MAP[pair]]; const active = pair === selected;
          return (
            <button key={pair} onClick={() => setSelected(pair)} style={{
              flexShrink: 0, minWidth: "80px", padding: "10px", borderRadius: "14px",
              border: active ? `1px solid ${THEME.purple}` : `1px solid ${THEME.border}`,
              background: active ? "rgba(139,92,246,0.12)" : THEME.glass,
              color: active ? THEME.purple : THEME.muted,
              cursor: "pointer", fontFamily: "inherit", textAlign: "center",
              boxShadow: active ? "0 0 16px rgba(139,92,246,0.2)" : "none",
            }}>
              {cg?.image && <img src={cg.image} alt={p?.name} style={{ width: "22px", height: "22px", borderRadius: "50%", display: "block", margin: "0 auto 4px" }} />}
              <div style={{ fontSize: "12px", fontWeight: "700" }}>{p?.name || pair}</div>
              {p && <div style={{ fontSize: "10px", color: p.change24h >= 0 ? THEME.green : THEME.red, marginTop: "2px" }}>{p.change24h >= 0 ? "▲" : "▼"}{Math.abs(p.change24h || 0).toFixed(1)}%</div>}
            </button>
          );
        })}
      </div>

      <div style={{ padding: "0 12px" }}>
        {/* Price header */}
        {sel.price && (
          <div style={{
            background: "linear-gradient(135deg, rgba(139,92,246,0.08), rgba(59,130,246,0.05))",
            border: `1px solid ${THEME.borderP}`, borderRadius: "18px", padding: "16px", marginBottom: "12px",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                {logoUrl && (
                  <div style={{ width: "44px", height: "44px", borderRadius: "14px", background: "rgba(255,255,255,0.08)", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
                    <img src={logoUrl} alt={sel.name} style={{ width: "36px", height: "36px", borderRadius: "50%" }} />
                  </div>
                )}
                <div>
                  <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "1px" }}>{selected}</div>
                  <div style={{ fontSize: "28px", fontWeight: "800", color: THEME.text, letterSpacing: "-1px" }}>
                    €{sel.price.toLocaleString("fr", { maximumFractionDigits: sel.price < 1 ? 4 : 2 })}
                  </div>
                  <div style={{ marginTop: "6px", display: "flex", gap: "6px", flexWrap: "wrap" }}>
                    {badge(`${sel.change24h >= 0 ? "+" : ""}${(sel.change24h || 0).toFixed(2)}% 24h`, sel.change24h >= 0 ? THEME.green : THEME.red)}
                    {cgCoin && badge(`#${cgCoin.market_cap_rank}`, THEME.muted)}
                  </div>
                </div>
              </div>
              <ScoreRing score={score} size={62} />
            </div>
          </div>
        )}

        {/* View toggle */}
        <div style={{ display: "flex", gap: "6px", marginBottom: "12px" }}>
          {[{ id: "chart", label: "📊 Graphique" }, { id: "fib", label: "🎯 Fibonacci" }, { id: "market", label: "📋 Marché" }].map(({ id, label }) => (
            <button key={id} onClick={() => setView(id)} style={{
              flex: 1, padding: "10px 4px", borderRadius: "10px",
              border: `1px solid ${view === id ? THEME.purple : THEME.border}`,
              background: view === id ? "rgba(139,92,246,0.12)" : THEME.glass,
              color: view === id ? THEME.purple : THEME.muted,
              fontFamily: "inherit", fontSize: "11px", cursor: "pointer", fontWeight: "700",
            }}>{label}</button>
          ))}
        </div>

        {/* CHART */}
        {view === "chart" && (
          <>
            <div style={{ display: "flex", gap: "6px", marginBottom: "10px" }}>
              {[{ v: 15, l: "15m" }, { v: 60, l: "1h" }, { v: 240, l: "4h" }, { v: 1440, l: "1j" }].map(({ v, l }) => (
                <button key={v} onClick={() => setIntervalVal(v)} style={{
                  flex: 1, padding: "8px", borderRadius: "8px",
                  border: `1px solid ${interval === v ? THEME.purple : THEME.border}`,
                  background: interval === v ? "rgba(139,92,246,0.12)" : THEME.glass,
                  color: interval === v ? THEME.purple : THEME.muted,
                  fontFamily: "inherit", fontSize: "11px", cursor: "pointer", fontWeight: "700",
                }}>{l}</button>
              ))}
            </div>

            <div style={{ background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "16px", padding: "14px", marginBottom: "12px" }}>
              <div style={{ height: "190px" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartHistory.slice(-80)} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="mktGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={THEME.purple} stopOpacity={0.3} />
                        <stop offset="95%" stopColor={THEME.purple} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="t" hide />
                    <YAxis domain={["auto", "auto"]} tickFormatter={(v) => v > 1000 ? `€${(v / 1000).toFixed(0)}k` : `€${parseFloat(v).toFixed(2)}`} tick={{ fill: THEME.muted, fontSize: 10 }} width={55} />
                    <Tooltip contentStyle={{ background: THEME.bg2, border: `1px solid ${THEME.borderP}`, borderRadius: "10px", fontSize: "12px" }} formatter={(v) => [`€${parseFloat(v).toLocaleString("fr", { maximumFractionDigits: 2 })}`, sel.name || selected]} />
                    <Area type="monotone" dataKey="price" stroke={THEME.purple} strokeWidth={2} fill="url(#mktGrad)" dot={false} activeDot={{ r: 4, fill: THEME.purple }} />
                    {fib && [
                      { y: fib.r618, color: THEME.green, label: "61.8%" },
                      { y: fib.r382, color: THEME.yellow, label: "38.2%" },
                      { y: fib.ext127, color: THEME.red, label: "127.2%" },
                    ].map((ref) => (
                      <ReferenceLine key={ref.label} y={ref.y} stroke={ref.color} strokeDasharray="3 3" strokeWidth={1}
                        label={{ value: ref.label, fill: ref.color, fontSize: 9, position: "insideTopRight" }} />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "8px", marginBottom: "12px" }}>
              {[
                { label: "RSI 14", val: rsi.toFixed(1), sub: rsi > 70 ? "SURACHAT" : rsi < 30 ? "SURVENTE" : "NEUTRE", color: rsi > 70 ? THEME.red : rsi < 30 ? THEME.green : THEME.yellow },
                { label: "MACD", val: typeof macd === "number" ? macd.toFixed(2) : macd, sub: macd > 0 ? "HAUSSIER" : "BAISSIER", color: macd > 0 ? THEME.green : THEME.red },
                { label: "BOLLINGER", val: boll.split(" ")[0], sub: boll.split(" ")[1] || "", color: THEME.blue },
              ].map((ind) => (
                <div key={ind.label} style={{ background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "14px", padding: "12px 8px", textAlign: "center" }}>
                  <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px" }}>{ind.label}</div>
                  <div style={{ fontSize: "18px", fontWeight: "800", color: ind.color, margin: "6px 0 4px" }}>{ind.val}</div>
                  {badge(ind.sub, ind.color)}
                </div>
              ))}
            </div>
          </>
        )}

        {/* FIBONACCI */}
        {view === "fib" && (
          <div style={{ background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "16px", padding: "16px", marginBottom: "12px" }}>
            {fib && sel.price && (
              <>
                <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "8px" }}>NIVEAUX FIBONACCI</div>
                {[
                  { label: "Résistance 127.2%", price: fib.ext127, type: "res" },
                  { label: "Résistance 100%",   price: fib.high,   type: "res" },
                  { label: "Support 61.8% ★",   price: fib.r618,   type: "sup" },
                  { label: "Support 50%",        price: fib.r500,   type: "sup" },
                  { label: "Support 38.2%",      price: fib.r382,   type: "sup" },
                ].sort((a, b) => b.price - a.price).map((l) => {
                  const dist = ((l.price - sel.price) / sel.price * 100).toFixed(1);
                  const color = l.type === "res" ? THEME.red : THEME.green;
                  return (
                    <div key={l.label} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: `1px solid ${THEME.border}` }}>
                      <span style={{ fontSize: "10px", color }}>{l.label}</span>
                      <div style={{ display: "flex", gap: "8px" }}>
                        <span style={{ fontSize: "10px", color: THEME.text, fontWeight: "600" }}>€{l.price > 100 ? l.price.toLocaleString("fr", { maximumFractionDigits: 0 }) : l.price.toFixed(4)}</span>
                        <span style={{ fontSize: "9px", color: parseFloat(dist) > 0 ? THEME.red : THEME.green }}>{parseFloat(dist) > 0 ? "▲" : "▼"}{Math.abs(dist)}%</span>
                      </div>
                    </div>
                  );
                })}
              </>
            )}
            {sparkline7d && (
              <div style={{ marginTop: "14px" }}>
                <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", marginBottom: "8px" }}>SPARKLINE 7 JOURS</div>
                <div style={{ height: "60px" }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={sparkline7d.map((p, i) => ({ t: i, price: p }))} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="sp7d" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={THEME.purple} stopOpacity={0.3} />
                          <stop offset="95%" stopColor={THEME.purple} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <Area type="monotone" dataKey="price" stroke={THEME.purple} strokeWidth={2} fill="url(#sp7d)" dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </div>
        )}

        {/* MARKET TABLE */}
        {view === "market" && (
          <div style={{ background: THEME.glass, border: `1px solid ${THEME.border}`, borderRadius: "16px", overflow: "hidden", marginBottom: "12px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: "8px", padding: "10px 14px", borderBottom: `1px solid ${THEME.border}`, background: "rgba(255,255,255,0.02)" }}>
              {["COIN", "PRIX", "24H", "RSI"].map((h) => (
                <div key={h} style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "1px", fontWeight: "700" }}>{h}</div>
              ))}
            </div>
            {filteredPairs.map((pair) => {
              const p = prices[pair]; const cg = cgData[CG_ID_MAP[pair]];
              if (!p) return null;
              const pRsi = calcRSI(p.change24h || 0);
              return (
                <div key={pair} onClick={() => { setSelected(pair); setView("chart"); }} style={{
                  display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: "8px",
                  padding: "12px 14px", borderBottom: `1px solid ${THEME.border}`,
                  cursor: "pointer",
                  background: selected === pair ? "rgba(139,92,246,0.06)" : "transparent",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    {cg?.image && <img src={cg.image} alt={p.name} style={{ width: "26px", height: "26px", borderRadius: "50%" }} />}
                    <div>
                      <div style={{ fontSize: "13px", color: THEME.text, fontWeight: "700" }}>{p.name}</div>
                      <div style={{ fontSize: "9px", color: THEME.muted }}>{cg ? `#${cg.market_cap_rank}` : ""}</div>
                    </div>
                  </div>
                  <div style={{ fontSize: "12px", color: THEME.text, textAlign: "right", fontWeight: "600" }}>€{p.price > 100 ? p.price.toLocaleString("fr", { maximumFractionDigits: 0 }) : p.price.toFixed(4)}</div>
                  <div style={{ fontSize: "11px", color: p.change24h >= 0 ? THEME.green : THEME.red, textAlign: "right", fontWeight: "600" }}>{p.change24h >= 0 ? "+" : ""}{(p.change24h || 0).toFixed(1)}%</div>
                  <div style={{ fontSize: "11px", color: pRsi > 70 ? THEME.red : pRsi < 30 ? THEME.green : THEME.yellow, textAlign: "right", fontWeight: "700" }}>{pRsi.toFixed(0)}</div>
                </div>
              );
            })}
          </div>
        )}

        {/* AI */}
        <div style={{ background: "linear-gradient(135deg, rgba(139,92,246,0.08), rgba(59,130,246,0.05))", border: `1px solid ${THEME.borderP}`, borderRadius: "16px", padding: "16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <div>
              <div style={{ fontSize: "10px", color: THEME.muted, letterSpacing: "1px" }}>⬡ ANALYSE IA 4 NIVEAUX</div>
              <div style={{ fontSize: "13px", color: THEME.purple, fontWeight: "700", marginTop: "2px" }}>Powered by Claude</div>
            </div>
            <ScoreRing score={score} size={46} />
          </div>

          {aiAnalysis ? (
            <div style={{ marginBottom: "12px", padding: "12px", background: "rgba(0,0,0,0.2)", borderRadius: "10px" }}>
              {renderAiAnalysis()}
            </div>
          ) : (
            <div style={{ fontSize: "11px", color: THEME.muted, fontStyle: "italic", marginBottom: "12px", lineHeight: "1.6" }}>
              L'IA analyse RSI, MACD, Fibonacci, Elliott Wave et le risque.<br />
              Elle vous dit OUI JE TRADE ou NON J'ATTENDS.
            </div>
          )}

          <button onClick={callAI} disabled={aiLoading} style={{
            width: "100%", padding: "14px", borderRadius: "12px",
            background: aiLoading ? THEME.glass : THEME.gradP,
            color: "#fff", border: "none", fontFamily: "inherit",
            fontSize: "13px", fontWeight: "700", cursor: aiLoading ? "not-allowed" : "pointer",
            opacity: aiLoading ? 0.6 : 1,
            boxShadow: aiLoading ? "none" : "0 4px 20px rgba(139,92,246,0.3)",
          }}>
            {aiLoading ? "⟳ Analyse en cours…" : "⬡ Lancer analyse IA complète"}
          </button>
        </div>
      </div>
    </div>
  );
}