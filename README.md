# trading-bot

A real-time crypto **momentum scalping bot** with a **phone-friendly live
dashboard**. It streams tick-by-tick prices from free public exchange
WebSockets, hunts for small short-term impulses, and paper-trades them.

Two ways to run it:

| | What it is | Where it runs |
| --- | --- | --- |
| **Dashboard** (`web/`) | Live UI, feed race, paper trading | Your browser / phone, deployed to GitHub Pages |
| **Bot** (`src/tradingbot/`) | Headless engine, same strategy | Your machine / a server |

**Both are paper-trading only: no API keys, no real orders, no real money.**

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

Feeds available: Binance perp, Binance spot, Bybit perp, OKX swap,
Hyperliquid, Coinbase.

It shows the live price and impulse, a 60s sparkline, the feed race table, your
position and equity, session stats, and a trade log — plus settings for asset,
which feed to trade, which to race, and the strategy thresholds. Settings
persist in `localStorage`.

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
step fails with "Pages is not enabled".

After that, pushes to `main` deploy automatically and the workflow summary
shows the URL (typically `https://<user>.github.io/trading-bot/`). Pull
requests run the tests only.

> **Deploying from a feature branch:** GitHub restricts the `github-pages`
> environment to the default branch by default. If you want to deploy this
> branch before merging, add it under **Settings → Environments →
> github-pages → Deployment branches**. Otherwise just merge to `main`.

Once it's live the page works on your phone like any website — add it to your
home screen for a full-screen, app-like view.

---

## The headless bot

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m tradingbot.main                          # Binance perps, BTC
python -m tradingbot.main --exchange okx --symbol BTC-USDT-SWAP
python -m tradingbot.main --exchange bybit --symbol ETHUSDT --stream bookTicker
python -m tradingbot.main --market spot --stream trade
```

> If the package isn't found, run from the repo root with
> `PYTHONPATH=src python -m tradingbot.main ...`, or `pip install -e .`.

Pipeline — every arrow is an interface, so any piece swaps out independently:

```
Exchange WS ──► Feed ──► Strategy ──► Risk ──► Broker ──► Portfolio
 (free, live)   Tick     Signal      sizing    fills      PnL
```

- **Feeds** — Binance (spot + USD-M futures), Bybit v5, OKX v5. All auto-reconnect.
- **Strategy** — measures return over a lookback window; goes long on a strong
  upward impulse; exits on take-profit, stop-loss, momentum reversal, or max hold.
- **Risk** — position sizing plus a daily loss limit that halts trading.
- **Broker** — `PaperBroker` simulates fills with fees and slippage.
  `binance_live.py` is a deliberate stub where real execution would go.
- **Portfolio** — round-trip trade log, win rate, PnL.

### Configuration

Edit `config.yaml`; every value can be overridden by an env var
(`TB_EXCHANGE`, `TB_SYMBOL`, `TB_MARKET`, `TB_STREAM`, `TB_ENTRY_THRESHOLD`, …).
Copy `.env.example` to `.env` for local overrides — it's git-ignored.

| Setting | Meaning |
| --- | --- |
| `feed.exchange` | `binance`, `bybit`, or `okx` |
| `feed.market` | `futures` (perps, faster) or `spot` |
| `feed.stream` | `trade`/`aggTrade` (prints) or `bookTicker` (top of book) |
| `strategy.entry_threshold` | Impulse size that triggers a buy (0.0015 = 0.15%) |
| `strategy.take_profit` / `stop_loss` | Exit targets |
| `broker.fee_bps` | Taker fee — **crucial**, must be well under your edge |
| `risk.daily_loss_limit_pct` | Auto-halt after this much daily loss |

**Watch the fees.** A round trip costs roughly `2 × fee + slippage`. At 5bps
that's ~0.12% before you make a cent, so a 0.20% take-profit keeps under half
its gross. If take-profit doesn't clearly beat the round-trip cost, the strategy
loses money *even when its direction calls are right*.

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
