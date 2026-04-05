const crypto = require("crypto");
const https = require("https");
const querystring = require("querystring");

// Minimum order volumes per Kraken rules (as of 2025)
const MIN_VOLUMES = {
  XBTUSD: 0.0001,
  ETHUSD: 0.002,
  SOLUSD: 0.5,
  ADAUSD: 10,
  DOTUSD: 1,
  XXBTZUSD: 0.0001,
  XETHZUSD: 0.002,
};

class KrakenRest {
  constructor(apiKey, apiSecret) {
    this.apiKey = apiKey || "";
    this.apiSecret = apiSecret || "";
    this.host = "api.kraken.com";
  }

  // HMAC-SHA512 signature required by Kraken private API
  _sign(path, nonce, postData) {
    const message = nonce + postData;
    const secretBuf = Buffer.from(this.apiSecret, "base64");
    const sha256 = crypto.createHash("sha256").update(message).digest();
    const hmac = crypto
      .createHmac("sha512", secretBuf)
      .update(Buffer.concat([Buffer.from(path), sha256]))
      .digest("base64");
    return hmac;
  }

  _request(method, path, params = {}) {
    return new Promise((resolve, reject) => {
      const isPrivate = path.startsWith("/0/private/");
      let postData = "";
      const headers = { "User-Agent": "CryptoTrader/1.0" };

      if (isPrivate) {
        if (!this.apiKey || !this.apiSecret) {
          return reject(new Error("API key/secret not configured"));
        }
        const nonce = String(Date.now() * 1000);
        const body = { nonce, ...params };
        postData = querystring.stringify(body);
        headers["Content-Type"] = "application/x-www-form-urlencoded";
        headers["Content-Length"] = Buffer.byteLength(postData);
        headers["API-Key"] = this.apiKey;
        headers["API-Sign"] = this._sign(path, nonce, postData);
      }

      const url = method === "GET" && Object.keys(params).length
        ? `${path}?${querystring.stringify(params)}`
        : path;

      const options = { hostname: this.host, port: 443, path: url, method, headers };

      const req = https.request(options, (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            const parsed = JSON.parse(data);
            if (parsed.error && parsed.error.length > 0) {
              return reject(new Error(parsed.error.join(", ")));
            }
            resolve(parsed.result);
          } catch {
            reject(new Error("Invalid JSON from Kraken"));
          }
        });
      });

      req.on("error", reject);
      if (postData) req.write(postData);
      req.end();
    });
  }

  // ── Public endpoints ──────────────────────────────────────────────────────

  getTicker(pairs) {
    return this._request("GET", "/0/public/Ticker", { pair: pairs.join(",") });
  }

  getOHLC(pair, interval = 60) {
    return this._request("GET", "/0/public/OHLC", { pair, interval });
  }

  getOrderBook(pair, count = 10) {
    return this._request("GET", "/0/public/Depth", { pair, count });
  }

  // ── Private endpoints ─────────────────────────────────────────────────────

  getBalance() {
    return this._request("POST", "/0/private/Balance");
  }

  getTradeBalance(asset = "ZUSD") {
    return this._request("POST", "/0/private/TradeBalance", { asset });
  }

  getOpenOrders() {
    return this._request("POST", "/0/private/OpenOrders");
  }

  getTradeHistory(start, end) {
    const params = {};
    if (start) params.start = start;
    if (end) params.end = end;
    return this._request("POST", "/0/private/TradesHistory", params);
  }

  addOrder({ pair, type, ordertype, volume, price }) {
    const pairKey = pair.replace("/", "");
    const minVol = MIN_VOLUMES[pairKey] || 0;
    if (parseFloat(volume) < minVol) {
      return Promise.reject(
        new Error(`Volume minimum pour ${pair}: ${minVol}`)
      );
    }
    const params = { pair, type, ordertype, volume: String(volume) };
    if (price) params.price = String(price);
    return this._request("POST", "/0/private/AddOrder", params);
  }

  cancelOrder(txid) {
    return this._request("POST", "/0/private/CancelOrder", { txid });
  }
}

module.exports = KrakenRest;
