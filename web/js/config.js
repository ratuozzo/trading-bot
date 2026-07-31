// Default settings for the dashboard.
// These mirror `config.yaml` on the Python side — if you tune one, tune both.

export const DEFAULTS = {
  // Coins watched simultaneously. More coins means more chances to catch an
  // impulse — it does not improve any individual trade's odds, it just raises
  // how often a setup appears.
  //
  // MEASURED against MEXC, not guessed. A coin must print ~2 trades inside the
  // lookback window or its impulse reads 0.000% forever and it can never fire.
  // Most majors fail that on MEXC: XRP 0.34 trades/s, DOGE 0.29, BNB 0.11,
  // LTC 0.10. Regenerate with: python scripts/pick_symbols.py --lookback 2
  symbols: ['BTC', 'ETH', 'PEPE', 'HYPE', 'SOL'],

  // signalFeed drives the decisions; execFeed is where the (simulated) fills
  // happen. Same venue for plain momentum; different ones for a lead-lag idea.
  signalFeed: 'mexc-futures',
  execFeed: 'mexc-futures',
  racing: ['mexc-futures', 'bybit', 'okx'],

  leadlag: {
    thresholdBp: 3,     // signal move that counts as an event
    windowMs: 500,      // ...measured over this window
  },

  // These defaults are chosen so a winning trade is actually a win after
  // costs — NOT because they guarantee a profit. Nothing can do that.
  //
  // At an 8bp taker fee the round trip costs ~18bp. Take-profit sits well
  // clear of it and the stop is kept tight, which puts break-even at roughly
  // a 51% win rate: the strategy has to be a little better than a coin flip.
  // The old 0.20%/0.15% settings needed ~94% at these fees, i.e. never.
  //
  // There is a real tension here and no setting escapes it: fees push you
  // toward bigger targets, and bigger targets get hit less often. Widening
  // take-profit lowers the bar but also lowers how often you clear it.
  strategy: {
    // Shorter lookback = a stricter velocity filter: the same threshold has to
    // happen faster. Sub-second values are where cascades live.
    // 2s not 1s: at 1s only BTC/ETH/PEPE print often enough on MEXC to be
    // measurable. 2s admits SOL and HYPE for a modest loosening.
    lookbackSeconds: 2.0,
    allowShorts: true,        // trade impulses down as well as up
    entryThreshold: 0.0025,   // ±0.25% impulse in 1s — a genuine cascade
    takeProfit: 0.006,        // +0.60%, far enough above the ~0.18% round trip
    stopLoss: 0.0025,         // -0.25%, cut fast
    reversalExit: 0.0015,     // bail if momentum flips 0.15%
    reversalWindow: 1.0,
    maxHoldSeconds: 45,
    cooldownSeconds: 3,
  },

  risk: {
    // Ten coins share the book, so no single entry may hog the cash.
    maxConcurrentPositions: 3,
    // 'equity' keeps every position the same size; 'cash' sizes off remaining
    // cash and hands whichever coin fires first a much larger bet.
    positionSizing: 'equity',
    orderSizePct: 0.25,        // of equity, so 3 x 25% = 75% deployed
    minNotional: 10,
    dailyLossLimitPct: 0.05,
  },

  broker: {
    startingCash: 10000,
    // MEXC futures placed through the API: 0.08% taker as of Jun 2026. The
    // 0%/0.01% shown in the MEXC app does NOT apply to API orders. This must
    // match your real venue or the results will flatter the strategy.
    feeBps: 8.0,
    slippageBps: 2.0,
  },
};

/**
 * Taker fees in basis points, one way. Verified July 2026 — fee schedules move,
 * so re-check your account's fee page before trusting any result.
 *
 * The MEXC entries are the trap worth knowing about: MEXC advertises 0% maker
 * and near-zero futures fees, but orders sent through the API are billed on a
 * SEPARATE schedule that overrides the displayed rates, and API accounts are
 * excluded from the zero-fee promotions. That API schedule was raised three
 * times between March and June 2026 (futures maker 0.01% -> 0.06%), so treat
 * it as a moving target rather than an edge.
 *
 * Note these are all TAKER rates. An impulse strategy has to cross the spread,
 * so the 0% maker rates are not reachable by it — a post-only order either
 * misses the move or fills because the move reversed.
 */
export const FEE_PRESETS = [
  { id: 'mexc-api-futures', label: 'MEXC futures via API', bps: 8.0 },
  { id: 'mexc-api-futures-mx', label: 'MEXC futures API + MX (20% off)', bps: 6.4 },
  { id: 'binance-perp', label: 'Binance USD-M perp taker', bps: 5.0 },
  { id: 'mexc-spot', label: 'MEXC spot taker', bps: 5.0 },
  { id: 'binance-spot', label: 'Binance spot taker', bps: 10.0 },
  { id: 'custom', label: 'Custom', bps: null },
];

const KEY = 'tradingbot.settings.v1';

export function loadSettings() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return structuredClone(DEFAULTS);
    // Merge so newly-added defaults appear for existing users.
    const saved = JSON.parse(raw);
    const merged = {
      ...structuredClone(DEFAULTS),
      ...saved,
      strategy: { ...DEFAULTS.strategy, ...(saved.strategy || {}) },
      risk: { ...DEFAULTS.risk, ...(saved.risk || {}) },
      broker: { ...DEFAULTS.broker, ...(saved.broker || {}) },
      leadlag: { ...DEFAULTS.leadlag, ...(saved.leadlag || {}) },
    };
    // Older saves used a single `primaryFeed`.
    if (saved.primaryFeed && !saved.signalFeed) {
      merged.signalFeed = saved.primaryFeed;
      merged.execFeed = saved.primaryFeed;
    }
    delete merged.primaryFeed;
    return merged;
  } catch {
    return structuredClone(DEFAULTS);
  }
}

export function saveSettings(settings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    /* private browsing / quota — settings just won't persist */
  }
}
