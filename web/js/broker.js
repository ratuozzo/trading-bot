// Paper broker + portfolio + risk — ports of the Python modules of the same
// name. Simulated money only; nothing here can place a real order.

export class PaperBroker {
  constructor({ startingCash, feeBps, slippageBps }) {
    this.startingCash = startingCash;
    this.cash = startingCash;
    this.feeRate = feeBps / 10000;
    this.slippageRate = slippageBps / 10000;
    this.qty = 0;
    this.avgEntry = 0;
  }

  get inPosition() { return this.qty > 0; }

  buy(quoteAmount, refPrice, ts) {
    if (quoteAmount <= 0 || refPrice <= 0) return null;
    const spend = Math.min(quoteAmount, this.cash);
    if (spend <= 0) return null;

    const price = refPrice * (1 + this.slippageRate);
    const qty = spend / (price * (1 + this.feeRate));
    const notional = qty * price;
    const fee = notional * this.feeRate;

    this.cash -= notional + fee;
    const newQty = this.qty + qty;
    this.avgEntry = newQty > 0
      ? (this.avgEntry * this.qty + price * qty) / newQty
      : 0;
    this.qty = newQty;
    return { side: 'BUY', qty, price, fee, ts };
  }

  sell(quantity, refPrice, ts) {
    if (this.qty <= 0 || refPrice <= 0) return null;
    const qty = Math.min(quantity, this.qty);
    const price = refPrice * (1 - this.slippageRate);
    const notional = qty * price;
    const fee = notional * this.feeRate;

    this.cash += notional - fee;
    this.qty -= qty;
    if (this.qty <= 1e-12) { this.qty = 0; this.avgEntry = 0; }
    return { side: 'SELL', qty, price, fee, ts };
  }

  equity(markPrice) { return this.cash + this.qty * markPrice; }
  unrealized(markPrice) {
    return this.qty > 0 ? (markPrice - this.avgEntry) * this.qty : 0;
  }
}

export class Portfolio {
  constructor(startingCash) {
    this.startingCash = startingCash;
    this.trades = [];
    this._openQty = 0;
    this._openPrice = 0;
    this._openTime = 0;
    this._openFees = 0;
  }

  recordFill(fill, reason = '') {
    if (fill.side === 'BUY') {
      const total = this._openQty + fill.qty;
      if (total > 0) {
        this._openPrice =
          (this._openPrice * this._openQty + fill.price * fill.qty) / total;
      }
      this._openQty = total;
      if (this._openTime === 0) this._openTime = fill.ts;
      this._openFees += fill.fee;
      return null;
    }

    if (this._openQty <= 0) return null;
    const qty = Math.min(fill.qty, this._openQty);
    const gross = (fill.price - this._openPrice) * qty;
    const entryFeeShare = this._openFees * (qty / this._openQty);
    const fees = entryFeeShare + fill.fee;

    const trade = {
      qty,
      entryPrice: this._openPrice,
      exitPrice: fill.price,
      entryTime: this._openTime,
      exitTime: fill.ts,
      fees,
      pnl: gross - fees,
      reason,
    };
    this.trades.push(trade);

    this._openQty -= qty;
    this._openFees -= entryFeeShare;
    if (this._openQty <= 1e-12) {
      this._openQty = 0; this._openPrice = 0; this._openTime = 0; this._openFees = 0;
    }
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
