import { THEME } from "../theme";

const TABS = [
  { id: "portfolio", label: "Portfolio", icon: "💼" },
  { id: "trade",     label: "Trade",     icon: "⚡" },
  { id: "market",    label: "Marché",    icon: "📊" },
  { id: "alerts",    label: "Alertes",   icon: "🔔" },
  { id: "learn",     label: "Apprendre", icon: "📚" },
];

export default function BottomNav({ active, onChange }) {
  return (
    <nav style={{
      position: "fixed", bottom: 0, left: 0, right: 0,
      background: `linear-gradient(180deg, ${THEME.bg2} 0%, #09090f 100%)`,
      borderTop: `1px solid ${THEME.borderP}`,
      display: "flex", zIndex: 100,
      paddingBottom: "env(safe-area-inset-bottom)",
    }}>
      {TABS.map((tab) => {
        const isActive = active === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            style={{
              flex: 1, minHeight: "64px", border: "none",
              background: "transparent", cursor: "pointer",
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              gap: "3px", position: "relative",
              transition: "all 0.2s", fontFamily: "inherit",
            }}
          >
            {isActive && (
              <div style={{
                position: "absolute", top: 0, left: "15%", right: "15%",
                height: "2px", borderRadius: "0 0 4px 4px",
                background: THEME.gradP,
                boxShadow: "0 0 12px rgba(139,92,246,0.8)",
              }} />
            )}
            <div style={{
              width: "38px", height: "38px", borderRadius: "12px",
              display: "flex", alignItems: "center", justifyContent: "center",
              background: isActive ? "rgba(139,92,246,0.15)" : "transparent",
              border: isActive ? "1px solid rgba(139,92,246,0.35)" : "1px solid transparent",
              transition: "all 0.2s",
              boxShadow: isActive ? "0 0 16px rgba(139,92,246,0.25)" : "none",
            }}>
              <span style={{ fontSize: "18px", lineHeight: 1 }}>{tab.icon}</span>
            </div>
            <span style={{
              fontSize: "9px", letterSpacing: "0.5px",
              textTransform: "uppercase",
              color: isActive ? THEME.purple : THEME.muted,
              fontWeight: isActive ? "700" : "400",
              transition: "color 0.2s",
            }}>{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}