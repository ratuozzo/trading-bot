// Default settings for the dashboard.
// These mirror `config.yaml` on the Python side — if you tune one, tune both.

export const DEFAULTS = {
  base: 'BTC',              // base asset; each feed maps it to its own symbol

  // signalFeed drives the decisions; execFeed is where the (simulated) fills
  // happen. Set them to the same venue for plain single-venue momentum, or to
  // different ones to trade a lead-lag idea (fast perp signal -> spot fills).
  signalFeed: 'binance-futures',
  execFeed: 'binance-spot',
  racing: ['binance-futures', 'bybit', 'okx', 'binance-spot'],

  leadlag: {
    thresholdBp: 3,     // signal move that counts as an event
    windowMs: 500,      // ...measured over this window
  },

  strategy: {
    lookbackSeconds: 5.0,
    entryThreshold: 0.0015,   // +0.15% impulse to go long
    takeProfit: 0.002,        // +0.20%
    stopLoss: 0.0015,         // -0.15%
    reversalExit: 0.0008,     // bail on a -0.08% flip
    reversalWindow: 1.5,
    maxHoldSeconds: 60,
    cooldownSeconds: 2,
  },

  risk: {
    orderSizePct: 0.95,
    minNotional: 10,
    dailyLossLimitPct: 0.05,
  },

  broker: {
    startingCash: 10000,
    // Binance SPOT taker is 0.10% (7.5bp with the BNB discount). Perps are
    // cheaper. This must match wherever execFeed points, or the simulated
    // results will flatter the strategy.
    feeBps: 10.0,
    slippageBps: 2.0,
  },
};

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
