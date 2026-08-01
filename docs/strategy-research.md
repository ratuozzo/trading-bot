# Strategy research — what the evidence says survives fees

Researched July 2026, to avoid re-testing what others have already settled.
Everything here is filtered through one question: **does it still work after
transaction costs?** At MEXC's 8bp API taker rate a round trip is ~20bp, and
that number has killed every strategy this repo has tested so far.

## The filter that decides everything: turnover

Cost drag is turnover × 20bp. Nothing else about a strategy matters until
this number is survivable.

| Strategy shape | Trades/yr | Cost/yr | Verdict |
| --- | ---: | ---: | --- |
| Our 5m impulse (41/day) | 14,965 | **2993%** | fatal |
| Our 1h impulse (3.3/day) | 1,204 | **241%** | fatal |
| Daily rebalance | 365 | 73% | fatal |
| 28-day lookback / 5-day hold | 73 | 15% | heavy |
| Weekly rebalance | 52 | 10% | heavy |
| Funding carry (~monthly) | 12 | 2.4% | negligible |

This is why everything we built lost. It was never primarily a signal
problem — a strategy trading 41 times a day needs to beat a 2993% annual
hurdle. No edge does that.

## What the literature says FAILS (don't re-test these)

**Intraday momentum.** Wen, Bouri, Xu & Zhao (2022) on Bitcoin
high-frequency data: predictability exists, but *"after considering the
transaction costs, the momentum effect cannot make investors obtain excess
returns."* We reproduced this independently.

**Short-horizon reversal / apparent weekly alpha.** *"Daily momentum
mean-reverts strongly once micro-structure frictions are accounted for, with
much of apparent weekly alpha attributable to bid-ask bounce and
dealer-inventory effects."* This independently confirms what we measured: a
stable ~1bp reversal at 5m that vanished into noise at 1h and 4h.

**Naive sign-based strategies.** Fail once 10bp costs are imposed — and MEXC
API costs double that.

**Anything tuned on one period.** Documented alpha decay of 9-76% between
first and second halves of study periods. Our own 4h test showed HYPE fading
at +231% in-sample and -71% out-of-sample from identical rules.

## What the literature says SURVIVES costs

Ordered by how well the cost structure fits this repo.

### 1. Funding-rate carry (delta-neutral) — TESTED, does not clear the bar

**Measured on MEXC over 540 days of real settlements. The public figures are
roughly an order of magnitude too high for this venue.**

| | BTC | ETH | SOL |
| --- | ---: | ---: | ---: |
| Mean funding per 8h | +0.0033% | +0.0026% | **-0.0002%** |
| Gross annualised | +3.6% | +2.9% | **-0.3%** |
| Settlements positive | 78% | 73% | 57% |
| Break-even hold | 27 days | 33 days | never |
| 30-day windows clearing cost | 60% | 49% | 24% |
| Worst negative run | -0.46% | -0.64% | -2.62% |

And that is return on *notional*. Carry funds two legs, so return on capital
is lower again:

| Perp leg leverage | Capital for $10k notional | Return on capital |
| --- | ---: | ---: |
| none | $20,000 | **1.65%/yr** |
| 3x | $13,333 | 2.47%/yr |
| 5x | $12,000 | 2.75%/yr |

US T-bills pay ~4-5% with no counterparty risk, no liquidation risk, and no
funds sitting on an offshore exchange. On MEXC's current funding regime the
carry loses to cash.

A conditional version — only entering when funding spikes — does not rescue
it either: over 540 days, funding on BTC and ETH **never once** exceeded
0.01% per 8h, and the three SOL triggers netted -0.06% of notional.

The strategy is not broken; MEXC's funding is simply too low right now. The
quoted 10-30% APY describes leverage-mania conditions, and this 540-day
window contains none of them. Worth re-measuring if funding regimes change —
`scripts/funding_analysis.py` re-runs the whole check in a minute.

### 1b. Funding-rate carry — the original literature claim

Long spot, short the perp when funding is positive; collect the 8-hourly
funding payment. **Not a directional bet** — it's a carry trade, so the cost
structure inverts: you pay ~20bp once on entry and once on exit, then earn
continuously for as long as you hold.

- Baseline funding ~0.01% per 8h ≈ **11%/yr** on notional
- Reported real-world range **10-30% APY**, low directional risk
- Entry/exit cost at monthly turnover ≈ **2.4%/yr**

Risks that matter: funding flips negative; liquidation on the short leg if
the hedge drifts; both legs need capital; execution slippage on two markets
at once. Sources note most retail attempts lose *because they ignore fees
and don't monitor delta in real time* — which is precisely the discipline
this repo has already built.

MEXC has both spot and perps, so this is implementable on the venue we're
already on.

### 2. Time-series momentum at multi-week horizons

Buy when the lookback return is in the top third of its history.

- **28-day lookback, 5-day hold: Sharpe 1.51** vs 0.84 for buy-and-hold
- Weekly rebalancing (Sharpe 0.24) beats daily (0.18) — turnover hurts
- ~73 trades/yr → ~15% annual cost drag, which is heavy but not fatal

### 3. Cross-sectional momentum / risk-managed variants

Rank coins, hold winners, rebalance weekly.

- Risk-managed weekly portfolio: 3.47% average weekly return vs 3.18%
  conventional, with *lower* volatility (17.66% vs 20.44%)
- ~52 trades/yr → ~10% cost drag

### 4. Size and volume factor portfolios

Reported to survive costs at **10-16% weekly turnover** — the lowest-turnover
survivors in the anomalies literature.

### 5. Pairs trading / statistical arbitrage

Reported abnormal returns of ~12%/month exceeding conservative cost
estimates. Market-neutral. Higher turnover than carry, and the reported
figure looks optimistic against everything else here — treat with caution
until reproduced.

### 6. Cost-aware execution filter (applies to any of the above)

The single most transferable idea found: **only take a trade when the
forecast magnitude exceeds a transaction-cost-derived threshold.** Reported
to restore profitability in otherwise-unprofitable configurations. This is
the generalisation of the break-even calculation already in this repo — the
difference is using it to *gate entries*, not just to report a bar.

## What this implies for us

The three tested-and-failed strategies here all shared one property: high
turnover. The survivors all share the opposite. Any next attempt should be
chosen on turnover first and signal second.

Reproduction order, easiest and most cost-robust first:

1. **Funding carry** — needs a funding-rate feed, no directional forecast at
   all, and the cost structure genuinely inverts
2. **28d/5d time-series momentum** — reuses the existing kline fetcher and
   backtester almost unchanged
3. **Cross-sectional momentum** — same data, ranks across coins

## Sources

- [Intraday return predictability in cryptocurrency markets: momentum, reversal, or both (Wen, Bouri, Xu & Zhao)](https://www.sciencedirect.com/science/article/abs/pii/S1062940822000833) · [SSRN copy](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4135239_code2537556.pdf?abstractid=4080253&mirid=1)
- [Cryptocurrency anomalies and economic constraints](https://www.sciencedirect.com/science/article/abs/pii/S1057521924001509)
- [Technical analysis in cryptocurrency markets: do transaction costs and bubbles matter?](https://www.sciencedirect.com/science/article/abs/pii/S1042443122000816)
- [Time-series and cross-sectional momentum in the cryptocurrency market](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
- [Cryptocurrency market risk-managed momentum strategies](https://www.sciencedirect.com/science/article/abs/pii/S1544612325011377)
- [Machine learning-based Bitcoin trading under transaction costs](https://arxiv.org/html/2606.00060)
- [Exploring risk and return profiles of funding rate arbitrage on CEX and DEX](https://www.sciencedirect.com/science/article/pii/S2096720925000818)
- [Pairs trading in the cryptocurrency market](https://thesis.eur.nl/pub/67552/Thesis-Pairs-trading-.pdf)
- [Momentum and liquidity in cryptocurrencies](https://arxiv.org/pdf/1904.00890)
