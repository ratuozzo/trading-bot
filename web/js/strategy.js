// Momentum scalper — a faithful port of src/tradingbot/strategy/momentum.py.
// Keep the two in sync: same parameter names, same entry/exit rules.
//
// The idea: a sharp move over a short window tends to carry a little further.
// A violent impulse is a different animal from a slow drift of the same size —
// it usually means a cascade (stops, liquidations) that has further to run.
// So the window is the velocity filter: the same threshold over a shorter
// lookback demands a faster move.
//
// Timestamps here are LOCAL arrival times (ms), not exchange times. Exchange
// clocks can be skewed by seconds, which would corrupt the lookback windows.

export const SIGNAL = {
  ENTER_LONG: 'ENTER_LONG',
  ENTER_SHORT: 'ENTER_SHORT',
  EXIT: 'EXIT',
  HOLD: 'HOLD',
};

export class MomentumScalper {
  constructor(cfg) {
    this.cfg = cfg;
    this.window = [];        // [{ ts, price }] oldest → newest
    this.lastExitTime = -Infinity;
    this.entryTime = null;
  }

  update(cfg) { this.cfg = cfg; }

  /**
   * `tick.price` is the SIGNAL price (the fast feed driving decisions).
   * `tick.execPrice` is the price on the venue actually traded, defaulting to
   * the signal price when both are the same feed.
   *
   * The split matters: a perp and a spot book differ by basis, so measuring
   * take-profit as (perp price vs spot entry) would be pure noise. Impulses
   * come from the signal feed; profit and loss comes from the exec feed.
   *
   * `dir` is +1 when long, -1 when short, 0 when flat.
   */
  onTick(tick, { dir = 0, entryPrice = 0 } = {}) {
    this._push(tick);
    const execPrice = tick.execPrice ?? tick.price;
    return dir === 0
      ? this._entryDecision(tick)
      : this._exitDecision(tick, entryPrice, execPrice, dir);
  }

  _push(tick) {
    this.window.push({ ts: tick.ts, price: tick.price });
    const cutoff = tick.ts - this.cfg.lookbackSeconds * 1000;
    while (this.window.length > 1 && this.window[0].ts < cutoff) {
      this.window.shift();
    }
  }

  _returnOver(seconds, nowTs, nowPrice) {
    const cutoff = nowTs - seconds * 1000;
    let ref = null;
    for (const p of this.window) {
      if (p.ts >= cutoff) { ref = p.price; break; }
    }
    if (ref === null || ref === 0) return 0;
    return (nowPrice - ref) / ref;
  }

  _entryDecision(tick) {
    if (tick.ts - this.lastExitTime < this.cfg.cooldownSeconds * 1000) {
      return { type: SIGNAL.HOLD, reason: 'cooldown' };
    }
    const span = tick.ts - this.window[0].ts;
    if (span < this.cfg.lookbackSeconds * 1000 * 0.5) {
      return { type: SIGNAL.HOLD, reason: 'warming up' };
    }
    const ret = this._returnOver(this.cfg.lookbackSeconds, tick.ts, tick.price);
    const pct = (ret * 100).toFixed(3);
    const per = `${this.cfg.lookbackSeconds}s`;

    if (ret >= this.cfg.entryThreshold) {
      return { type: SIGNAL.ENTER_LONG, reason: `impulse +${pct}% / ${per}` };
    }
    if (this.cfg.allowShorts && ret <= -this.cfg.entryThreshold) {
      return { type: SIGNAL.ENTER_SHORT, reason: `impulse ${pct}% / ${per}` };
    }
    return { type: SIGNAL.HOLD, reason: `no impulse (${pct}%)` };
  }

  _exitDecision(tick, entryPrice, execPrice, dir) {
    if (!entryPrice) return { type: SIGNAL.HOLD, reason: 'no entry price' };

    // Signed so positive always means "in profit", whichever way we're facing.
    // Take-profit and stop-loss are real money, so they read the exec venue.
    const change = (dir * (execPrice - entryPrice)) / entryPrice;

    if (change >= this.cfg.takeProfit) {
      return { type: SIGNAL.EXIT, reason: `take profit +${(change * 100).toFixed(3)}%` };
    }
    if (change <= -this.cfg.stopLoss) {
      return { type: SIGNAL.EXIT, reason: `stop loss ${(change * 100).toFixed(3)}%` };
    }

    // A reversal is momentum turning against the position, so it also flips.
    const recent = dir * this._returnOver(this.cfg.reversalWindow, tick.ts, tick.price);
    if (recent <= -this.cfg.reversalExit) {
      return { type: SIGNAL.EXIT, reason: `reversal ${(recent * 100).toFixed(3)}%` };
    }

    if (this.entryTime !== null &&
        tick.ts - this.entryTime >= this.cfg.maxHoldSeconds * 1000) {
      return { type: SIGNAL.EXIT, reason: 'max hold time' };
    }
    return { type: SIGNAL.HOLD, reason: `holding (${(change * 100).toFixed(3)}%)` };
  }

  noteEntry(ts) { this.entryTime = ts; }
  noteExit(ts) { this.lastExitTime = ts; this.entryTime = null; }
  reset() { this.window = []; this.lastExitTime = -Infinity; this.entryTime = null; }
}
