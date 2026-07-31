// Wires feeds → strategy → risk → broker → portfolio → UI.
// Everything runs in the browser: your phone connects straight to the
// exchanges, so there's no server hop adding latency.

import { DEFAULTS, FEE_PRESETS, loadSettings, saveSettings } from './config.js';
import { FEEDS, Feed, FeedRace } from './feeds.js';
import { MomentumScalper, SIGNAL } from './strategy.js';
import { PaperBroker, Portfolio, RiskManager, LONG, SHORT } from './broker.js';
import { LeadLagAnalyzer, roundTripCostBp, breakEvenWinRate } from './leadlag.js';

const $ = (id) => document.getElementById(id);
const nowMs = () => performance.timeOrigin + performance.now();

let settings = loadSettings();
let feeds = [];
let race = new FeedRace(settings.symbols[0]);
let leadlag = new LeadLagAnalyzer();
let broker, portfolio, risk;
let running = false;

// Everything below is keyed by symbol — ten coins run ten independent
// strategies sharing one book of cash.
let strategies = new Map();   // symbol -> MomentumScalper
let signalPrice = new Map();  // symbol -> latest price on the signal venue
let execPrice = new Map();    // symbol -> latest price on the exec venue
let impulse = new Map();      // symbol -> current return over the lookback
let history = [];             // [{ts, price}] sparkline for the focused symbol
let msgTimestamps = [];
let focus = settings.symbols[0];   // which coin the big price card shows

// ---------------------------------------------------------------- session

function newSession() {
  broker = new PaperBroker(settings.broker);
  portfolio = new Portfolio(settings.broker.startingCash);
  risk = new RiskManager(settings.risk, settings.broker.startingCash);
  strategies = new Map(
    settings.symbols.map((sym) => [sym, new MomentumScalper(settings.strategy)]),
  );
  leadlag = new LeadLagAnalyzer({
    threshold: settings.leadlag.thresholdBp / 10000,
    window: settings.leadlag.windowMs,
  });
  signalPrice = new Map();
  execPrice = new Map();
  impulse = new Map();
  if (!settings.symbols.includes(focus)) focus = settings.symbols[0];
  history = [];
  msgTimestamps = [];
  $('haltNotice').classList.add('hidden');
  renderTrades();
  renderStats();
  renderPosition();
}

function start() {
  stopFeeds();
  race = new FeedRace(settings.symbols[0]);

  // Both the signal and exec venues must be connected regardless of the race set.
  const ids = new Set([settings.signalFeed, settings.execFeed, ...settings.racing]);
  feeds = [...ids].map((id) => {
    // One socket per venue carries every symbol.
    const feed = new Feed(id, settings.symbols, {
      onTick: handleTick,
      onStatus: (st, detail) => { race.setStatus(id, st, detail); renderRace(); },
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
  const sym = tick.symbol;
  race.record(tick);

  // The exec venue supplies fill prices; lead-lag is measured on the focused
  // coin only, since mixing symbols would average unrelated moves together.
  if (tick.feedId === settings.execFeed) {
    execPrice.set(sym, tick.price);
    if (sym === focus) leadlag.onExecTick(tick.price, tick.localTs);
  }

  // Everything below is driven by the signal feed only.
  if (tick.feedId !== settings.signalFeed) return;

  const strategy = strategies.get(sym);
  if (!strategy) return;

  const t = { price: tick.price, ts: tick.localTs };
  signalPrice.set(sym, t.price);

  if (sym === focus) {
    leadlag.onSignalTick(tick.price, tick.localTs);
    msgTimestamps.push(t.ts);
    history.push(t);
    const hCut = t.ts - 60000;
    while (history.length > 1 && history[0].ts < hCut) history.shift();
  }

  // Can't price a fill until the exec venue has reported this symbol.
  const fillPrice = settings.execFeed === settings.signalFeed
    ? t.price : execPrice.get(sym);
  if (fillPrice == null) return;

  const pos = broker.position(sym);
  const signal = strategy.onTick(
    { ...t, execPrice: fillPrice },
    { dir: broker.dir(sym), entryPrice: pos.avgEntry },
  );
  impulse.set(sym, strategy._returnOver(
    settings.strategy.lookbackSeconds, t.ts, t.price));

  const isEntry =
    signal.type === SIGNAL.ENTER_LONG || signal.type === SIGNAL.ENTER_SHORT;

  if (isEntry && !broker.inPosition(sym)) {
    const notional = risk.orderNotional(broker.cash, broker.openCount);
    if (notional > 0) {
      const side = signal.type === SIGNAL.ENTER_SHORT ? SHORT : LONG;
      const fill = broker.open(sym, side, notional, fillPrice, t.ts);
      if (fill) {
        portfolio.recordFill(fill);
        strategy.noteEntry(t.ts);
        renderPosition();
      }
    }
  } else if (signal.type === SIGNAL.EXIT && broker.inPosition(sym)) {
    const fill = broker.close(sym, fillPrice, t.ts);
    if (fill) {
      portfolio.recordFill(fill, signal.reason);
      strategy.noteExit(t.ts);
      risk.updateEquity(broker.equity(marks()));
      if (risk.halted) $('haltNotice').classList.remove('hidden');
      renderTrades();
      renderStats();
      renderPosition();
    }
  }
}

/** Latest mark price per symbol, preferring the venue we trade on. */
function marks() {
  const out = {};
  for (const sym of settings.symbols) {
    const p = execPrice.get(sym) ?? signalPrice.get(sym);
    if (p != null) out[sym] = p;
  }
  return out;
}

// ---------------------------------------------------------------- render

let lastRendered = null;

let flashTimer = null;

function renderPrice() {
  const lastPrice = signalPrice.get(focus);
  if (lastPrice == null) return;
  $('symbolLabel').textContent = focus;
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

  const ret = impulse.get(focus) ?? 0;
  const pct = (ret * 100).toFixed(3);
  $('impulse').textContent =
    `${ret >= 0 ? '+' : ''}${pct}% / ${settings.strategy.lookbackSeconds}s`;
  $('impulse').style.color =
    ret >= settings.strategy.entryThreshold ? 'var(--up)'
      : ret <= -settings.strategy.entryThreshold ? 'var(--down)' : '';

  $('rate').textContent = `${msgTimestamps.length} msg/s`;
}

function renderPosition() {
  if (!broker) return;
  const state = $('posState');
  const detail = $('posDetail');
  const m = marks();
  const open = broker.openSymbols;

  if (open.length) {
    const parts = open.map((sym) => {
      const pos = broker.position(sym);
      const u = m[sym] ? broker.unrealized(sym, m[sym]) : 0;
      return `${sym} ${pos.side === SHORT ? 'S' : 'L'} ${fmtSigned(u)}`;
    });
    state.textContent = `${open.length} open`;
    state.classList.toggle('long', open.length > 0);
    state.classList.remove('short');
    detail.textContent = parts.join(' · ');
  } else {
    state.textContent = 'flat';
    state.classList.remove('long', 'short');
    detail.textContent = risk?.halted
      ? 'halted — daily loss limit'
      : `watching ${settings.symbols.length} coins`;
  }

  const eq = broker.equity(m);
  $('equity').textContent = fmtMoney(eq);
  const diff = eq - broker.startingCash;
  const pctChange = (diff / broker.startingCash) * 100;
  const pnlEl = $('pnl');
  pnlEl.textContent =
    `${fmtSigned(diff)} (${pctChange >= 0 ? '+' : ''}${pctChange.toFixed(3)}%)`;
  pnlEl.style.color = diff > 0 ? 'var(--up)' : diff < 0 ? 'var(--down)' : '';
}

/** The scanner: every watched coin, its impulse, and any open position. */
function renderScanner() {
  if (!broker) return;
  const m = marks();
  const thr = settings.strategy.entryThreshold;

  const rows = settings.symbols.map((sym) => {
    const ret = impulse.get(sym) ?? 0;
    const pos = broker.position(sym);
    return { sym, price: m[sym], ret, pos };
  }).sort((a, b) => Math.abs(b.ret) - Math.abs(a.ret));  // closest to firing first

  $('scanTable').querySelector('tbody').innerHTML = rows.map((r) => {
    const armed = Math.abs(r.ret) >= thr;
    const cls = r.pos.qty > 0 ? 'holding' : armed ? 'armed' : '';
    const posLabel = r.pos.qty > 0
      ? `<span class="side ${r.pos.side === SHORT ? 'short' : 'long'}"
          >${r.pos.side === SHORT ? 'S' : 'L'}</span>`
      : '';
    const u = r.pos.qty > 0 && m[r.sym] ? broker.unrealized(r.sym, m[r.sym]) : null;
    return `<tr class="${cls}" data-sym="${r.sym}">
      <td>${r.sym === focus ? '<b>' + r.sym + '</b>' : r.sym}</td>
      <td class="r">${r.price != null ? fmtPrice(r.price) : '—'}</td>
      <td class="r" style="color:${
        r.ret >= thr ? 'var(--up)' : r.ret <= -thr ? 'var(--down)' : 'inherit'}">
        ${r.ret >= 0 ? '+' : ''}${(r.ret * 100).toFixed(3)}%</td>
      <td class="r">${posLabel}${u !== null ? ' ' + fmtSigned(u) : ''}</td>
    </tr>`;
  }).join('');
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

  renderBreakEven();
}

/** The bar the strategy has to clear before it can make a cent. */
function renderBreakEven() {
  const be = breakEvenWinRate(settings.strategy, settings.broker);
  const el = $('breakeven');

  if (be.required === Infinity) {
    el.textContent =
      `A take-profit of ${(settings.strategy.takeProfit * 10000).toFixed(0)}bp ` +
      `nets ${be.netWinBp.toFixed(1)}bp after ${be.costBp.toFixed(1)}bp of costs — ` +
      `every winning trade still loses money. Raise take-profit or cut fees.`;
    el.className = 'hint verdict-bad';
    return;
  }

  const pct = be.required * 100;
  el.textContent =
    `Costs ${be.costBp.toFixed(1)}bp per round trip: a win nets ` +
    `+${be.netWinBp.toFixed(1)}bp, a loss costs −${be.netLossBp.toFixed(1)}bp, ` +
    `so this setup needs a ${pct.toFixed(0)}% win rate to break even.`;
  el.className = pct >= 75 ? 'hint verdict-bad'
    : pct >= 55 ? 'hint verdict-warn' : 'hint verdict-good';
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
      // Signed by direction, so a short that fell shows a positive move.
      const d = t.side === SHORT ? -1 : 1;
      const pct = ((d * (t.exitPrice - t.entryPrice)) / t.entryPrice) * 100;
      const held = ((t.exitTime - t.entryTime) / 1000).toFixed(1);
      const tag = t.side === SHORT ? 'S' : 'L';
      return `<li>
        <div>
          <div class="mono"><span class="side ${t.side === SHORT ? 'short' : 'long'}"
            >${tag}</span> ${escapeHtml(t.symbol)} ${fmtPrice(t.entryPrice)} → ${fmtPrice(t.exitPrice)}</div>
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
      const note = r.detail && r.status !== 'live'
        ? `<div class="feed-note">${escapeHtml(r.detail)}</div>` : '';
      return `<tr class="${isBest && !dead ? 'fastest' : ''} ${dead ? 'dead' : ''}">
        <td><div class="venue">
          <span class="dot" data-state="${r.status}"></span>
          ${escapeHtml(meta.label)}${feedRole(r.feedId)}
        </div>${note}</td>
        <td class="r">${rate}</td>
        <td class="r">${lag}</td>
        <td class="r">${r.price ? fmtPrice(r.price) : '—'}</td>
      </tr>`;
    })
    .join('');

  const anyLive = rows.some((r) => r.status === 'live');
  $('masterDot').dataset.state = !running ? 'idle' : anyLive ? 'live' : 'connecting';

  const sig = FEEDS[settings.signalFeed]?.label ?? '—';
  const exe = FEEDS[settings.execFeed]?.label ?? '—';
  $('feedLabel').textContent =
    settings.signalFeed === settings.execFeed ? sig : `${sig} → ${exe}`;
}

// ✦ marks the signal venue, ● the execution venue.
function feedRole(feedId) {
  const isSignal = feedId === settings.signalFeed;
  const isExec = feedId === settings.execFeed;
  if (isSignal && isExec) return ' ✦●';
  if (isSignal) return ' ✦';
  if (isExec) return ' ●';
  return '';
}

function renderLeadLag() {
  const cost = roundTripCostBp(settings.broker);
  $('costLabel').textContent = `${cost.toFixed(1)}bp`;

  const rows = leadlag.summary();
  $('leadlagTable').querySelector('tbody').innerHTML = rows
    .map((r) => {
      const has = r.samples > 0;
      const med = has ? r.medianBp : null;
      // Only a follow-through that clears the round trip is tradeable.
      const cls = !has ? '' : med >= cost ? 'edge' : med > 0 ? 'weak' : 'noedge';
      return `<tr class="${cls}">
        <td>${r.horizon < 1000 ? `${r.horizon}ms` : `${r.horizon / 1000}s`}</td>
        <td class="r">${has ? r.samples : '—'}</td>
        <td class="r">${has ? `${med >= 0 ? '+' : ''}${med.toFixed(2)}` : '—'}</td>
        <td class="r">${has ? `${(r.hitRate * 100).toFixed(0)}%` : '—'}</td>
      </tr>`;
    })
    .join('');

  $('llEvents').textContent = leadlag.events;

  const best = rows.filter((r) => r.samples >= 30)
    .reduce((a, r) => (a === null || r.medianBp > a.medianBp ? r : a), null);
  const verdict = $('llVerdict');
  if (!best) {
    verdict.textContent = 'Collecting samples — needs 30+ per horizon to mean anything.';
    verdict.className = 'hint';
  } else if (best.medianBp >= cost) {
    verdict.textContent =
      `Follow-through at ${best.horizon}ms (${best.medianBp.toFixed(2)}bp) clears the ` +
      `${cost.toFixed(1)}bp round trip. Worth paper-trading — but you still have to ` +
      `reach the exchange inside that window.`;
    verdict.className = 'hint verdict-good';
  } else {
    // Say "30+ samples" explicitly: a thinner horizon may show a bigger number
    // in the table, and it would look like this is contradicting it.
    verdict.textContent =
      `Best follow-through over horizons with 30+ samples is ` +
      `${best.medianBp.toFixed(2)}bp at ${best.horizon}ms — under the ` +
      `${cost.toFixed(1)}bp round trip. No tradeable edge at these fees yet.`;
    verdict.className = 'hint verdict-bad';
  }
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
  const opts = Object.entries(FEEDS)
    .map(([id, m]) => `<option value="${id}">${m.label}</option>`)
    .join('');
  $('setSignalFeed').innerHTML = opts;
  $('setExecFeed').innerHTML = opts;

  $('setFeePreset').innerHTML = FEE_PRESETS
    .map((p) => `<option value="${p.id}">${p.label}${
      p.bps === null ? '' : ` — ${p.bps}bp`}</option>`)
    .join('');
  // Picking a preset just fills the fee box; the box stays the source of truth.
  $('setFeePreset').addEventListener('change', (e) => {
    const preset = FEE_PRESETS.find((p) => p.id === e.target.value);
    if (preset && preset.bps !== null) $('setFee').value = preset.bps;
  });
  $('setFee').addEventListener('input', () => { $('setFeePreset').value = 'custom'; });

  // Warn as soon as the venue is picked, not after Apply.
  $('setExecFeed').addEventListener('change', updateSpotWarning);
  $('setAllowShorts').addEventListener('change', updateSpotWarning);

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
  $('resetLl').addEventListener('click', () => { leadlag.reset(); renderLeadLag(); });
}

/** Shorts on a spot venue are simulated but not actually placeable. */
function updateSpotWarning() {
  const isSpot = FEEDS[$('setExecFeed').value]?.spot;
  const wantsShorts = $('setAllowShorts').checked;
  $('spotShortWarning').classList.toggle('hidden', !(isSpot && wantsShorts));
}

function syncSettingsUI() {
  $('setSymbols').value = settings.symbols.join(', ');
  $('setMaxPos').value = settings.risk.maxConcurrentPositions;
  $('setSignalFeed').value = settings.signalFeed;
  $('setExecFeed').value = settings.execFeed;
  $('setLlThreshold').value = settings.leadlag.thresholdBp;
  $('setEntry').value = (settings.strategy.entryThreshold * 100).toFixed(3);
  $('setTp').value = (settings.strategy.takeProfit * 100).toFixed(3);
  $('setSl').value = (settings.strategy.stopLoss * 100).toFixed(3);
  $('setLookback').value = settings.strategy.lookbackSeconds;
  $('setFee').value = settings.broker.feeBps;
  $('setCash').value = settings.broker.startingCash;
  $('setAllowShorts').checked = !!settings.strategy.allowShorts;
  const match = FEE_PRESETS.find((p) => p.bps === settings.broker.feeBps);
  $('setFeePreset').value = match ? match.id : 'custom';
  updateSpotWarning();
  document.querySelectorAll('#raceChecks input').forEach((cb) => {
    cb.checked = settings.racing.includes(cb.value);
  });
}

function readSettingsUI() {
  const num = (id, fallback) => {
    const v = parseFloat($(id).value);
    return Number.isFinite(v) ? v : fallback;
  };
  settings.symbols = parseSymbols($('setSymbols').value);
  settings.risk.maxConcurrentPositions =
    Math.max(1, Math.round(num('setMaxPos', DEFAULTS.risk.maxConcurrentPositions)));
  settings.signalFeed = $('setSignalFeed').value;
  settings.execFeed = $('setExecFeed').value;
  settings.leadlag.thresholdBp = num('setLlThreshold', DEFAULTS.leadlag.thresholdBp);
  settings.racing = [...document.querySelectorAll('#raceChecks input')]
    .filter((cb) => cb.checked)
    .map((cb) => cb.value);
  settings.strategy.allowShorts = $('setAllowShorts').checked;
  settings.strategy.entryThreshold = num('setEntry', 0.15) / 100;
  settings.strategy.takeProfit = num('setTp', 0.2) / 100;
  settings.strategy.stopLoss = num('setSl', 0.15) / 100;
  settings.strategy.lookbackSeconds = num('setLookback', 5);
  settings.broker.feeBps = num('setFee', DEFAULTS.broker.feeBps);
  settings.broker.startingCash = num('setCash', DEFAULTS.broker.startingCash);
}

/** "btc, eth , sol" -> ['BTC','ETH','SOL'], deduped and capped. */
function parseSymbols(raw) {
  const out = [];
  for (const part of String(raw).split(/[\s,]+/)) {
    const sym = part.trim().toUpperCase();
    if (sym && !out.includes(sym)) out.push(sym);
  }
  return out.length ? out.slice(0, 20) : [...DEFAULTS.symbols];
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

// Tapping a scanner row focuses that coin in the big price card.
$('scanTable').addEventListener('click', (e) => {
  const row = e.target.closest('tr[data-sym]');
  if (!row) return;
  focus = row.dataset.sym;
  history = [];
  msgTimestamps = [];
  leadlag.reset();
  renderScanner();
  renderPrice();
});

buildSettingsUI();
newSession();
renderScanner();
renderRace();
renderLeadLag();

// One render loop keeps the UI smooth no matter how fast ticks arrive —
// rendering per-tick would melt the phone at 100+ msg/s.
setInterval(() => {
  renderPrice();
  renderPosition();
  renderScanner();
  renderRace();
}, 250);
setInterval(renderSpark, 500);
setInterval(renderLeadLag, 1000);
