/**
 * Risk Management Rules
 * ─ Max 5% of capital per trade
 * ─ Stop-loss at -7% minimum
 * ─ Max 10% portfolio in one asset
 * ─ Auto-cagnotte: 10% of each realised profit
 */

// ── POSITION SIZING ──────────────────────────────────────────────────────────

/**
 * Calculate maximum position size based on portfolio value
 * @param {number} portfolioEUR   - Total portfolio value in EUR
 * @param {number} currentPrice   - Entry price of the asset
 * @param {number} riskPct        - Max % of portfolio to risk (default 5%)
 * @param {number} stopLossPct    - Stop-loss % below entry (default 7%)
 */
export function calcPositionSize(portfolioEUR, currentPrice, riskPct = 0.05, stopLossPct = 0.07) {
  const maxEUR      = portfolioEUR * riskPct;           // €50 on €1000 portfolio
  const stopPrice   = currentPrice * (1 - stopLossPct); // Price where you exit
  const riskPerUnit = currentPrice - stopPrice;         // EUR at risk per unit
  const volume      = riskPerUnit > 0 ? (maxEUR * stopLossPct) / riskPerUnit : maxEUR / currentPrice;

  return {
    maxEUR:       parseFloat(maxEUR.toFixed(2)),
    stopPrice:    parseFloat(stopPrice.toFixed(4)),
    riskEUR:      parseFloat((maxEUR * stopLossPct).toFixed(2)),
    volume:       parseFloat(volume.toFixed(6)),
    leverage:     1, // Never use leverage — this app enforces spot only
  };
}

// ── PORTFOLIO EXPOSURE CHECKS ─────────────────────────────────────────────────

/**
 * Check if adding a position would exceed concentration rules
 * @param {number} portfolioEUR   - Total portfolio value
 * @param {number} assetCurrentEUR - Current value in this asset
 * @param {number} newTradeEUR     - Size of proposed trade
 * @param {number} maxConcentration - Max % in one asset (default 10%)
 */
export function checkConcentration(portfolioEUR, assetCurrentEUR, newTradeEUR, maxConcentration = 0.10) {
  const afterEUR = assetCurrentEUR + newTradeEUR;
  const pct      = portfolioEUR > 0 ? afterEUR / portfolioEUR : 0;
  const allowed  = pct <= maxConcentration;
  const remaining = Math.max(0, portfolioEUR * maxConcentration - assetCurrentEUR);

  return {
    allowed,
    currentPct:   parseFloat((assetCurrentEUR / portfolioEUR * 100).toFixed(1)),
    afterPct:     parseFloat((pct * 100).toFixed(1)),
    maxPct:       maxConcentration * 100,
    remainingEUR: parseFloat(remaining.toFixed(2)),
    warning:      !allowed ? `⚠️ Dépasse la limite de ${maxConcentration * 100}% du portefeuille` : null,
  };
}

// ── STOP-LOSS LEVELS ──────────────────────────────────────────────────────────

/**
 * Calculate stop-loss levels
 * Default: hard stop at -7%, trailing stop at -5%
 */
export function calcStopLoss(entryPrice, options = {}) {
  const { hard = 0.07, trailing = 0.05, fibSupport = null } = options;
  const hardStop     = entryPrice * (1 - hard);
  const trailingStop = entryPrice * (1 - trailing);

  // If Fibonacci support provided, set stop just below it
  const fibStop = fibSupport ? fibSupport * 0.99 : null;

  return {
    hard:     parseFloat(hardStop.toFixed(4)),
    trailing: parseFloat(trailingStop.toFixed(4)),
    fib:      fibStop ? parseFloat(fibStop.toFixed(4)) : null,
    // Recommended: use highest of hard or just below fib support
    recommended: fibStop && fibStop > hardStop ? parseFloat(fibStop.toFixed(4)) : parseFloat(hardStop.toFixed(4)),
  };
}

// ── CAGNOTTE (AUTO SAVINGS) ───────────────────────────────────────────────────

const CAGNOTTE_KEY = "crypto_cagnotte_eur";

/** Get cagnotte value from localStorage */
export function getCagnotte() {
  try {
    return parseFloat(localStorage.getItem(CAGNOTTE_KEY) || "0");
  } catch { return 0; }
}

/** Add to cagnotte: automatically set aside 10% of a realized profit */
export function addToCagnotte(profitEUR, rate = 0.10) {
  const contribution = parseFloat((profitEUR * rate).toFixed(2));
  if (contribution <= 0) return { contribution: 0, total: getCagnotte() };
  const current = getCagnotte();
  const total   = parseFloat((current + contribution).toFixed(2));
  try { localStorage.setItem(CAGNOTTE_KEY, String(total)); } catch {}
  return { contribution, total };
}

/** Reset cagnotte */
export function resetCagnotte() {
  try { localStorage.removeItem(CAGNOTTE_KEY); } catch {}
}

// ── TRADE LOG ─────────────────────────────────────────────────────────────────

const TRADES_KEY = "crypto_trade_log";

export function getTradeLog() {
  try {
    return JSON.parse(localStorage.getItem(TRADES_KEY) || "[]");
  } catch { return []; }
}

export function logTrade(trade) {
  const log = getTradeLog();
  log.unshift({ ...trade, id: Date.now(), ts: new Date().toISOString() });
  if (log.length > 200) log.pop();
  try { localStorage.setItem(TRADES_KEY, JSON.stringify(log)); } catch {}
}

// ── RISK SCORING ──────────────────────────────────────────────────────────────

/**
 * Composite risk score for a potential trade (0 = very risky, 100 = very safe)
 * Factors: RSI, portfolio concentration, market conditions
 */
export function calcRiskScore({ rsi, concentrationPct, change24h, portfolioExposurePct }) {
  let score = 100;

  // RSI risk
  if (rsi > 80) score -= 30;
  else if (rsi > 70) score -= 15;
  else if (rsi < 25) score += 10; // Oversold = opportunity

  // Concentration risk
  if (concentrationPct > 15) score -= 25;
  else if (concentrationPct > 10) score -= 15;

  // Volatility (24h change as proxy)
  if (Math.abs(change24h) > 10) score -= 20;
  else if (Math.abs(change24h) > 5) score -= 10;

  // Overall portfolio exposure
  if (portfolioExposurePct > 80) score -= 20;

  return Math.min(100, Math.max(0, score));
}

// ── VALIDATORS ────────────────────────────────────────────────────────────────

export function validateTrade({ volumeEUR, portfolioEUR, rsi, concentrationPct }) {
  const errors = [];
  const warnings = [];

  if (volumeEUR > portfolioEUR * 0.05) {
    errors.push(`Volume trop élevé — max 5% du capital (€${(portfolioEUR * 0.05).toFixed(0)})`);
  }
  if (rsi > 75) {
    warnings.push(`RSI ${rsi.toFixed(0)} — Zone de surachat. Considérez d'attendre.`);
  }
  if (concentrationPct > 10) {
    warnings.push(`Concentration ${concentrationPct}% — dépasse la règle des 10%.`);
  }

  return { valid: errors.length === 0, errors, warnings };
}
