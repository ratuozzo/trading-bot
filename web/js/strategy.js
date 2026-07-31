// Momentum scalper — a faithful port of src/tradingbot/strategy/momentum.py.
// Keep the two in sync: same parameter names, same entry/exit rules.
//
// Timestamps here are LOCAL arrival times (ms), not exchange times. Exchange
// clocks can be skewed by seconds, which would corrupt the lookback windows.

export const SIGNAL = {
  ENTER_LONG: 'ENTER_LONG',
  EXIT_LONG: 'EXIT_LONG',
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
   * `tick.execPrice` is the price on the venue we actually trade, and defaults
   * to the signal price when both are the same feed.
   *
   * The split matters: a perp and a spot book differ by basis, so measuring
   * take-profit as (perp price vs spot entry) would be pure noise. Impulses
   * come from the signal feed; profit and loss comes from the exec feed.
   */
  onTick(tick, { inPosition, entryPrice }) {
    this._push(tick);
    const execPrice = tick.execPrice ?? tick.price;
    return inPosition
      ? this._exitDecision(tick, entryPrice, execPrice)
      : this._entryDecision(tick);
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
    if (ret >= this.cfg.entryThreshold) {
      return {
        type: SIGNAL.ENTER_LONG,
        reason: `impulse +${(ret * 100).toFixed(3)}% / ${this.cfg.lookbackSeconds}s`,
      };
    }
    return { type: SIGNAL.HOLD, reason: `no impulse (${(ret * 100).toFixed(3)}%)` };
  }

  _exitDecision(tick, entryPrice, execPrice) {
    if (!entryPrice) return { type: SIGNAL.HOLD, reason: 'no entry price' };
    // Take-profit and stop-loss are real money, so they read the exec venue.
    const change = (execPrice - entryPrice) / entryPrice;

    if (change >= this.cfg.takeProfit) {
      return { type: SIGNAL.EXIT_LONG, reason: `take profit +${(change * 100).toFixed(3)}%` };
    }
    if (change <= -this.cfg.stopLoss) {
      return { type: SIGNAL.EXIT_LONG, reason: `stop loss ${(change * 100).toFixed(3)}%` };
    }
    const recent = this._returnOver(this.cfg.reversalWindow, tick.ts, tick.price);
    if (recent <= -this.cfg.reversalExit) {
      return { type: SIGNAL.EXIT_LONG, reason: `reversal ${(recent * 100).toFixed(3)}%` };
    }
    if (this.entryTime !== null &&
        tick.ts - this.entryTime >= this.cfg.maxHoldSeconds * 1000) {
      return { type: SIGNAL.EXIT_LONG, reason: 'max hold time' };
    }
    return { type: SIGNAL.HOLD, reason: `holding (${(change * 100).toFixed(3)}%)` };
  }

  noteEntry(ts) { this.entryTime = ts; }
  noteExit(ts) { this.lastExitTime = ts; this.entryTime = null; }
  reset() { this.window = []; this.lastExitTime = -Infinity; this.entryTime = null; }
}
