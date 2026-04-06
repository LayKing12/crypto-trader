import { useState, useCallback, useEffect } from "react";
import { useWebSocket } from "./useWebSocket";

// Derived indicator helpers
export function calcRSI(change) {
  if (change > 3) return Math.min(78 + change * 2, 88);
  if (change > 0) return 50 + change * 5;
  if (change > -2) return 50 + change * 4;
  return Math.max(25 + change * 3, 18);
}
export function calcMACD(change) {
  return parseFloat((change * 1.4).toFixed(2));
}
export function calcBollinger(change) {
  if (change > 3) return "Expansion haute";
  if (change < -3) return "Expansion basse";
  return "Canal central";
}
export function calcScore(change, rsi) {
  const base = 50 + change * 4;
  const adj = rsi > 70 ? -15 : rsi < 35 ? 20 : 0;
  return Math.min(95, Math.max(5, Math.round(base + adj)));
}

function makeHistory(price, n = 50) {
  const data = [];
  let p = price * 0.88;
  for (let i = 0; i < n; i++) {
    p = p * (1 + (Math.random() - 0.48) * 0.025);
    data.push({ t: i, price: parseFloat(p.toFixed(4)) });
  }
  data[data.length - 1].price = price;
  return data;
}

const KRAKEN_MAP = {
  "XBT/USD":  "XBTUSD",
  "ETH/USD":  "ETHUSD",
  "SOL/USD":  "SOLUSD",
  "ADA/USD":  "ADAUSD",
  "DOT/USD":  "DOTUSD",
  "XRP/USD":  "XRPUSD",
  "LINK/USD": "LINKUSD",
  "LTC/USD":  "LTCUSD",
  "BCH/USD":  "BCHUSD",
  "XLM/USD":  "XLMUSD",
  "AVAX/USD": "AVAXUSD",
  "ATOM/USD": "ATOMUSD",
  "ALGO/USD": "ALGOUSD",
  "NEAR/USD": "NEARUSD",
  "TRX/USD":  "TRXUSD",
  "UNI/USD":  "UNIUSD",
};

const DEFAULT_PAIRS = {
  "XBT/USD":  { name: "BTC",  symbol: "₿",  color: "#f7931a", price: 84200,  change24h: 0, vol24h: 0 },
  "ETH/USD":  { name: "ETH",  symbol: "Ξ",  color: "#627eea", price: 2190,   change24h: 0, vol24h: 0 },
  "SOL/USD":  { name: "SOL",  symbol: "◎",  color: "#9945ff", price: 148,    change24h: 0, vol24h: 0 },
  "ADA/USD":  { name: "ADA",  symbol: "₳",  color: "#0033ad", price: 0.45,   change24h: 0, vol24h: 0 },
  "DOT/USD":  { name: "DOT",  symbol: "●",  color: "#e6007a", price: 7.2,    change24h: 0, vol24h: 0 },
  "XRP/USD":  { name: "XRP",  symbol: "✕",  color: "#00aae4", price: 0.52,   change24h: 0, vol24h: 0 },
  "LINK/USD": { name: "LINK", symbol: "⬡",  color: "#2a5ada", price: 14.5,   change24h: 0, vol24h: 0 },
  "LTC/USD":  { name: "LTC",  symbol: "Ł",  color: "#bfbbbb", price: 88,     change24h: 0, vol24h: 0 },
  "BCH/USD":  { name: "BCH",  symbol: "Ƀ",  color: "#8dc351", price: 370,    change24h: 0, vol24h: 0 },
  "XLM/USD":  { name: "XLM",  symbol: "✷",  color: "#7d9bcc", price: 0.11,   change24h: 0, vol24h: 0 },
  "AVAX/USD": { name: "AVAX", symbol: "△",  color: "#e84142", price: 36,     change24h: 0, vol24h: 0 },
  "ATOM/USD": { name: "ATOM", symbol: "⚛",  color: "#6f7390", price: 8.5,    change24h: 0, vol24h: 0 },
  "ALGO/USD": { name: "ALGO", symbol: "◈",  color: "#00d190", price: 0.22,   change24h: 0, vol24h: 0 },
  "NEAR/USD": { name: "NEAR", symbol: "Ν",  color: "#00c08b", price: 5.1,    change24h: 0, vol24h: 0 },
  "TRX/USD":  { name: "TRX",  symbol: "T",  color: "#ff0013", price: 0.13,   change24h: 0, vol24h: 0 },
  "UNI/USD":  { name: "UNI",  symbol: "♦",  color: "#ff007a", price: 8.2,    change24h: 0, vol24h: 0 },
};

export function usePrices() {
  const [prices, setPrices] = useState(() => {
    const init = {};
    for (const [pair, meta] of Object.entries(DEFAULT_PAIRS)) {
      init[pair] = { ...meta, history: makeHistory(meta.price) };
    }
    return init;
  });

  const handleMessage = useCallback((msg) => {
    if (msg.type === "snapshot") {
      setPrices((prev) => {
        const next = { ...prev };
        for (const [pair, tick] of Object.entries(msg.data)) {
          next[pair] = {
            ...prev[pair],
            ...tick,
            history: prev[pair]?.history || makeHistory(tick.price),
          };
        }
        return next;
      });
    } else if (msg.type === "ticker") {
      const tick = msg.data;
      setPrices((prev) => {
        const old = prev[tick.pair] || {};
        const newHist = old.history
          ? [...old.history.slice(1), { t: Date.now(), price: tick.price }]
          : makeHistory(tick.price);
        return {
          ...prev,
          [tick.pair]: { ...old, ...tick, history: newHist },
        };
      });
    }
  }, []);

  useWebSocket(handleMessage);

  // Fetch real prices + 24h change from Kraken every 60s
  useEffect(() => {
    const fetchKraken = async () => {
      try {
        const pairs = Object.values(KRAKEN_MAP).join(",");
        const res = await fetch(
          `https://api.kraken.com/0/public/Ticker?pair=${pairs}`
        );
        const json = await res.json();
        if (json.error?.length) return;

        setPrices((prev) => {
          const next = { ...prev };
          for (const [pair, krakenPair] of Object.entries(KRAKEN_MAP)) {
            // Kraken retourne parfois le pair avec un préfixe différent
            const resultKeys = Object.keys(json.result);
            const matchedKey = resultKeys.find((k) =>
              k.includes(krakenPair.slice(0, 3)) ||
              k === krakenPair ||
              k === "X" + krakenPair ||
              k === "XX" + krakenPair.slice(0, 3) + "Z" + krakenPair.slice(3)
            );
            const data = matchedKey ? json.result[matchedKey] : null;
            if (!data) continue;

            const price    = parseFloat(data.c[0]);  // dernier prix
            const open     = parseFloat(data.o);      // prix ouverture 24h
            const vol24h   = parseFloat(data.v[1]);   // volume 24h
            const change24h = parseFloat(
              (((price - open) / open) * 100).toFixed(2)
            );

            next[pair] = {
              ...prev[pair],
              price,
              change24h,
              vol24h,
              history: prev[pair]?.history || makeHistory(price),
            };
          }
          return next;
        });
      } catch (e) {
        console.warn("[Kraken ticker] erreur:", e);
      }
    };

    fetchKraken();
    const id = setInterval(fetchKraken, 60000);
    return () => clearInterval(id);
  }, []);

  return prices;
}