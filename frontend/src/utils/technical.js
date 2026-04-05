/**
 * Technical Analysis Utilities
 * RSI from real OHLC closes, Fibonacci, Elliott Wave (simplified), MACD
 */

// ── RSI ──────────────────────────────────────────────────────────────────────

/** Real RSI from an array of close prices (standard Wilder smoothing) */
export function calcRSIFromCloses(closes, period = 14) {
  if (closes.length < period + 1) return 50;

  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) gains += d;
    else losses -= d;
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;

  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
  }

  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return parseFloat((100 - 100 / (1 + rs)).toFixed(2));
}

/** Fast RSI approximation from 24h % change (used as fallback when no OHLC) */
export function calcRSI(change) {
  if (change > 3) return Math.min(78 + change * 2, 88);
  if (change > 0) return 50 + change * 5;
  if (change > -2) return 50 + change * 4;
  return Math.max(25 + change * 3, 18);
}

/** MACD approximation */
export function calcMACD(change) {
  return parseFloat((change * 1.4).toFixed(2));
}

/** MACD from two EMA arrays (real) */
export function calcMACDFromCloses(closes) {
  const ema12 = calcEMA(closes, 12);
  const ema26 = calcEMA(closes, 26);
  if (ema12 === null || ema26 === null) return 0;
  return parseFloat((ema12 - ema26).toFixed(4));
}

function calcEMA(closes, period) {
  if (closes.length < period) return null;
  const k = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k);
  }
  return ema;
}

/** Bollinger band status */
export function calcBollinger(change) {
  if (change > 3) return "Expansion haute";
  if (change < -3) return "Expansion basse";
  return "Canal central";
}

/** Opportunity score */
export function calcScore(change, rsi) {
  const base = 50 + change * 4;
  const adj = rsi > 70 ? -15 : rsi < 35 ? 20 : 0;
  return Math.min(95, Math.max(5, Math.round(base + adj)));
}

// ── FIBONACCI ─────────────────────────────────────────────────────────────────

/**
 * Fibonacci retracement + extension levels
 * @param {number} low  - Swing low
 * @param {number} high - Swing high
 * @returns {object}    - All key Fibonacci price levels
 */
export function calcFibonacci(low, high) {
  const diff = high - low;
  const fmt = (v) => parseFloat(v.toFixed(4));
  return {
    low,
    high,
    diff,
    // Retracement levels (support zones on pullback)
    r236: fmt(high - diff * 0.236),
    r382: fmt(high - diff * 0.382),
    r500: fmt(high - diff * 0.500),
    r618: fmt(high - diff * 0.618),  // Golden ratio — strongest support
    r786: fmt(high - diff * 0.786),
    r100: fmt(low),
    // Extension levels (profit targets)
    ext127: fmt(high + diff * 0.272),  // 1.272 extension — Wave 3 minimum
    ext162: fmt(high + diff * 0.618),  // 1.618 extension — Wave 3 ideal target
    ext200: fmt(high + diff * 1.000),  // 2.000 extension — strong breakout target
    ext262: fmt(high + diff * 1.618),  // 2.618 extension — Wave 5 target
  };
}

/**
 * Get Fibonacci levels from a price history array
 * Uses rolling 20-period high/low as swing reference
 */
export function fibFromHistory(history, lookback = 30) {
  if (!history || history.length < 10) return null;
  const slice = history.slice(-lookback);
  const prices = slice.map((c) => c.price || c.close || c[4] || 0).filter(Boolean);
  if (!prices.length) return null;
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  return calcFibonacci(low, high);
}

/**
 * Identify nearest Fibonacci support/resistance
 */
export function nearestFibLevel(currentPrice, fib) {
  if (!fib) return null;
  const levels = [
    { label: "Support 61.8%", price: fib.r618, type: "support" },
    { label: "Support 50%", price: fib.r500, type: "support" },
    { label: "Support 38.2%", price: fib.r382, type: "support" },
    { label: "Support 23.6%", price: fib.r236, type: "support" },
    { label: "Cible 127.2%", price: fib.ext127, type: "resistance" },
    { label: "Cible 161.8%", price: fib.ext162, type: "resistance" },
  ];
  return levels.reduce((best, lvl) => {
    const dist = Math.abs(lvl.price - currentPrice);
    return !best || dist < Math.abs(best.price - currentPrice) ? lvl : best;
  }, null);
}

// ── ELLIOTT WAVE ──────────────────────────────────────────────────────────────

/**
 * Simplified Elliott Wave position estimator
 * Returns estimated wave, confidence, and next price target
 *
 * Theory (5-wave impulse):
 * Wave 1: Initial impulse up
 * Wave 2: Retracement 50-61.8% of Wave 1
 * Wave 3: 1.618× Wave 1 height (strongest move, never shortest)
 * Wave 4: Retracement 23.6-38.2% of Wave 3
 * Wave 5: ~1× Wave 1 height
 */
export function estimateElliottWave(history) {
  if (!history || history.length < 40) {
    return { wave: "?", target: null, confidence: 0, description: "Données insuffisantes" };
  }

  const prices = history.map((c) => c.price || c.close || parseFloat(c[4]) || 0).filter(Boolean);
  const n = prices.length;
  const current = prices[n - 1];

  // Use different lookback periods to estimate wave structure
  const recent20 = prices.slice(-20);
  const prev20 = prices.slice(-40, -20);

  const recentHigh = Math.max(...recent20);
  const recentLow = Math.min(...recent20);
  const prevHigh = Math.max(...prev20);
  const prevLow = Math.min(...prev20);

  const recentRange = recentHigh - recentLow;
  const prevRange = prevHigh - prevLow;

  const posInRecent = (current - recentLow) / (recentRange || 1);
  const retracement = (recentHigh - current) / (recentRange || 1);

  // Price relative to previous range
  const isBreakingOut = current > prevHigh;
  const isInPrevRange = current >= prevLow && current <= prevHigh;

  let wave, target, confidence, description;

  if (isBreakingOut && posInRecent > 0.8) {
    // Strong upward breakout from previous high
    wave = "3";
    target = recentHigh + recentRange * 0.618; // 1.618 extension
    confidence = 72;
    description = "Vague 3 probable — momentum fort, pas de surachat encore";
  } else if (posInRecent > 0.6 && posInRecent < 0.85) {
    wave = "1 ou 3";
    target = recentHigh + recentRange * 0.272; // 1.272 extension
    confidence = 55;
    description = "Phase haussière — vague impulsive en cours";
  } else if (retracement >= 0.382 && retracement <= 0.618 && prevHigh > recentHigh) {
    // Retraced 38-62% — likely wave 2 or 4
    wave = "2 ou 4";
    target = recentHigh; // Target is to reclaim recent high
    confidence = 60;
    description = "Correction en cours — attendre confirmation reprise";
  } else if (retracement > 0.618) {
    wave = "2 (profonde)";
    target = recentLow + recentRange * 1.618;
    confidence = 50;
    description = "Correction profonde — risque de structure alternative";
  } else if (posInRecent < 0.3 && isInPrevRange) {
    wave = "4 ou B";
    target = recentLow + recentRange * 1.272;
    confidence = 45;
    description = "Zone de consolidation — patience requise";
  } else {
    wave = "5 ou A";
    target = recentHigh + recentRange * 0.272;
    confidence = 40;
    description = "Phase terminale ou début de correction";
  }

  return { wave, target: parseFloat(target.toFixed(4)), confidence, description, recentHigh, recentLow };
}

// ── EXIT STRATEGY (10/20/20/40) ───────────────────────────────────────────────

/**
 * Calculate exit levels using the 10/20/20/40 strategy
 * At each profit target, sell that % of remaining position
 *
 * The percentages refer to gains at which to take profits,
 * and what fraction of the position to sell at each level.
 */
export function calcExitLevels(entryPrice) {
  return [
    { step: 1, gainPct: 10,  sellPct: 10, price: entryPrice * 1.10,  label: "Sécuriser départ" },
    { step: 2, gainPct: 20,  sellPct: 20, price: entryPrice * 1.20,  label: "Réduire risque" },
    { step: 3, gainPct: 35,  sellPct: 20, price: entryPrice * 1.35,  label: "Cagnotter profits" },
    { step: 4, gainPct: 50,  sellPct: 40, price: entryPrice * 1.50,  label: "Sortie principale" },
    { step: 5, gainPct: 100, sellPct: 10, price: entryPrice * 2.00,  label: "Moonbag (optionnel)" },
  ];
}

/** Which exit step the current price has reached */
export function currentExitStep(entryPrice, currentPrice) {
  const levels = calcExitLevels(entryPrice);
  const gained = ((currentPrice - entryPrice) / entryPrice) * 100;
  for (let i = levels.length - 1; i >= 0; i--) {
    if (gained >= levels[i].gainPct) return levels[i].step;
  }
  return 0;
}
