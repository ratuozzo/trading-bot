// Exchange WebSocket adapters.
//
// Every adapter normalises messages into the same tick shape:
//   { price, bid, ask, qty, exchangeTs, localTs }
//
// `localTs` is stamped with performance.timeOrigin + performance.now() the
// instant the message arrives. That matters: comparing feeds by *their own*
// timestamps would measure clock skew between exchanges, not real latency.
// Comparing local arrival times uses one clock for all feeds, so the race
// results are meaningful.

const now = () => performance.timeOrigin + performance.now();

// Each venue names the same market differently.
export const SYMBOL_MAP = {
  'binance-futures': (b) => `${b.toLowerCase()}usdt`,
  'binance-spot': (b) => `${b.toLowerCase()}usdt`,
  bybit: (b) => `${b.toUpperCase()}USDT`,
  okx: (b) => `${b.toUpperCase()}-USDT-SWAP`,
  hyperliquid: (b) => b.toUpperCase(),
  coinbase: (b) => `${b.toUpperCase()}-USD`,
};

// `spot: true` venues cannot be shorted without a margin account — the bot
// would have nothing to sell. Perps can be shorted directly, and are cheaper.
export const FEEDS = {
  'binance-futures': { label: 'Binance Perp', venue: 'Binance USD-M futures', spot: false },
  'binance-spot': { label: 'Binance Spot', venue: 'Binance spot', spot: true },
  bybit: { label: 'Bybit Perp', venue: 'Bybit linear perps', spot: false },
  okx: { label: 'OKX Swap', venue: 'OKX perpetual swap', spot: false },
  hyperliquid: { label: 'Hyperliquid', venue: 'Hyperliquid perps', spot: false },
  coinbase: { label: 'Coinbase', venue: 'Coinbase spot', spot: true },
};

/**
 * A single reconnecting exchange connection.
 * Emits normalised ticks via the `onTick` callback.
 */
export class Feed {
  constructor(id, base, { onTick, onStatus } = {}) {
    this.id = id;
    this.base = base;
    this.symbol = SYMBOL_MAP[id](base);
    this.onTick = onTick || (() => {});
    this.onStatus = onStatus || (() => {});
    this.ws = null;
    this.stopped = false;
    this.backoff = 1000;
    this.msgCount = 0;
    this.lastPrice = null;
    this._bid = null;
    this._ask = null;
    this._keepalive = null;
  }

  start() {
    this.stopped = false;
    this._connect();
  }

  stop() {
    this.stopped = true;
    clearInterval(this._keepalive);
    if (this.ws) {
      try { this.ws.close(); } catch { /* already closing */ }
    }
    this.ws = null;
    this.onStatus('idle');
  }

  _connect() {
    if (this.stopped) return;
    this.onStatus('connecting');
    let ws;
    try {
      ws = new WebSocket(this._url());
    } catch (err) {
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.backoff = 1000;
      this.onStatus('live');
      for (const frame of this._subscribeFrames()) {
        ws.send(JSON.stringify(frame));
      }
      if (this.id === 'okx') {
        // OKX drops idle connections after 30s.
        clearInterval(this._keepalive);
        this._keepalive = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 20000);
      }
    };

    ws.onmessage = (ev) => {
      const localTs = now();
      if (ev.data === 'pong') return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      const tick = this._parse(msg);
      if (!tick) return;
      this.msgCount++;
      this.lastPrice = tick.price;
      this.onTick({ ...tick, feedId: this.id, localTs });
    };

    ws.onerror = () => { /* onclose always follows; handle it there */ };

    ws.onclose = () => {
      clearInterval(this._keepalive);
      if (this.stopped) return;
      this.onStatus('reconnecting');
      this._scheduleReconnect();
    };
  }

  _scheduleReconnect() {
    if (this.stopped) return;
    setTimeout(() => this._connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 15000);
  }

  _url() {
    switch (this.id) {
      case 'binance-futures':
        return `wss://fstream.binance.com/ws/${this.symbol}@aggTrade`;
      case 'binance-spot':
        return `wss://stream.binance.com:9443/ws/${this.symbol}@trade`;
      case 'bybit':
        return 'wss://stream.bybit.com/v5/public/linear';
      case 'okx':
        return 'wss://ws.okx.com:8443/ws/v5/public';
      case 'hyperliquid':
        return 'wss://api.hyperliquid.xyz/ws';
      case 'coinbase':
        return 'wss://ws-feed.exchange.coinbase.com';
      default:
        throw new Error(`unknown feed ${this.id}`);
    }
  }

  _subscribeFrames() {
    switch (this.id) {
      case 'bybit':
        return [{ op: 'subscribe', args: [`publicTrade.${this.symbol}`] }];
      case 'okx':
        return [{ op: 'subscribe', args: [{ channel: 'trades', instId: this.symbol }] }];
      case 'hyperliquid':
        return [{ method: 'subscribe', subscription: { type: 'trades', coin: this.symbol } }];
      case 'coinbase':
        return [{ type: 'subscribe', product_ids: [this.symbol], channels: ['matches'] }];
      default:
        return []; // Binance encodes the subscription in the URL
    }
  }

  _parse(msg) {
    try {
      switch (this.id) {
        case 'binance-futures':
        case 'binance-spot': {
          if (!msg.p) return null;
          return {
            price: parseFloat(msg.p),
            qty: parseFloat(msg.q || 0),
            exchangeTs: Number(msg.T ?? msg.E ?? 0),
          };
        }
        case 'bybit': {
          if (!String(msg.topic || '').startsWith('publicTrade')) return null;
          const last = Array.isArray(msg.data) ? msg.data[msg.data.length - 1] : null;
          if (!last) return null;
          return {
            price: parseFloat(last.p),
            qty: parseFloat(last.v || 0),
            exchangeTs: Number(last.T || msg.ts || 0),
          };
        }
        case 'okx': {
          if (msg?.arg?.channel !== 'trades') return null;
          const last = Array.isArray(msg.data) ? msg.data[msg.data.length - 1] : null;
          if (!last) return null;
          return {
            price: parseFloat(last.px),
            qty: parseFloat(last.sz || 0),
            exchangeTs: Number(last.ts || 0),
          };
        }
        case 'hyperliquid': {
          if (msg.channel !== 'trades') return null;
          const last = Array.isArray(msg.data) ? msg.data[msg.data.length - 1] : null;
          if (!last) return null;
          return {
            price: parseFloat(last.px),
            qty: parseFloat(last.sz || 0),
            exchangeTs: Number(last.time || 0),
          };
        }
        case 'coinbase': {
          if (msg.type !== 'match' && msg.type !== 'last_match') return null;
          return {
            price: parseFloat(msg.price),
            qty: parseFloat(msg.size || 0),
            exchangeTs: msg.time ? Date.parse(msg.time) : 0,
          };
        }
        default:
          return null;
      }
    } catch {
      return null;
    }
  }
}

/**
 * Runs several feeds at once and measures which one reports a market move
 * first, using local arrival times only.
 *
 * Matching on absolute price would not work: a perp and a spot book trade at
 * genuinely different prices (basis can be tens of dollars on BTC), so the
 * same "level" never lines up across venues. Instead each feed is judged on
 * its OWN relative move — when a feed's price shifts by `MOVE_THRESHOLD`
 * within `MOVE_WINDOW`, that's an event. Events of the same direction landing
 * close together are treated as the same market move, and the gap between the
 * first feed to flag it and each other feed is the sample.
 *
 * Both timestamps come from this device, so the numbers are free of exchange
 * clock skew and reflect genuine arrival order at your location.
 */
const MOVE_THRESHOLD = 0.0002; // 2 bp move counts as an event
const MOVE_WINDOW = 1000;      // ...measured over 1s
const GROUP_WINDOW = 2000;     // events within 2s are the same market move

export class FeedRace {
  constructor() {
    this.stats = new Map(); // feedId -> per-feed state
    this._events = [];      // recent market moves being raced
  }

  ensure(feedId) {
    if (!this.stats.has(feedId)) {
      this.stats.set(feedId, {
        samples: [], msgs: 0, recent: [], lastTs: 0, status: 'idle', price: null,
        history: [], eventDir: 0, prevRet: 0, prevTs: 0,
      });
    }
    return this.stats.get(feedId);
  }

  setStatus(feedId, status) {
    this.ensure(feedId).status = status;
  }

  record(tick) {
    const s = this.ensure(tick.feedId);
    s.msgs++;
    s.lastTs = tick.localTs;
    s.price = tick.price;

    // Rolling one-second window for the message rate.
    s.recent.push(tick.localTs);
    const cut = tick.localTs - 1000;
    while (s.recent.length && s.recent[0] < cut) s.recent.shift();

    // Keep a short price history for this feed's own move detection.
    s.history.push({ ts: tick.localTs, price: tick.price });
    const hCut = tick.localTs - MOVE_WINDOW;
    while (s.history.length > 1 && s.history[0].ts < hCut) s.history.shift();

    const ref = s.history[0].price;
    if (!ref) return;
    const ret = (tick.price - ref) / ref;
    const prevRet = s.prevRet;
    const prevTs = s.prevTs || tick.localTs;
    s.prevRet = ret;
    s.prevTs = tick.localTs;

    const dir = ret >= MOVE_THRESHOLD ? 1 : ret <= -MOVE_THRESHOLD ? -1 : 0;
    if (dir === 0) {
      // Hysteresis: only re-arm once the move has clearly decayed.
      if (Math.abs(ret) < MOVE_THRESHOLD * 0.5) s.eventDir = 0;
      return;
    }
    if (s.eventDir === dir) return; // already counted this move
    s.eventDir = dir;

    // Ticks are discrete, so the true crossing sits between this tick and the
    // last one. Interpolating recovers sub-tick timing — without it the error
    // is a whole tick interval, which is the same magnitude as the latencies
    // being measured here.
    const target = dir * MOVE_THRESHOLD;
    let crossTs = tick.localTs;
    if ((ret - prevRet) !== 0 && Math.sign(prevRet - target) !== Math.sign(ret - target)) {
      const frac = (target - prevRet) / (ret - prevRet);
      if (frac >= 0 && frac <= 1) {
        crossTs = prevTs + frac * (tick.localTs - prevTs);
      }
    }
    this._registerEvent(dir, tick.feedId, crossTs, s);
  }

  _registerEvent(dir, feedId, ts, s) {
    let ev = null;
    for (let i = this._events.length - 1; i >= 0; i--) {
      const e = this._events[i];
      if (ts - e.ts > GROUP_WINDOW) break;
      if (e.dir === dir) { ev = e; break; }
    }
    if (!ev) {
      ev = { dir, ts, seen: new Set([feedId]) };
      this._events.push(ev);
      if (this._events.length > 200) this._events.shift();
      s.samples.push(0); // this feed saw it first
      if (s.samples.length > 200) s.samples.shift();
      return;
    }
    if (ev.seen.has(feedId)) return;
    ev.seen.add(feedId);
    s.samples.push(ts - ev.ts);
    if (s.samples.length > 200) s.samples.shift();
  }

  // Median lag behind whichever feed saw the level first (ms). Lower = faster.
  summary() {
    const out = [];
    for (const [feedId, s] of this.stats) {
      const sorted = [...s.samples].sort((a, b) => a - b);
      const median = sorted.length
        ? Math.round(sorted[Math.floor(sorted.length / 2)])
        : null;
      // Drop stale entries so an idle feed reads 0/s rather than a frozen count.
      const cut = now() - 1000;
      while (s.recent.length && s.recent[0] < cut) s.recent.shift();

      out.push({
        feedId,
        status: s.status,
        msgs: s.msgs,
        rate: s.recent.length,
        price: s.price,
        lagMs: median,
        samples: sorted.length,
      });
    }
    return out;
  }

  reset() {
    for (const s of this.stats.values()) {
      s.samples = []; s.msgs = 0; s.recent = []; s.history = []; s.eventDir = 0;
    }
    this._events = [];
  }
}
