export default function ScoreRing({ score, size = 64 }) {
  const r = size / 2 - 6;
  const circ = 2 * Math.PI * r;
  const fill = circ * (score / 100);
  const color = score > 65 ? "#00ff88" : score > 40 ? "#f0b429" : "#ff4d4d";
  const cx = size / 2, cy = size / 2;

  return (
    <svg width={size} height={size} style={{ transform: "rotate(-90deg)", flexShrink: 0 }}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="5" />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth="5"
        strokeDasharray={`${fill} ${circ}`} strokeLinecap="round"
        style={{ transition: "stroke-dasharray 0.6s ease, stroke 0.4s" }} />
      <text x={cx} y={cy + 5} textAnchor="middle" fill={color}
        style={{
          fontSize: `${size * 0.22}px`, fontWeight: "700",
          transform: `rotate(90deg) translate(0, -${size}px)`,
          fontFamily: "monospace",
        }}>
        {score}
      </text>
    </svg>
  );
}
