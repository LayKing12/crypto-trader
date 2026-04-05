import { useState, useEffect } from "react";
import { THEME } from "./theme";
import { usePrices } from "./hooks/usePrices";
import BottomNav from "./components/BottomNav";
import Portfolio from "./tabs/Portfolio";
import Trade from "./tabs/Trade";
import Market from "./tabs/Market";
import Alertes from "./tabs/Alertes";
import Apprendre from "./tabs/Apprendre";

function Header({ prices }) {
  const [time, setTime] = useState(new Date().toLocaleTimeString("fr"));
  useEffect(() => {
    const iv = setInterval(() => setTime(new Date().toLocaleTimeString("fr")), 1000);
    return () => clearInterval(iv);
  }, []);

  const entries = Object.values(prices);

  return (
    <header style={{
      background: `linear-gradient(180deg, ${THEME.bg2} 0%, ${THEME.bg} 100%)`,
      borderBottom: `1px solid ${THEME.borderP}`,
      position: "sticky", top: 0, zIndex: 50,
    }}>
      <div style={{
        padding: "12px 16px",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div style={{
            width: "34px", height: "34px", borderRadius: "10px",
            background: THEME.gradP,
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 0 20px rgba(139,92,246,0.5)",
            fontSize: "18px",
          }}>⬡</div>
          <div>
            <div style={{
              fontSize: "15px", fontWeight: "800", letterSpacing: "1px",
              background: THEME.gradP,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
            }}>CRYPTOMIND</div>
            <div style={{ fontSize: "9px", color: THEME.muted, letterSpacing: "2px" }}>
              AI TRADING ENGINE
            </div>
          </div>
          <span style={{
            background: "rgba(16,185,129,0.12)", color: THEME.green,
            fontSize: "9px", padding: "3px 8px", borderRadius: "20px",
            border: "1px solid rgba(16,185,129,0.3)", letterSpacing: "1px",
          }}>● LIVE</span>
        </div>
        <span style={{
          fontSize: "11px", color: THEME.muted,
          background: THEME.glass, padding: "4px 10px",
          borderRadius: "8px", border: `1px solid ${THEME.border}`,
        }}>{time}</span>
      </div>

      <div style={{
        display: "flex", overflowX: "auto",
        background: "rgba(0,0,0,0.3)",
        borderTop: `1px solid ${THEME.border}`,
        scrollbarWidth: "none",
      }}>
        {entries.map((p) => (
          <div key={p.pair} style={{
            flexShrink: 0, padding: "5px 14px",
            borderRight: `1px solid ${THEME.border}`,
            fontSize: "11px", whiteSpace: "nowrap",
          }}>
            <span style={{ color: THEME.muted }}>{p.name} </span>
            <span style={{ color: THEME.text, fontWeight: "600" }}>
              ${p.price > 100
                ? p.price.toLocaleString("fr", { maximumFractionDigits: 0 })
                : p.price.toFixed(4)}
            </span>
            <span style={{
              marginLeft: "4px", fontSize: "10px",
              color: p.change24h >= 0 ? THEME.green : THEME.red,
            }}>
              {p.change24h >= 0 ? "▲" : "▼"}{Math.abs(p.change24h).toFixed(2)}%
            </span>
          </div>
        ))}
      </div>

      <style>{`
        ::-webkit-scrollbar { display: none; }
        * { -webkit-tap-highlight-color: transparent; box-sizing: border-box; }
        body { background: ${THEME.bg}; margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
      `}</style>
    </header>
  );
}

export default function App() {
  const [tab, setTab] = useState("portfolio");
  const prices = usePrices();

  return (
    <div style={{
      height: "100dvh", display: "flex", flexDirection: "column",
      background: THEME.bg, overflow: "hidden",
    }}>
      <Header prices={prices} />
      <div style={{
        flex: 1, overflowY: "auto", overflowX: "hidden",
        WebkitOverflowScrolling: "touch",
      }}>
        {tab === "portfolio" && <Portfolio prices={prices} />}
        {tab === "trade"     && <Trade prices={prices} />}
        {tab === "market"    && <Market prices={prices} />}
        {tab === "alerts"    && <Alertes prices={prices} />}
        {tab === "learn"     && <Apprendre />}
      </div>
      <BottomNav active={tab} onChange={setTab} />
    </div>
  );
}