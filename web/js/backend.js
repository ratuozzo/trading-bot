// Backend mode.
//
// When the dashboard is served BY the bot's backend, it stops doing its own
// trading and simply views what the server is doing. The engine then lives in
// the server process: closing this page, locking the phone, or losing signal
// does not stop it.
//
// Detection is by origin, not configuration: if /healthz answers on the same
// host that served this page, we're in backend mode. A page served from
// GitHub Pages has no backend and falls back to trading in the browser.
//
// Same-origin matters for more than tidiness — an HTTPS page cannot open a
// ws:// socket or fetch http:// (mixed content), so a Pages-hosted dashboard
// physically cannot drive a bare-IP backend.

const TOKEN_KEY = 'tradingbot.token';

export function savedToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

export function saveToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode */ }
}

/** Is a backend serving this page? Resolves to false on a static host. */
export async function detectBackend() {
  try {
    const res = await fetch('healthz', { cache: 'no-store' });
    if (!res.ok) return false;
    const body = await res.json();
    return body && body.ok === true;
  } catch {
    return false;   // static hosting, or the server is down
  }
}

export class BackendClient {
  constructor({ onState, onStatus } = {}) {
    this.onState = onState || (() => {});
    this.onStatus = onStatus || (() => {});
    this.token = savedToken();
    this.ws = null;
    this.stopped = false;
    this.backoff = 1000;
    this.lastState = null;
  }

  _auth(url) {
    // Browsers can't set headers on a WebSocket handshake, so the token goes
    // in the query string for the stream and in a header for plain fetches.
    if (!this.token) return url;
    return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(this.token);
  }

  headers() {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  async command(path, body) {
    const res = await fetch('api/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.headers() },
      body: JSON.stringify(body || {}),
    });
    if (res.status === 401) {
      this.onStatus('unauthorised', 'token rejected');
      return { error: 'unauthorised' };
    }
    return res.json();
  }

  start() { return this.command('start'); }
  stop() { return this.command('stop'); }
  reset() { return this.command('reset'); }
  setConfig(patch) { return this.command('config', patch); }

  connect() {
    this.stopped = false;
    this._open();
  }

  disconnect() {
    this.stopped = true;
    if (this.ws) { try { this.ws.close(); } catch { /* closing */ } }
    this.ws = null;
  }

  _open() {
    if (this.stopped) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = this._auth(`${proto}//${location.host}${location.pathname.replace(/[^/]*$/, '')}api/stream`);
    this.onStatus('connecting');

    let ws;
    try { ws = new WebSocket(url); }
    catch { this._retry(); return; }
    this.ws = ws;

    ws.onopen = () => { this.backoff = 1000; this.onStatus('live'); };
    ws.onmessage = (ev) => {
      let state;
      try { state = JSON.parse(ev.data); } catch { return; }
      this.lastState = state;
      this.onState(state);
    };
    ws.onclose = (ev) => {
      if (this.stopped) return;
      // 1008/1011 or an immediate close usually means the token was refused.
      this.onStatus(
        'reconnecting',
        ev.code === 1008 ? 'unauthorised — check the token' : '',
      );
      this._retry();
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  _retry() {
    if (this.stopped) return;
    setTimeout(() => this._open(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 15000);
  }
}
