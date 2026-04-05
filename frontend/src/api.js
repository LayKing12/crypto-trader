const BASE = "/api";

async function request(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const api = {
  getPrices: () => request("GET", "/prices"),
  getBalance: () => request("GET", "/balance"),
  getPortfolio: () => request("GET", "/portfolio"),
  getTrades: () => request("GET", "/trades"),
  getOrders: () => request("GET", "/orders"),
  getAlerts: () => request("GET", "/alerts"),
  getOHLC: (pair, interval = 60) =>
    request("GET", `/ohlc/${pair}?interval=${interval}`),

  placeTrade: (order) => request("POST", "/trade", order),
  cancelOrder: (txid) => request("DELETE", `/orders/${txid}`),

  sendWhatsApp: (phone, message, urgent = false) =>
    request("POST", "/whatsapp/send", { phone, message, urgent }),

  /** Legacy 2-arg AI call — kept for backward compatibility */
  getAIAnalysis: (portfolio, indicators) =>
    request("POST", "/ai-analysis", { portfolio, indicators }),

  /** Full 4-level AI analysis with Fibonacci + Elliott Wave + risk */
  getAIAnalysisFull: (payload) =>
    request("POST", "/ai-analysis", payload),

  /** CoinGecko market data: logos, sparklines 7j, 20 coins */
  getCoinGeckoMarkets: () => request("GET", "/coingecko/markets"),
};
