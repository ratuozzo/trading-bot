# trading-bot

A real-time crypto **price-streaming momentum scalping bot**. It connects to a
free, public exchange WebSocket, watches the price tick-by-tick, and tries to
catch small short-term impulses — entering long when the market pushes up
sharply and exiting quickly on a target, stop, or reversal.

**It runs in paper-trading mode by default: no real money, no exchange account,
no API keys.** A live broker can be plugged in later (see below) without
changing the strategy or engine.

> ⚠️ **This is educational software, not financial advice.** Scalping tiny
> moves is hard: exchange fees and slippage eat small edges fast, and a strategy
> that looks good can still lose money live. Prove it in paper mode over a long
> period before you ever consider real funds — and only risk what you can afford
> to lose.

## How it works

```
Binance WebSocket ──► Feed ──► Strategy ──► Risk ──► Broker ──► Portfolio
  (free, live)       (Tick)   (Signal)    (sizing)  (fills)     (PnL)
```

- **Feed** (`feeds/binance.py`) — subscribes to Binance's free public streams
  (no key needed): `@trade` for the fastest last-price updates, or `@bookTicker`
  for live best bid/ask. Auto-reconnects with backoff.
- **Strategy** (`strategy/momentum.py`) — measures the return over a short
  lookback window; goes long on a strong enough upward impulse; exits on
  take-profit, stop-loss, a quick momentum reversal, or a max hold time.
- **Risk** (`risk.py`) — sizes each order and halts trading after a daily loss
  limit.
- **Broker** (`brokers/paper.py`) — simulates fills with fees + slippage and
  tracks cash/positions. `brokers/binance_live.py` is the stub where real order
  execution goes.
- **Portfolio** (`portfolio.py`) — logs round-trip trades and reports win rate
  and PnL.

Every arrow above is an interface, so any piece (exchange, strategy, broker)
can be swapped independently.

## Quick start

```bash
# 1. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run (paper trading, streaming live BTC/USDT prices)
python -m tradingbot.main --config config.yaml
```

You'll see live status lines, plus `BUY`/`SELL` logs whenever the strategy
trades, and a summary when you stop it with `Ctrl-C`.

Try another coin or the bid/ask stream:

```bash
python -m tradingbot.main --symbol ethusdt
python -m tradingbot.main --symbol solusdt --stream bookTicker
```

> If `python -m tradingbot.main` can't find the package, run from the repo root
> with `PYTHONPATH=src python -m tradingbot.main ...`, or `pip install -e .`.

## Configuration

Edit `config.yaml`. The most important knobs:

| Setting | Meaning |
| --- | --- |
| `feed.symbol` | Which market, e.g. `btcusdt`, `ethusdt` (lowercase) |
| `feed.stream` | `trade` (last price, fastest) or `bookTicker` (bid/ask) |
| `strategy.entry_threshold` | How big an impulse triggers a buy (0.0015 = 0.15%) |
| `strategy.take_profit` / `stop_loss` | Exit targets |
| `broker.mode` | `paper` or `live` |
| `broker.fee_bps` | Taker fee — **crucial**; must be smaller than your edge |
| `risk.daily_loss_limit_pct` | Auto-halt after this much daily loss |

Any value can be overridden with an environment variable (e.g. `TB_SYMBOL`,
`TB_ENTRY_THRESHOLD`, `TB_MODE`). Copy `.env.example` to `.env` for your local
overrides — `.env` is git-ignored.

**Watch the fees.** With a 0.10% taker fee each way, a round trip costs ~0.20%.
Your `take_profit` and `entry_threshold` need to clear that plus slippage just
to break even. Tune in paper mode.

## Going live later (not implemented yet)

The wiring is ready but real order execution is intentionally left as a stub in
`brokers/binance_live.py` so you don't trade real money by accident. When you're
ready:

1. Test your strategy in paper mode until you're confident.
2. Create Binance API keys with **trading** permission only (no withdrawal).
3. Put them in env vars — never in git:
   ```bash
   export TB_API_KEY=...   TB_API_SECRET=...   TB_MODE=live
   ```
4. Implement `buy`/`sell`/`position`/`cash` in `BinanceLiveBroker` using signed
   REST calls, and test against the **Binance Spot Testnet** first
   (<https://testnet.binance.vision/>).

Nothing else changes — the engine, strategy, risk, and portfolio already talk to
the broker interface.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests cover the paper broker, the strategy's entry/exit logic, the portfolio
accounting, and the risk limits — all offline, no network needed.

## Project layout

```
src/tradingbot/
├── main.py          # CLI entry point
├── engine.py        # ties feed → strategy → risk → broker → portfolio
├── config.py        # YAML + env config
├── models.py        # Tick, Signal, Order, Fill, Position, Trade
├── feeds/           # price sources (Binance WebSocket)
├── strategy/        # MomentumScalper
├── brokers/         # PaperBroker + live stub
├── portfolio.py     # trade log & performance
└── risk.py          # position sizing & loss limits
tests/               # offline unit tests
```
