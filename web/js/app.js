// Wires feeds → strategy → risk → broker → portfolio → UI.
// Everything runs in the browser: your phone connects straight to the
// exchanges, so there's no server hop adding latency.

import { DEFAULTS, loadSettings, saveSettings } from './config.js';
import { FEEDS, Feed, FeedRace } from './feeds.js';
import { MomentumScalper, SIGNAL } from './strategy.js';
import { PaperBroker, Portfolio, RiskManager } from './broker.js';

const $ = (id) => document.getElementById(id);
const nowMs = () => performance.timeOrigin + performance.now();

let settings = loadSettings();
let feeds = [];
let race = new FeedRace();
let strategy, broker, portfolio, risk;
let running = false;
let lastPrice = null;
let history = [];          // [{ts, price}] for the sparkline
let msgTimestamps = [];    // for msg/s on the primary feed

// ---------------------------------------------------------------- session

function newSession() {
  broker = new PaperBroker(settings.broker);
  portfolio = new Portfolio(settings.broker.startingCash);
  risk = new RiskManager(settings.risk, settings.broker.startingCash);
  strategy = new MomentumScalper(settings.strategy);
  lastPrice = null;
  history = [];
  msgTimestamps = [];
  $('haltNotice').classList.add('hidden');
  renderTrades();
  renderStats();
  renderPosition();
}

function start() {
  stopFeeds();
  race = new FeedRace();

  // Always include the trading feed in the race set.
  const ids = new Set([settings.primaryFeed, ...settings.racing]);
  feeds = [...ids].map((id) => {
    const feed = new Feed(id, settings.base, {
      onTick: handleTick,
      onStatus: (s) => { race.setStatus(id, s); renderRace(); },
    });
    race.ensure(id);
    feed.start();
    return feed;
  });

  running = true;
  $('runBtn').textContent = 'Stop';
  $('runBtn').dataset.running = 'true';
  renderRace();
}

function stop() {
  stopFeeds();
  running = false;
  $('runBtn').textContent = 'Start';
  $('runBtn').dataset.running = 'false';
  $('masterDot').dataset.state = 'idle';
}

function stopFeeds() {
  feeds.forEach((f) => f.stop());
  feeds = [];
}

// ---------------------------------------------------------------- ticks

function handleTick(tick) {
  race.record(tick);

  // Only the chosen feed drives trading decisions.
  if (tick.feedId !== settings.primaryFeed) return;

  const t = { price: tick.price, ts: tick.localTs };
  lastPrice = t.price;

  msgTimestamps.push(t.ts);

  history.push(t);
  const hCut = t.ts - 60000;
  while (history.length > 1 && history[0].ts < hCut) history.shift();

  const signal = strategy.onTick(t, {
    inPosition: broker.inPosition,
    entryPrice: broker.avgEntry,
  });

  if (signal.type === SIGNAL.ENTER_LONG && !broker.inPosition) {
    const notional = risk.orderNotional(broker.cash);
    if (notional > 0) {
      const fill = broker.buy(notional, t.price, t.ts);
      if (fill) {
        portfolio.recordFill(fill);
        strategy.noteEntry(t.ts);
        renderPosition();
      }
    }
  } else if (signal.type === SIGNAL.EXIT_LONG && broker.inPosition) {
    const fill = broker.sell(broker.qty, t.price, t.ts);
    if (fill) {
      const trade = portfolio.recordFill(fill, signal.reason);
      strategy.noteExit(t.ts);
      risk.updateEquity(broker.equity(t.price));
      if (risk.halted) $('haltNotice').classList.remove('hidden');
      renderTrades();
      renderStats();
      renderPosition();
    }
  }
}

// ---------------------------------------------------------------- render

let lastRendered = null;

let flashTimer = null;

function renderPrice() {
  if (lastPrice === null) return;
  const el = $('price');
  el.textContent = fmtPrice(lastPrice);

  // Flash on change, then settle back to neutral. A persistent colour based on
  // the last tick alone contradicts the trend readouts next to it.
  if (lastRendered !== null && lastPrice !== lastRendered) {
    const up = lastPrice > lastRendered;
    el.classList.toggle('up', up);
    el.classList.toggle('down', !up);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => el.classList.remove('up', 'down'), 220);
  }
  lastRendered = lastPrice;

  // Trim against the clock, not the last tick, so a stalled feed decays to 0.
  const cutNow = nowMs() - 1000;
  while (msgTimestamps.length && msgTimestamps[0] < cutNow) msgTimestamps.shift();

  const ret = strategy
    ? strategy._returnOver(settings.strategy.lookbackSeconds, nowMs(), lastPrice)
    : 0;
  const pct = (ret * 100).toFixed(3);
  $('impulse').textContent =
    `${ret >= 0 ? '+' : ''}${pct}% / ${settings.strategy.lookbackSeconds}s`;
  $('impulse').style.color =
    ret >= settings.strategy.entryThreshold ? 'var(--up)'
      : ret <= -settings.strategy.entryThreshold ? 'var(--down)' : '';

  $('rate').textContent = `${msgTimestamps.length} msg/s`;
}

function renderPosition() {
  const state = $('posState');
  const detail = $('posDetail');
  if (broker?.inPosition) {
    state.textContent = 'LONG';
    state.classList.add('long');
    const u = lastPrice ? broker.unrealized(lastPrice) : 0;
    detail.textContent =
      `${broker.qty.toFixed(6)} @ ${fmtPrice(broker.avgEntry)}  ${fmtSigned(u)}`;
  } else {
    state.textContent = 'flat';
    state.classList.remove('long');
    detail.textContent = risk?.halted ? 'halted' : 'waiting for impulse';
  }

  if (broker && lastPrice) {
    const eq = broker.equity(lastPrice);
    $('equity').textContent = fmtMoney(eq);
    const diff = eq - broker.startingCash;
    const pctChange = (diff / broker.startingCash) * 100;
    const pnlEl = $('pnl');
    pnlEl.textContent = `${fmtSigned(diff)} (${pctChange >= 0 ? '+' : ''}${pctChange.toFixed(3)}%)`;
    pnlEl.style.color = diff > 0 ? 'var(--up)' : diff < 0 ? 'var(--down)' : '';
  }
}

function renderStats() {
  if (!portfolio) return;
  $('nTrades').textContent = portfolio.trades.length;
  $('winRate').textContent = portfolio.trades.length
    ? `${(portfolio.winRate * 100).toFixed(0)}%`
    : '—';
  const r = portfolio.realizedPnl;
  const el = $('realized');
  el.textContent = portfolio.trades.length ? fmtSigned(r) : '—';
  el.style.color = r > 0 ? 'var(--up)' : r < 0 ? 'var(--down)' : '';
}

function renderTrades() {
  const list = $('tradeList');
  if (!portfolio?.trades.length) {
    list.innerHTML = '<li class="empty">No trades yet.</li>';
    return;
  }
  list.innerHTML = portfolio.trades
    .slice(-25)
    .reverse()
    .map((t) => {
      const pct = ((t.exitPrice - t.entryPrice) / t.entryPrice) * 100;
      const held = ((t.exitTime - t.entryTime) / 1000).toFixed(1);
      return `<li>
        <div>
          <div class="mono">${fmtPrice(t.entryPrice)} → ${fmtPrice(t.exitPrice)}</div>
          <div class="trade-meta">${escapeHtml(t.reason)} · held ${held}s</div>
        </div>
        <div class="tpnl ${t.pnl >= 0 ? 'pos' : 'neg'}">
          ${fmtSigned(t.pnl)}<div class="trade-meta">${pct >= 0 ? '+' : ''}${pct.toFixed(3)}%</div>
        </div>
      </li>`;
    })
    .join('');
}

function renderRace() {
  const rows = race.summary();
  // Rank by measured lag; feeds with no samples sort last.
  const withLag = rows.filter((r) => r.lagMs !== null);
  const best = withLag.length ? Math.min(...withLag.map((r) => r.lagMs)) : null;

  rows.sort((a, b) => {
    if (a.lagMs === null && b.lagMs === null) return 0;
    if (a.lagMs === null) return 1;
    if (b.lagMs === null) return -1;
    return a.lagMs - b.lagMs;
  });

  $('raceTable').querySelector('tbody').innerHTML = rows
    .map((r) => {
      const meta = FEEDS[r.feedId] || { label: r.feedId };
      const isBest = best !== null && r.lagMs === best;
      const dead = r.status !== 'live';
      const lag = r.lagMs === null
        ? '—'
        : isBest ? '0ms' : `+${Math.round(r.lagMs - best)}ms`;
      const rate = r.rate ?? 0;
      return `<tr class="${isBest && !dead ? 'fastest' : ''} ${dead ? 'dead' : ''}">
        <td><div class="venue">
          <span class="dot" data-state="${r.status}"></span>
          ${escapeHtml(meta.label)}${r.feedId === settings.primaryFeed ? ' ★' : ''}
        </div></td>
        <td class="r">${rate}</td>
        <td class="r">${lag}</td>
        <td class="r">${r.price ? fmtPrice(r.price) : '—'}</td>
      </tr>`;
    })
    .join('');

  const anyLive = rows.some((r) => r.status === 'live');
  $('masterDot').dataset.state = !running ? 'idle' : anyLive ? 'live' : 'connecting';
  $('feedLabel').textContent = FEEDS[settings.primaryFeed]?.label ?? '—';
}

function renderSpark() {
  const canvas = $('spark');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = 48;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (history.length < 2) return;

  const prices = history.map((p) => p.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;
  const t0 = history[0].ts;
  const tSpan = history[history.length - 1].ts - t0 || 1;

  ctx.beginPath();
  history.forEach((p, i) => {
    const x = ((p.ts - t0) / tSpan) * w;
    const y = h - ((p.price - min) / span) * (h - 6) - 3;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  const rising = prices[prices.length - 1] >= prices[0];
  ctx.strokeStyle = rising ? '#2fd47b' : '#ff5c6c';
  ctx.lineWidth = 1.5;
  ctx.lineJoin = 'round';
  ctx.stroke();
}

// ---------------------------------------------------------------- format

function fmtPrice(p) {
  if (p == null) return '—';
  const d = p >= 1000 ? 2 : p >= 1 ? 4 : 6;
  return p.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtMoney(v) {
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtSigned(v) {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------- settings UI

function buildSettingsUI() {
  const feedSel = $('setFeed');
  feedSel.innerHTML = Object.entries(FEEDS)
    .map(([id, m]) => `<option value="${id}">${m.label}</option>`)
    .join('');

  $('raceChecks').innerHTML = Object.entries(FEEDS)
    .map(([id, m]) => `<label><input type="checkbox" value="${id}" /> ${m.label}</label>`)
    .join('');

  syncSettingsUI();

  $('applyBtn').addEventListener('click', () => {
    readSettingsUI();
    saveSettings(settings);
    newSession();
    if (running) start();
    document.querySelector('.settings').open = false;
  });

  $('resetBtn').addEventListener('click', () => {
    newSession();
    race.reset();
    renderRace();
  });

  $('resetRace').addEventListener('click', () => { race.reset(); renderRace(); });
}

function syncSettingsUI() {
  $('setBase').value = settings.base;
  $('setFeed').value = settings.primaryFeed;
  $('setEntry').value = (settings.strategy.entryThreshold * 100).toFixed(3);
  $('setTp').value = (settings.strategy.takeProfit * 100).toFixed(3);
  $('setSl').value = (settings.strategy.stopLoss * 100).toFixed(3);
  $('setLookback').value = settings.strategy.lookbackSeconds;
  $('setFee').value = settings.broker.feeBps;
  $('setCash').value = settings.broker.startingCash;
  document.querySelectorAll('#raceChecks input').forEach((cb) => {
    cb.checked = settings.racing.includes(cb.value);
  });
  $('symbolLabel').textContent = settings.base;
}

function readSettingsUI() {
  const num = (id, fallback) => {
    const v = parseFloat($(id).value);
    return Number.isFinite(v) ? v : fallback;
  };
  settings.base = $('setBase').value;
  settings.primaryFeed = $('setFeed').value;
  settings.racing = [...document.querySelectorAll('#raceChecks input')]
    .filter((cb) => cb.checked)
    .map((cb) => cb.value);
  settings.strategy.entryThreshold = num('setEntry', 0.15) / 100;
  settings.strategy.takeProfit = num('setTp', 0.2) / 100;
  settings.strategy.stopLoss = num('setSl', 0.15) / 100;
  settings.strategy.lookbackSeconds = num('setLookback', 5);
  settings.broker.feeBps = num('setFee', DEFAULTS.broker.feeBps);
  settings.broker.startingCash = num('setCash', DEFAULTS.broker.startingCash);
  $('symbolLabel').textContent = settings.base;
}

// ---------------------------------------------------------------- boot

$('runBtn').addEventListener('click', () => (running ? stop() : start()));

// Mobile browsers suspend sockets in background tabs; reconnect on return.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && running) {
    const stale = feeds.some((f) => !f.ws || f.ws.readyState !== WebSocket.OPEN);
    if (stale) start();
  }
});

buildSettingsUI();
newSession();
renderRace();

// One render loop keeps the UI smooth no matter how fast ticks arrive —
// rendering per-tick would melt the phone at 100+ msg/s.
setInterval(() => {
  renderPrice();
  renderPosition();
  renderRace();
}, 250);
setInterval(renderSpark, 500);
