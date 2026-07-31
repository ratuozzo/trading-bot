# trading-bot

A real-time crypto **momentum scalping bot** with a **phone-friendly live
dashboard**. It streams tick-by-tick prices from free public exchange
WebSockets, hunts for small short-term impulses, and paper-trades them.

Three ways to run it:

| | What it is | Where it runs |
| --- | --- | --- |
| **Backend** (`src/tradingbot/server/`) | Always-on service: engine + API + serves the dashboard | A VPS, a Pi, Docker |
| **Dashboard** (`web/`) | Live UI. Views the backend, or trades in-browser if there isn't one | Your browser / phone |
| **CLI bot** (`src/tradingbot/`) | Headless single-symbol engine | A terminal |

**Closing the browser only stops trading in the in-browser mode.** With the
backend running, the engine lives in the server process and keeps going.

**All three are paper-trading only: no API keys, no real orders, no real money.**

> ⚠️ **Educational software, not financial advice.** Scalping small moves is
> hard — fees and slippage eat thin edges fast, and a strategy that looks good
> for an hour can lose money over a week. Paper-trade it for a long time before
> even thinking about real funds, and only risk what you can afford to lose.

---

## Which feed is actually fastest?

Short answer: **it depends on where you are**, so the dashboard measures it
instead of guessing.

A few things are worth knowing:

- **Perps lead spot.** Perpetual futures (Binance USD-M, Bybit linear, OKX
  swaps) are where leveraged flow lands first, so price discovery happens there
  and spot follows. Both the bot and dashboard now default to perps.
- **Binance spot isn't "slow" in the polling sense** — its streams are
  push-based, not interval-based. What held it back here was that it's *spot*.
- **Geography usually dominates.** Binance sits in AWS Tokyo; from Europe or
  the US that's 100-250ms of unavoidable round-trip. A closer venue can beat a
  "faster" one purely on distance.
- **OKX `bbo-tbt`** is genuinely tick-by-tick top-of-book, and **Bybit
  `orderbook.1`** pushes at ~10ms — both are excellent low-latency options.

### The feed race

The dashboard connects to several venues **at once** and reports which one
tells you about a move first.

It can't just compare prices: a perp and a spot book trade at genuinely
different prices (basis on BTC is often tens of dollars), so the same "level"
never lines up across venues. Instead each feed is judged on its **own relative
move** — when a feed's price shifts 2bp within a second, that's an event, and
events landing close together are treated as the same market move. The gap
between the first feed to flag it and the rest is the measurement.

Both timestamps come from *your device*, so results are free of exchange clock
skew and reflect true arrival order at your location. Crossing times are
linearly interpolated between ticks, which matters — without it the error is a
full tick interval, the same magnitude as the latencies being measured.

Against simulated feeds with known latencies, irregular tick timing and jitter,
the race recovers the correct ordering; absolute gaps read a few ms
conservative. **Trust the ranking more than the exact milliseconds.**

---

## The backend (always-on trading)

Without it, the dashboard *is* the bot: close the tab and everything stops.
The backend moves the engine into a long-running process, so the page becomes
a viewer that can come and go.

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m tradingbot.server --autostart
# open http://127.0.0.1:8787
```

The dashboard detects the backend automatically — same origin, no config. The
header badge reads **SERVER** instead of **IN-BROWSER**, and Start/Stop then
drive the service rather than the tab.

### Why it serves the dashboard itself

Not tidiness — necessity. A page served over HTTPS (like GitHub Pages) cannot
open a `ws://` socket or `fetch` over `http://`; browsers block it as mixed
content. So a Pages-hosted dashboard physically *cannot* talk to a bare-IP
backend. Serving both from one origin sidesteps that and CORS together.

The Pages deployment still works — it just falls back to trading in-browser.

### API

| | |
| --- | --- |
| `GET /api/state` | full snapshot |
| `GET /api/stream` | WebSocket, a snapshot ~2×/second |
| `POST /api/start` \| `/api/stop` \| `/api/reset` | control |
| `GET \| POST /api/config` | read/update strategy and risk settings |
| `GET /healthz` | liveness, no auth |

### Security

This API can start and stop trading, so **the server refuses to bind to
anything other than loopback without `TB_API_TOKEN`**. It fails at boot rather
than quietly exposing itself.

```bash
export TB_API_TOKEN=$(openssl rand -hex 32)
```

Pass it as `Authorization: Bearer <token>`, or `?token=…` for the WebSocket
(browsers can't set headers on a WS handshake). Enter it once in the
dashboard's Settings and it's remembered.

Safest exposure is not to expose it: keep it on loopback and reach it through
an SSH tunnel or a Cloudflare Tunnel, which also gives you HTTPS.

```bash
ssh -L 8787:127.0.0.1:8787 you@your-server   # then open localhost:8787
```

### Running it for real

```bash
export TB_API_TOKEN=$(openssl rand -hex 32)
docker compose up -d
```

Or as a service — `deploy/systemd/tradingbot.service` restarts on failure and
on boot. Put the token in `/etc/tradingbot.env`.

### Session persistence

State is written atomically (temp file + rename, so a crash mid-write can't
corrupt it) and reloaded on boot: a restart resumes the same cash, trade log
and P&L rather than silently starting a flattering fresh session. Stop/start
from the UI also preserves it. Only `POST /api/reset` clears it.

---

## The dashboard

```
web/
├── index.html
├── styles.css
└── js/
    ├── app.js       # wiring + rendering
    ├── feeds.js     # 6 exchange adapters + the race
    ├── strategy.js  # port of momentum.py
    ├── broker.js    # ports of paper.py / portfolio.py / risk.py
    └── config.js    # defaults, mirrors config.yaml
```

Feeds available: **MEXC perp** (default), Bybit perp, OKX swap, Hyperliquid,
plus Binance perp/spot — note Binance geo-blocks several regions and will fail
to connect from them.

It shows the live price and impulse, a 60s sparkline, a **scanner** covering
every watched coin, the feed race table, your positions and equity, session
stats, and a trade log. Settings persist in `localStorage`.

**Watching many coins.** The dashboard scans up to 20 symbols at once (10 by
default) over a single WebSocket per venue, each with its own independent
strategy state, all sharing one book of cash. `Max positions at once` caps how
many can be held simultaneously — without it the first signal of the session
would swallow all the capital and the other nine coins would never get a turn.
Tap any row in the scanner to focus that coin in the big price card.

More coins raises **how often** a setup appears. It does not make any
individual trade more likely to win, and it does not change the break-even
maths below.

The **backend** scans the same list server-side. Only the single-symbol CLI
bot (`python -m tradingbot.main`) is limited to one coin.

**Your phone connects straight to the exchanges.** There's no server relaying
ticks, which is both simpler and lower-latency than proxying through the Python
bot would be.

### Run it locally

```bash
cd web && python3 -m http.server 8000
# then open http://localhost:8000
```

To use it on your phone over your LAN, browse to `http://<your-computer-ip>:8000`.

---

## Deploying to GitHub Pages

`.github/workflows/deploy.yml` runs the Python tests plus a JS syntax check,
then publishes `web/` to GitHub Pages.

**One-time setup:** in the repo, go to **Settings → Pages → Build and
deployment → Source** and choose **GitHub Actions**. Without this the deploy
step fails with `Get Pages site failed … verify that the repository has Pages
enabled`.

Pushes to the default branch deploy automatically, and the workflow summary
shows the URL — typically `https://<user>.github.io/trading-bot/`. Pull
requests run the tests only.

> **If you later add a `main` branch** and make it the default, note that
> GitHub restricts the `github-pages` environment to the default branch. To
> deploy from a feature branch, add it under **Settings → Environments →
> github-pages → Deployment branches**.

Once it's live the page works on your phone like any website — add it to your
home screen for a full-screen, app-like view.

---

## The headless bot

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m tradingbot.main                          # MEXC perps, BTC_USDT
python -m tradingbot.main --exchange okx --symbol BTC-USDT-SWAP
python -m tradingbot.main --exchange bybit --symbol ETHUSDT --stream bookTicker
python -m tradingbot.main --exchange mexc --symbol ETH_USDT
```

> If the package isn't found, run from the repo root with
> `PYTHONPATH=src python -m tradingbot.main ...`, or `pip install -e .`.

Pipeline — every arrow is an interface, so any piece swaps out independently:

```
Exchange WS ──► Feed ──► Strategy ──► Risk ──► Broker ──► Portfolio
 (free, live)   Tick     Signal      sizing    fills      PnL
```

- **Feeds** — MEXC contract, Bybit v5, OKX v5, Binance (spot + USD-M futures).
  All auto-reconnect.
- **Strategy** — measures return over a lookback window; enters **long on an
  upward impulse and short on a downward one**; exits on take-profit,
  stop-loss, momentum reversal, or max hold.
- **Risk** — position sizing plus a daily loss limit that halts trading.
- **Broker** — `PaperBroker` simulates fills with fees and slippage.
  `binance_live.py` is a deliberate stub where real execution would go.
- **Portfolio** — round-trip trade log, win rate, PnL.

### On "defaults that are always profitable"

There is no such setting, and it is worth being plain about why rather than
shipping something that looks like one. Parameters decide **what you risk and
what you need**; the market decides whether you get it. What defaults *can* do
is make sure a winning trade is actually a win after costs — the old ones
failed even that test.

The defaults are now tuned so the break-even bar is about a **51% win rate**:
take-profit 0.60%, stop-loss 0.25%, on a ~18bp round trip. The strategy has to
be slightly better than a coin flip. Under the previous 0.20%/0.15% settings at
these fees, the bar was ~94% — unreachable.

The tension no setting escapes: fees push you toward bigger targets, and bigger
targets get hit less often. Widening take-profit lowers the bar you must clear
and simultaneously lowers how often you clear it. The dashboard shows the
break-even number live so you can see that trade-off as you tune, and the
session's actual win rate next to it so you can see whether you are beating it.

### Configuration

Edit `config.yaml`; every value can be overridden by an env var
(`TB_EXCHANGE`, `TB_SYMBOL`, `TB_MARKET`, `TB_STREAM`, `TB_ENTRY_THRESHOLD`, …).
Copy `.env.example` to `.env` for local overrides — it's git-ignored.

| Setting | Meaning |
| --- | --- |
| `feed.exchange` | `mexc` (default), `bybit`, `okx`, or `binance` |
| `feed.market` | `futures` (perps, faster) or `spot` |
| `feed.stream` | `trade`/`aggTrade` (prints) or `bookTicker` (top of book) |
| `strategy.entry_threshold` | Impulse size that triggers a buy (0.0015 = 0.15%) |
| `strategy.take_profit` / `stop_loss` | Exit targets |
| `broker.fee_bps` | Taker fee — **crucial**, must be well under your edge |
| `risk.daily_loss_limit_pct` | Auto-halt after this much daily loss |

**Watch the fees.** A round trip costs `2 × fee + 2 × slippage`. If take-profit
doesn't clearly beat that, the strategy loses money *even when its direction
calls are right*. At Binance spot's 10bp taker, the default 0.20% take-profit
nets **−2bp on a winning trade** — you would grind the account down while the
win-rate display reads 100%.

### What is a "bp"?

A **basis point** is 1/100th of a percent. 100bp = 1%, 10bp = 0.10%, 1bp =
0.01%. Fees and small price moves are quoted this way because saying "0.08%"
repeatedly gets unwieldy. A 0.60% take-profit is 60bp.

### Venue fee reality (checked July 2026)

All figures are **taker** rates, because an impulse strategy has to cross the
spread. Re-check your own account's fee page — these move.

| Venue | Taker (one way) | Round trip |
| --- | --- | --- |
| Binance USD-M perp | 0.05% | **10 bp** |
| MEXC spot | 0.05% | 10 bp |
| Binance spot + BNB discount | 0.075% | 15 bp |
| MEXC futures **via API** | 0.08% | 16 bp |
| Binance spot | 0.10% | 20 bp |

**MEXC is the default venue** because Binance geo-blocks a number of
jurisdictions outright — its API answers `HTTP 451, "restricted location"` —
so a Binance row will simply never populate from those places. The dashboard
now says so in the feed table instead of leaving a silent blank row.

**The MEXC trap.** MEXC advertises 0% maker and near-zero futures fees, and
that is real — for manual web/app trading. Orders sent through the **API are
billed on a separate schedule that overrides the displayed rates**, and API
accounts are excluded from the zero-fee promotions. That schedule was raised
three times in three months:

| Effective | API futures maker | API futures taker |
| --- | --- | --- |
| Mar 31, 2026 | 0.01% | 0.05% |
| May 1, 2026 | 0.04% | 0.06% |
| Jun 1, 2026 | 0.06% | 0.08% |

A bot is an API trader, so it pays the API rate — currently *worse* than
Binance perps. Building a strategy whose profitability depends on that number
means building on something that rose 6× in two months.

**Why 0% maker doesn't rescue it.** Zero-maker rates exist on several venues,
but this strategy structurally cannot reach them. Impulse-following is a taker
strategy: you detect a move and need in immediately. A post-only order either
never fills (you miss the move) or fills *because price came back to you* —
which usually means the impulse died. The maker rate is available mainly when
you are on the wrong side. Harvesting it means becoming a market maker, which
is a different and considerably harder business.

Also weigh **spread and depth**, not just the headline fee: thinner books mean
worse fills, and the simulator's flat slippage assumption will flatter a
low-liquidity venue.

Sources: [MEXC API futures fee update, Jun 1 2026](https://www.mexc.com/announcements/article/updates-to-api-futures-trading-fees-jun-1-2026-17827791535742),
[MEXC API futures launch, Mar 31 2026](https://www.mexc.com/announcements/article/introducing-api-futures-trading-on-mar-31-2026-17827791534551),
[MEXC fee overview](https://www.mexc.com/crypto-pulse/article/mexc-trading-fees-complete-guide-39643).

---

## Development

```bash
pip install -r requirements-dev.txt
pytest -q          # 30 tests, all offline
```

Tests cover the paper broker, strategy entry/exit rules, portfolio accounting,
risk limits, and feed URL/message parsing for all three exchanges.

The JS in `web/js/strategy.js` and `web/js/broker.js` is a deliberate port of
the Python modules — same parameter names, same rules — so results match. They
were verified to agree numerically. **If you tune one side, tune the other**,
or the dashboard and the headless bot will drift apart.

## Going live later (not implemented)

Real execution is intentionally left as a stub in `brokers/binance_live.py` so
you can't trade real money by accident. When you're ready:

1. Prove the strategy in paper mode first.
2. Create API keys with **trading** permission only — never withdrawal.
3. Export them as `TB_API_KEY` / `TB_API_SECRET` and set `TB_MODE=live`.
4. Implement `buy`/`sell`/`position`/`cash`, testing against the
   [Binance Spot Testnet](https://testnet.binance.vision/) first.

Nothing else changes — the engine, strategy, risk, and portfolio already talk
to the broker interface.
