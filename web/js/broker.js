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
    // symbol -> { qty, avgEntry, side }. Watching ten coins means up to ten
    // independent positions, so a single position field no longer suffices.
    this.positions = new Map();
  }

  position(symbol) {
    return this.positions.get(symbol) || { qty: 0, avgEntry: 0, side: null };
  }

  inPosition(symbol) { return this.position(symbol).qty > 0; }

  dir(symbol) {
    const p = this.position(symbol);
    return p.side ? dirOf(p.side) : 0;
  }

  get openCount() { return this.positions.size; }
  get openSymbols() { return [...this.positions.keys()]; }

  /**
   * Open a position worth roughly `quoteAmount`. Slippage always works
   * against us: we buy a touch higher, sell a touch lower.
   */
  open(symbol, side, quoteAmount, refPrice, ts) {
    if (this.inPosition(symbol) || quoteAmount <= 0 || refPrice <= 0) return null;
    const spend = Math.min(quoteAmount, this.cash);
    if (spend <= 0) return null;

    const d = dirOf(side);
    const price = refPrice * (1 + d * this.slippageRate);
    const qty = spend / (price * (1 + this.feeRate));
    const notional = qty * price;
    const fee = notional * this.feeRate;

    this.cash -= notional + fee;
    this.positions.set(symbol, { qty, avgEntry: price, side });
    return { symbol, side, action: 'OPEN', qty, price, fee, ts };
  }

  /** Close the whole position in `symbol` at ~refPrice. */
  close(symbol, refPrice, ts) {
    const pos = this.positions.get(symbol);
    if (!pos || pos.qty <= 0 || refPrice <= 0) return null;

    const d = dirOf(pos.side);
    // Closing reverses the trade, so slippage flips sign too.
    const price = refPrice * (1 - d * this.slippageRate);
    const qty = pos.qty;
    const notional = qty * price;
    const fee = notional * this.feeRate;
    const pnl = d * (price - pos.avgEntry) * qty;

    // Return the notional put up at entry, adjusted by P&L, less the exit fee.
    this.cash += qty * pos.avgEntry + pnl - fee;

    const fill = { symbol, side: pos.side, action: 'CLOSE', qty, price, fee, ts };
    this.positions.delete(symbol);
    return fill;
  }

  /** Signed P&L of one open position if marked at `markPrice`. */
  unrealized(symbol, markPrice) {
    const pos = this.positions.get(symbol);
    if (!pos || pos.qty <= 0) return 0;
    return dirOf(pos.side) * (markPrice - pos.avgEntry) * pos.qty;
  }

  /** `marks` maps symbol -> latest price. Unpriced positions hold at entry. */
  equity(marks = {}) {
    let total = this.cash;
    for (const [symbol, pos] of this.positions) {
      total += pos.qty * pos.avgEntry;
      const mark = marks[symbol];
      if (mark) total += this.unrealized(symbol, mark);
    }
    return total;
  }
}

export class Portfolio {
  constructor(startingCash) {
    this.startingCash = startingCash;
    this.trades = [];
    this._open = new Map();   // symbol -> open leg
  }

  recordFill(fill, reason = '') {
    if (fill.action === 'OPEN') {
      this._open.set(fill.symbol, {
        side: fill.side,
        qty: fill.qty,
        price: fill.price,
        ts: fill.ts,
        fee: fill.fee,
      });
      return null;
    }

    const o = this._open.get(fill.symbol);
    if (!o) return null;
    const d = dirOf(o.side);
    const fees = o.fee + fill.fee;

    const trade = {
      symbol: fill.symbol,
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
    this._open.delete(fill.symbol);
    return trade;
  }

  /** Per-symbol realised P&L, so you can see which coins actually work. */
  bySymbol() {
    const map = new Map();
    for (const t of this.trades) {
      const e = map.get(t.symbol) || { symbol: t.symbol, n: 0, wins: 0, pnl: 0 };
      e.n++;
      if (t.pnl > 0) e.wins++;
      e.pnl += t.pnl;
      map.set(t.symbol, e);
    }
    return [...map.values()].sort((a, b) => b.pnl - a.pnl);
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

  /**
   * Size for one new position. `openCount` caps how many coins can be held at
   * once — without it the first signal of the session would swallow all the
   * cash and the other nine coins would never get a turn.
   */
  orderNotional(cash, openCount = 0) {
    if (this.halted) return 0;
    if (openCount >= this.cfg.maxConcurrentPositions) return 0;
    const notional = cash * this.cfg.orderSizePct;
    return notional < this.cfg.minNotional ? 0 : notional;
  }

  updateEquity(equity) {
    if (this.startingEquity <= 0) return;
    const dd = (this.startingEquity - equity) / this.startingEquity;
    if (dd >= this.cfg.dailyLossLimitPct) this.halted = true;
  }
}
