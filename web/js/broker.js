// Paper broker + portfolio + risk — ports of the Python modules of the same
// name. Simulated money only; nothing here can place a real order.
//
// Positions are directional: LONG profits when price rises, SHORT when it
// falls. Cash accounting is deliberately uniform across both — on open the
// notional is deducted (an outright purchase for a long, posted margin for a
// short), and on close the notional comes back adjusted by the position's
// P&L. That keeps one code path instead of two subtly different ones.

export const LONG = 'LONG';
export const SHORT = 'SHORT';

/** +1 for a long, -1 for a short. */
export const dirOf = (side) => (side === SHORT ? -1 : 1);

export class PaperBroker {
  constructor({ startingCash, feeBps, slippageBps }) {
    this.startingCash = startingCash;
    this.cash = startingCash;
    this.feeRate = feeBps / 10000;
    this.slippageRate = slippageBps / 10000;
    this.qty = 0;          // always >= 0; direction lives in `side`
    this.avgEntry = 0;
    this.side = null;
  }

  get inPosition() { return this.qty > 0; }
  get dir() { return this.side ? dirOf(this.side) : 0; }

  /**
   * Open a position worth roughly `quoteAmount`. Slippage always works
   * against us: we buy a touch higher, sell a touch lower.
   */
  open(side, quoteAmount, refPrice, ts) {
    if (this.inPosition || quoteAmount <= 0 || refPrice <= 0) return null;
    const spend = Math.min(quoteAmount, this.cash);
    if (spend <= 0) return null;

    const d = dirOf(side);
    const price = refPrice * (1 + d * this.slippageRate);
    const qty = spend / (price * (1 + this.feeRate));
    const notional = qty * price;
    const fee = notional * this.feeRate;

    this.cash -= notional + fee;
    this.qty = qty;
    this.avgEntry = price;
    this.side = side;
    return { side, action: 'OPEN', qty, price, fee, ts };
  }

  /** Close the whole position at ~refPrice. */
  close(refPrice, ts) {
    if (!this.inPosition || refPrice <= 0) return null;

    const d = this.dir;
    // Closing reverses the trade, so slippage flips sign too.
    const price = refPrice * (1 - d * this.slippageRate);
    const qty = this.qty;
    const notional = qty * price;
    const fee = notional * this.feeRate;
    const pnl = d * (price - this.avgEntry) * qty;

    // Return the notional put up at entry, adjusted by P&L, less the exit fee.
    this.cash += qty * this.avgEntry + pnl - fee;

    const fill = { side: this.side, action: 'CLOSE', qty, price, fee, ts };
    this.qty = 0;
    this.avgEntry = 0;
    this.side = null;
    return fill;
  }

  /** Signed P&L of the open position if marked at `markPrice`. */
  unrealized(markPrice) {
    if (!this.inPosition) return 0;
    return this.dir * (markPrice - this.avgEntry) * this.qty;
  }

  equity(markPrice) {
    if (!this.inPosition) return this.cash;
    return this.cash + this.qty * this.avgEntry + this.unrealized(markPrice);
  }
}

export class Portfolio {
  constructor(startingCash) {
    this.startingCash = startingCash;
    this.trades = [];
    this._open = null;
  }

  recordFill(fill, reason = '') {
    if (fill.action === 'OPEN') {
      this._open = {
        side: fill.side,
        qty: fill.qty,
        price: fill.price,
        ts: fill.ts,
        fee: fill.fee,
      };
      return null;
    }

    if (!this._open) return null;
    const o = this._open;
    const d = dirOf(o.side);
    const fees = o.fee + fill.fee;

    const trade = {
      side: o.side,
      qty: fill.qty,
      entryPrice: o.price,
      exitPrice: fill.price,
      entryTime: o.ts,
      exitTime: fill.ts,
      fees,
      pnl: d * (fill.price - o.price) * fill.qty - fees,
      reason,
    };
    this.trades.push(trade);
    this._open = null;
    return trade;
  }

  get realizedPnl() { return this.trades.reduce((a, t) => a + t.pnl, 0); }
  get wins() { return this.trades.filter((t) => t.pnl > 0).length; }
  get winRate() { return this.trades.length ? this.wins / this.trades.length : 0; }
}

export class RiskManager {
  constructor(cfg, startingEquity) {
    this.cfg = cfg;
    this.startingEquity = startingEquity;
    this.halted = false;
  }

  update(cfg) { this.cfg = cfg; }

  orderNotional(cash) {
    if (this.halted) return 0;
    const notional = cash * this.cfg.orderSizePct;
    return notional < this.cfg.minNotional ? 0 : notional;
  }

  updateEquity(equity) {
    if (this.startingEquity <= 0) return;
    const dd = (this.startingEquity - equity) / this.startingEquity;
    if (dd >= this.cfg.dailyLossLimitPct) this.halted = true;
  }
}
