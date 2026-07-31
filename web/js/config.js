// Default settings for the dashboard.
// These mirror `config.yaml` on the Python side — if you tune one, tune both.

export const DEFAULTS = {
  base: 'BTC',              // base asset; each feed maps it to its own symbol
  primaryFeed: 'binance-futures',
  racing: ['binance-futures', 'bybit', 'okx', 'binance-spot'],

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
    feeBps: 5.0,          // perp taker fees are typically lower than spot
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
    return {
      ...structuredClone(DEFAULTS),
      ...saved,
      strategy: { ...DEFAULTS.strategy, ...(saved.strategy || {}) },
      risk: { ...DEFAULTS.risk, ...(saved.risk || {}) },
      broker: { ...DEFAULTS.broker, ...(saved.broker || {}) },
    };
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
