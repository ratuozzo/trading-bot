// Exchange WebSocket adapters.
//
// One connection per venue carries every symbol you're watching, so tracking
// ten coins costs one socket rather than ten.
//
// Every adapter normalises messages into the same tick shape:
//   { feedId, symbol, price, qty, exchangeTs, localTs }
// where `symbol` is the base asset ("BTC"), not the venue's own spelling.
//
// `localTs` is stamped with performance.timeOrigin + performance.now() the
// instant the message arrives. That matters: comparing feeds by *their own*
// timestamps would measure clock skew between exchanges, not real latency.

const now = () => performance.timeOrigin + performance.now();

// Each venue names the same market differently.
export const SYMBOL_MAP = {
  'mexc-futures': (b) => `${b.toUpperCase()}_USDT`,
  bybit: (b) => `${b.toUpperCase()}USDT`,
  okx: (b) => `${b.toUpperCase()}-USDT-SWAP`,
  hyperliquid: (b) => b.toUpperCase(),
  'binance-futures': (b) => `${b.toLowerCase()}usdt`,
  'binance-spot': (b) => `${b.toLowerCase()}usdt`,
};

// `spot: true` venues cannot be shorted without a margin account.
// `restricted` flags venues that geo-block some jurisdictions — they will
// simply fail to connect from those places, which is worth saying out loud
// rather than leaving a blank row.
export const FEEDS = {
  'mexc-futures': { label: 'MEXC Perp', venue: 'MEXC USDT futures', spot: false },
  bybit: { label: 'Bybit Perp', venue: 'Bybit linear perps', spot: false },
  okx: { label: 'OKX Swap', venue: 'OKX perpetual swap', spot: false },
  hyperliquid: { label: 'Hyperliquid', venue: 'Hyperliquid perps', spot: false },
  'binance-futures': {
    label: 'Binance Perp', venue: 'Binance USD-M futures', spot: false,
    restricted: 'Binance geo-blocks several regions (incl. much of the EU)',
  },
  'binance-spot': {
    label: 'Binance Spot', venue: 'Binance spot', spot: true,
    restricted: 'Binance geo-blocks several regions (incl. much of the EU)',
  },
};

/** A single reconnecting exchange connection covering many symbols. */
export class Feed {
  constructor(id, bases, { onTick, onStatus } = {}) {
    this.id = id;
    this.bases = [...bases];
    this.onTick = onTick || (() => {});
    this.onStatus = onStatus || (() => {});

    // venue symbol -> base asset, for routing messages back
    this.symbolToBase = new Map();
    for (const b of this.bases) {
      this.symbolToBase.set(SYMBOL_MAP[id](b), b);
    }

    this.ws = null;
    this.stopped = false;
    this.backoff = 1000;
    this._keepalive = null;
    this._sawMessage = false;
    this._openedAt = 0;
  }

  start() { this.stopped = false; this._connect(); }

  stop() {
    this.stopped = true;
    clearInterval(this._keepalive);
    if (this.ws) { try { this.ws.close(); } catch { /* already closing */ } }
    this.ws = null;
    this.onStatus('idle');
  }

  _status(state, detail) { this.onStatus(state, detail); }

  _connect() {
    if (this.stopped) return;
    this._status('connecting');
    this._sawMessage = false;

    let ws;
    try {
      ws = new WebSocket(this._url());
    } catch (err) {
      this._status('error', `could not open socket: ${err.message}`);
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.backoff = 1000;
      this._openedAt = now();
      this._status('live');
      for (const frame of this._subscribeFrames()) ws.send(JSON.stringify(frame));
      this._startKeepalive(ws);

      // Connected but silent usually means the subscription was rejected —
      // say so instead of showing an empty row forever.
      setTimeout(() => {
        if (!this.stopped && !this._sawMessage && this.ws === ws) {
          this._status('error', 'connected but no data — symbol may not exist here');
        }
      }, 12000);
    };

    ws.onmessage = (ev) => {
      const localTs = now();

      // Some venues can push compressed/binary frames. We only speak JSON, so
      // surface that clearly rather than dropping every message in silence.
      if (typeof ev.data !== 'string') {
        this._status('error', 'binary/compressed frames not supported');
        return;
      }
      if (ev.data === 'pong') return;

      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }

      const err = this._errorOf(msg);
      if (err) { this._status('error', err); return; }

      for (const tick of this._parse(msg)) {
        this._sawMessage = true;
        this._status('live');
        this.onTick({ ...tick, feedId: this.id, localTs });
      }
    };

    ws.onerror = () => { /* onclose always follows; handle it there */ };

    ws.onclose = (ev) => {
      clearInterval(this._keepalive);
      if (this.stopped) return;
      // A close within a second of opening, with no data, is the signature of
      // a geo-block or a rejected handshake.
      const quick = now() - this._openedAt < 1500 && !this._sawMessage;
      const hint = FEEDS[this.id]?.restricted;
      this._status(
        'reconnecting',
        quick && hint ? `blocked? ${hint}` : `closed (${ev.code || 'no code'})`,
      );
      this._scheduleReconnect();
    };
  }

  _startKeepalive(ws) {
    clearInterval(this._keepalive);
    const ping = this._pingFrame();
    if (!ping) return;
    this._keepalive = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(typeof ping === 'string' ? ping : JSON.stringify(ping));
      }
    }, 15000);
  }

  _scheduleReconnect() {
    if (this.stopped) return;
    setTimeout(() => this._connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 15000);
  }

  _url() {
    switch (this.id) {
      case 'mexc-futures': return 'wss://contract.mexc.com/edge';
      case 'bybit': return 'wss://stream.bybit.com/v5/public/linear';
      case 'okx': return 'wss://ws.okx.com:8443/ws/v5/public';
      case 'hyperliquid': return 'wss://api.hyperliquid.xyz/ws';
      case 'binance-futures':
      case 'binance-spot': {
        // Binance encodes subscriptions in the URL via a combined stream.
        const host = this.id === 'binance-futures'
          ? 'wss://fstream.binance.com' : 'wss://stream.binance.com:9443';
        const streams = [...this.symbolToBase.keys()]
          .map((s) => `${s}@${this.id === 'binance-futures' ? 'aggTrade' : 'trade'}`)
          .join('/');
        return `${host}/stream?streams=${streams}`;
      }
      default: throw new Error(`unknown feed ${this.id}`);
    }
  }

  _pingFrame() {
    // MEXC closes idle contract sockets after 60s; OKX after 30s.
    if (this.id === 'mexc-futures') return { method: 'ping' };
    if (this.id === 'okx') return 'ping';
    return null; // others rely on protocol-level pings
  }

  _subscribeFrames() {
    const syms = [...this.symbolToBase.keys()];
    switch (this.id) {
      case 'mexc-futures':
        // One sub.deal per contract, all on the same connection.
        return syms.map((s) => ({ method: 'sub.deal', param: { symbol: s } }));
      case 'bybit':
        return [{ op: 'subscribe', args: syms.map((s) => `publicTrade.${s}`) }];
      case 'okx':
        return [{
          op: 'subscribe',
          args: syms.map((s) => ({ channel: 'trades', instId: s })),
        }];
      case 'hyperliquid':
        return syms.map((s) => ({
          method: 'subscribe', subscription: { type: 'trades', coin: s },
        }));
      default:
        return []; // Binance subscribes via the URL
    }
  }

  /** Venue-specific error envelopes, so a rejection isn't silent. */
  _errorOf(msg) {
    if (this.id === 'mexc-futures' && msg.channel === 'rs.error') {
      return `MEXC: ${msg.data || 'subscription rejected'}`;
    }
    if (this.id === 'bybit' && msg.success === false) {
      return `Bybit: ${msg.ret_msg || 'subscription rejected'}`;
    }
    if (this.id === 'okx' && msg.event === 'error') {
      return `OKX: ${msg.msg || 'subscription rejected'}`;
    }
    if (this.id.startsWith('binance') && msg.error) {
      return `Binance: ${msg.error.msg || 'error'}`;
    }
    return null;
  }

  /** Always returns an array — some venues batch several trades per message. */
  _parse(msg) {
    try {
      switch (this.id) {
        case 'mexc-futures': {
          if (msg.channel !== 'push.deal' || !msg.data) return [];
          const base = this.symbolToBase.get(msg.symbol);
          if (!base) return [];
          const d = msg.data;
          return [{
            symbol: base,
            price: parseFloat(d.p),
            qty: parseFloat(d.v || 0),
            exchangeTs: Number(d.t || msg.ts || 0),
          }];
        }
        case 'bybit': {
          if (!String(msg.topic || '').startsWith('publicTrade')) return [];
          if (!Array.isArray(msg.data)) return [];
          return msg.data.map((d) => {
            const base = this.symbolToBase.get(d.s);
            if (!base) return null;
            return {
              symbol: base,
              price: parseFloat(d.p),
              qty: parseFloat(d.v || 0),
              exchangeTs: Number(d.T || msg.ts || 0),
            };
          }).filter(Boolean);
        }
        case 'okx': {
          if (msg?.arg?.channel !== 'trades' || !Array.isArray(msg.data)) return [];
          return msg.data.map((d) => {
            const base = this.symbolToBase.get(d.instId);
            if (!base) return null;
            return {
              symbol: base,
              price: parseFloat(d.px),
              qty: parseFloat(d.sz || 0),
              exchangeTs: Number(d.ts || 0),
            };
          }).filter(Boolean);
        }
        case 'hyperliquid': {
          if (msg.channel !== 'trades' || !Array.isArray(msg.data)) return [];
          return msg.data.map((d) => {
            const base = this.symbolToBase.get(d.coin);
            if (!base) return null;
            return {
              symbol: base,
              price: parseFloat(d.px),
              qty: parseFloat(d.sz || 0),
              exchangeTs: Number(d.time || 0),
            };
          }).filter(Boolean);
        }
        case 'binance-futures':
        case 'binance-spot': {
          // Combined-stream envelope: { stream, data }
          const d = msg.data || msg;
          if (!d.p || !d.s) return [];
          const base = this.symbolToBase.get(String(d.s).toLowerCase());
          if (!base) return [];
          return [{
            symbol: base,
            price: parseFloat(d.p),
            qty: parseFloat(d.q || 0),
            exchangeTs: Number(d.T ?? d.E ?? 0),
          }];
        }
        default:
          return [];
      }
    } catch {
      return [];
    }
  }
}

/**
 * Measures which venue reports a market move first, using local arrival times
 * only. See the README for why matching on absolute price does not work
 * across venues. Scoped to a single symbol.
 */
const MOVE_THRESHOLD = 0.0002; // 2 bp move counts as an event
const MOVE_WINDOW = 1000;      // ...measured over 1s
const GROUP_WINDOW = 2000;     // events within 2s are the same market move

export class FeedRace {
  constructor(symbol) {
    this.symbol = symbol;
    this.stats = new Map();
    this._events = [];
  }

  ensure(feedId) {
    if (!this.stats.has(feedId)) {
      this.stats.set(feedId, {
        samples: [], msgs: 0, recent: [], lastTs: 0, status: 'idle', detail: '',
        price: null, history: [], eventDir: 0, prevRet: 0, prevTs: 0,
      });
    }
    return this.stats.get(feedId);
  }

  setStatus(feedId, status, detail = '') {
    const s = this.ensure(feedId);
    s.status = status;
    s.detail = detail;
  }

  record(tick) {
    if (tick.symbol !== this.symbol) return;
    const s = this.ensure(tick.feedId);
    s.msgs++;
    s.lastTs = tick.localTs;
    s.price = tick.price;

    s.recent.push(tick.localTs);
    const cut = tick.localTs - 1000;
    while (s.recent.length && s.recent[0] < cut) s.recent.shift();

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
      if (Math.abs(ret) < MOVE_THRESHOLD * 0.5) s.eventDir = 0;
      return;
    }
    if (s.eventDir === dir) return;
    s.eventDir = dir;

    // Interpolate the crossing: without it the error is a whole tick interval,
    // the same magnitude as the latencies being measured.
    const target = dir * MOVE_THRESHOLD;
    let crossTs = tick.localTs;
    if ((ret - prevRet) !== 0 &&
        Math.sign(prevRet - target) !== Math.sign(ret - target)) {
      const frac = (target - prevRet) / (ret - prevRet);
      if (frac >= 0 && frac <= 1) crossTs = prevTs + frac * (tick.localTs - prevTs);
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
      this._events.push({ dir, ts, seen: new Set([feedId]) });
      if (this._events.length > 200) this._events.shift();
      s.samples.push(0);
      if (s.samples.length > 200) s.samples.shift();
      return;
    }
    if (ev.seen.has(feedId)) return;
    ev.seen.add(feedId);
    s.samples.push(ts - ev.ts);
    if (s.samples.length > 200) s.samples.shift();
  }

  summary() {
    const out = [];
    for (const [feedId, s] of this.stats) {
      const sorted = [...s.samples].sort((a, b) => a - b);
      const median = sorted.length
        ? Math.round(sorted[Math.floor(sorted.length / 2)]) : null;
      const cut = now() - 1000;
      while (s.recent.length && s.recent[0] < cut) s.recent.shift();
      out.push({
        feedId, status: s.status, detail: s.detail, msgs: s.msgs,
        rate: s.recent.length, price: s.price, lagMs: median,
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
