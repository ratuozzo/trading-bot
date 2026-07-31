// Lead-lag edge measurement.
//
// The question this answers: when the fast feed (a perp, say) jumps, does the
// execution venue (Binance spot) actually follow — and by enough to pay the
// round trip?
//
// Method: when the signal feed moves at least `threshold` within `window`, we
// snapshot the exec venue's price at that instant, then re-read it at each
// horizon. Follow-through is signed by the signal's direction, so a positive
// number means spot moved the way the perp predicted.
//
// This is deliberately separate from trading. It measures whether the edge
// exists at all, before any money (even simulated) is at stake.

const now = () => performance.timeOrigin + performance.now();

export const DEFAULT_HORIZONS = [100, 250, 500, 1000, 2000];

export class LeadLagAnalyzer {
  constructor({
    horizons = DEFAULT_HORIZONS,
    threshold = 0.0003,   // 3bp signal move counts as an event
    window = 500,         // ...measured over 500ms
    maxSamples = 500,
  } = {}) {
    this.horizons = [...horizons].sort((a, b) => a - b);
    this.threshold = threshold;
    this.window = window;
    this.maxSamples = maxSamples;

    this.signalHistory = [];   // {ts, price}
    this.execHistory = [];     // {ts, price}
    this.pending = [];         // events awaiting their horizons
    this.samples = new Map();  // horizon -> number[] (signed bp)
    this.horizons.forEach((h) => this.samples.set(h, []));
    this._armed = 0;           // hysteresis latch, same idea as FeedRace
    this.events = 0;
  }

  configure({ threshold, window } = {}) {
    if (Number.isFinite(threshold)) this.threshold = threshold;
    if (Number.isFinite(window)) this.window = window;
  }

  onExecTick(price, ts) {
    this.execHistory.push({ ts, price });
    const cut = ts - (this.horizons[this.horizons.length - 1] + 2000);
    while (this.execHistory.length > 1 && this.execHistory[0].ts < cut) {
      this.execHistory.shift();
    }
    this._resolve(ts);
  }

  onSignalTick(price, ts) {
    this.signalHistory.push({ ts, price });
    const cut = ts - this.window;
    while (this.signalHistory.length > 1 && this.signalHistory[0].ts < cut) {
      this.signalHistory.shift();
    }

    const ref = this.signalHistory[0].price;
    if (!ref) return;
    const ret = (price - ref) / ref;
    const dir = ret >= this.threshold ? 1 : ret <= -this.threshold ? -1 : 0;

    if (dir === 0) {
      if (Math.abs(ret) < this.threshold * 0.5) this._armed = 0;
      return;
    }
    if (this._armed === dir) return;
    this._armed = dir;

    const execAt = this._execPriceAt(ts);
    if (execAt === null) return;   // exec venue hasn't reported yet

    this.events++;
    this.pending.push({
      ts, dir, execAt,
      remaining: new Set(this.horizons),
    });
    if (this.pending.length > 2000) this.pending.shift();
  }

  /** Most recent exec price at or before `ts`. */
  _execPriceAt(ts) {
    if (!this.execHistory.length) return null;
    for (let i = this.execHistory.length - 1; i >= 0; i--) {
      if (this.execHistory[i].ts <= ts) return this.execHistory[i].price;
    }
    return null; // all exec data is newer than the event
  }

  _resolve(nowTs) {
    if (!this.pending.length) return;
    const latestExec = this.execHistory[this.execHistory.length - 1];
    if (!latestExec) return;

    for (let i = this.pending.length - 1; i >= 0; i--) {
      const ev = this.pending[i];
      for (const h of [...ev.remaining]) {
        if (nowTs < ev.ts + h) continue;
        // Signed by direction: positive == spot followed the signal.
        const moveBp = ((latestExec.price - ev.execAt) / ev.execAt) * 10000 * ev.dir;
        const arr = this.samples.get(h);
        arr.push(moveBp);
        if (arr.length > this.maxSamples) arr.shift();
        ev.remaining.delete(h);
      }
      if (!ev.remaining.size) this.pending.splice(i, 1);
    }
  }

  /**
   * Per-horizon stats. `medianBp` is the typical follow-through; `hitRate` is
   * how often the exec venue moved the predicted way at all.
   */
  summary() {
    return this.horizons.map((h) => {
      const arr = this.samples.get(h);
      if (!arr.length) {
        return { horizon: h, samples: 0, medianBp: null, meanBp: null, hitRate: null };
      }
      const sorted = [...arr].sort((a, b) => a - b);
      const mid = Math.floor(sorted.length / 2);
      const median = sorted.length % 2
        ? sorted[mid]
        : (sorted[mid - 1] + sorted[mid]) / 2;
      const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
      const hits = arr.filter((v) => v > 0).length;
      return {
        horizon: h,
        samples: arr.length,
        medianBp: median,
        meanBp: mean,
        hitRate: hits / arr.length,
      };
    });
  }

  reset() {
    this.signalHistory = [];
    this.execHistory = [];
    this.pending = [];
    this.horizons.forEach((h) => this.samples.set(h, []));
    this._armed = 0;
    this.events = 0;
  }
}

/**
 * Round-trip cost in bp: taker fee both ways plus slippage both ways.
 * Follow-through has to clear this before the idea is worth anything.
 */
export function roundTripCostBp({ feeBps, slippageBps }) {
  return feeBps * 2 + slippageBps * 2;
}
