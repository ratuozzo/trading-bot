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

### 2. Time-series momentum (28d/5d) — TESTED, does not reproduce

**The first strategy here whose failure is not about fees.** Tested on 26
coins over 6.2 years of daily candles (`scripts/backtest_tsmom.py`), with
warm-up excluded from scoring and every signal executed at the *next* open.

Turnover came in far below the estimate — 23 trades/yr, not 73, because a
5-day hold usually re-confirms rather than flips. That is ~2%/yr of cost
drag, entirely survivable. The strategy still lost money:

| Equal-weight portfolio, 5.2 yrs scored | Return | Sharpe | vs buy&hold |
| --- | ---: | ---: | ---: |
| 28d/5d, original 13 coins | **-6.7%** | 0.21 | bh -35.6%, Sharpe 0.23 |
| 28d/5d, holdout 13 coins | **-17.9%** | 0.22 | bh -90.3%, Sharpe -0.17 |

The published Sharpe 1.51 does not appear. What does appear is 0.2 — the
same risk-adjusted return as buy-and-hold, reached by a much more
complicated route.

**Costs are not the binding constraint.** Re-run at zero fees and zero
slippage, out-of-sample Sharpe moves from -0.03 to +0.03. The turnover
filter did its job; the signal simply is not there.

**Walk-forward says regime, not edge.** Five sequential ~15-month blocks
with parameters held fixed: 3/5 positive Sharpe on the original coins, 3/5
on the holdout. That is a coin flip.

**A better-looking variant was selection noise.** A 25-cell sweep over
lookback and hold put 90d/5d well ahead (4/5 blocks positive, +28.8% where
28d/5d lost). On 13 coins never used to find it, it returned **-48.2%**
(Sharpe 0.05). Picking the best of ~30 variants and calling it an edge is
overfitting the test set instead of the training set; the holdout is what
catches it.

The one durable property: it *does* cut drawdowns and beat buy-and-hold's
Sharpe in falling markets (holdout -17.9% against -90.3%). That is real, and
it is worth nothing on its own — a strategy returning -7% is beaten by cash.

### 2b. Time-series momentum — the original literature claim

Buy when the lookback return is in the top third of its history.

- **28-day lookback, 5-day hold: Sharpe 1.51** vs 0.84 for buy-and-hold
- Weekly rebalancing (Sharpe 0.24) beats daily (0.18) — turnover hurts
- ~73 trades/yr → ~15% annual cost drag, which is heavy but not fatal

### 3. Cross-sectional momentum — TESTED, the result is coin selection

Rank the universe by 28-day return, long the top third, short the bottom
third, rebalance weekly, dollar-neutral (`scripts/backtest_xsmom.py`).

The first run looked like the best result this project has produced: on 13
large caps, **+352.9% over 6.2 years, Sharpe 1.01, and 5/5 walk-forward
blocks positive**, with a −23% drawdown against buy-and-hold's −37% test
loss. Market-neutrality appeared to be doing exactly what it promised.

Then the same parameters ran on coins that had not been looked at:

| Universe (28d rank / 7d hold, top&bottom third) | Return | Sharpe | Test Sharpe |
| --- | ---: | ---: | ---: |
| A — 13 large caps *(the set it was first run on)* | **+352.9%** | **1.01** | 0.68 |
| A+B — 26 coins | +173.4% | 0.80 | 0.39 |
| B — 13 mid-cap alts | +94.1% | 0.50 | **-1.01** |
| C — 17 older small caps | **-90.3%** | **-0.98** | -0.17 |
| **A+B+C — all 43 coins** | **+17.5%** | **0.23** | 0.32 |

Identical rules, identical dates, identical costs. Sharpe swings from +1.01
to −0.98 on nothing but the choice of coins. **That is not an edge, it is a
coin-selection lottery** — and the winning ticket happened to be the set
looked at first, which is exactly how this failure mode always presents.

On the realistic universe — rank across everything liquid, all 43 coins —
the strategy returns **+17.5% over 6.2 years, about 2.6%/yr**, against a
3.5%/yr cost drag and a T-bill at 4-5%. Gross of costs it earns ~6%/yr, so
here fees are roughly half the problem rather than all of it or none of it.

The small-cap collapse is not a data artifact. Every daily move above 60% in
the panel was checked and they are real events (DOGE's +387% in January 2021,
XRP on the Ripple ruling, ALGO in November 2021). The short leg on small caps
gets squeezed by genuine moves, which is a real risk of the trade, not a bug
in the backtest.

Two parameter notes, both reported rather than acted on. A 40-cell sweep
favoured a shorter 14d rank with a 7d hold (test Sharpe 1.34 on the combined
26). On the 17 coins never used to find it, that variant returned **-55.6%**.
And a long-only version of the same ranking gives up almost everything
out-of-sample (Sharpe 0.02 vs 0.68), which does support the underlying
theory: subtracting the common "all coins move together" factor is the part
that works. It just is not worth enough to pay for.

### 3b. Cross-sectional momentum — the original literature claim

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

The original theory was that turnover was the whole problem. Testing changed
that conclusion in a way worth recording.

**Cost was the binding constraint for the high-turnover strategies, and it is
not the binding constraint any more.** The 5-minute impulse needed to clear
2993%/yr and never could. Time-series momentum only needed to clear ~2%/yr —
and still lost, at zero fees too. Lowering turnover moved the failure from
"the fee eats it" to "there is nothing to eat", which is progress in
understanding and no progress in P&L.

Scorecard so far, all measured rather than assumed:

| Strategy | Turnover | Net result | Blocked by |
| --- | ---: | ---: | --- |
| Tick momentum (2s) | ~41/day | negative | cost |
| Elder Impulse (5m/1h/4h) | 3-41/day | negative | cost |
| Funding carry | ~monthly | +1.7-2.8%/yr | yields less than T-bills |
| Time-series momentum 28d/5d | 23/yr | -1.3%/yr | no out-of-sample signal |
| Cross-sectional momentum | 35/yr | +2.6%/yr | universe-dependent, below cash |

Nothing tested has beaten a Treasury bill. Two of the five now clear their
own transaction costs, which the first three never did — the failures moved
from arithmetic to genuine absence of signal.

Three methodological rules earned the hard way, worth keeping for anything
tested next:

1. **Hold out coins, not just dates.** A single train/test split did not
   catch the 90d/5d variant; 13 unseen coins did, and reversed its sign.
   Cross-sectional momentum needed a *third* coin set before it broke.
2. **Report the universe as a parameter.** Coin choice moved cross-sectional
   Sharpe from +1.01 to -0.98 with nothing else changed. A backtest that
   names its parameters but not its universe has hidden its biggest one.
3. **Do not score the warm-up.** Including days when a rule structurally
   cannot fire reports it as "flat", which flatters it in a crash. Fixing
   this moved the time-series headline from +120% to -6.7%.

Remaining untested candidates:

- **Size / volume factor portfolios** — lowest turnover in the literature;
  needs the volume field the strategy currently discards. Note the warning
  above applies with full force: a size factor *is* a statement about the
  universe, and the universe is what just failed.
- **Cost-aware execution filter** — not a strategy, a gate. Only useful
  bolted onto something that already has a signal, and nothing tested has one.

The more informative finding is not on this list. Across five strategies the
binding constraint moved from cost to signal, and the best honest result is
~2.6%/yr against a 4-5% risk-free rate. On this venue, at 8bp API taker, the
largest single lever left is **the fee tier itself** — the MEXC app charges
0.01% where the API charges 0.08%. That is an 8x difference on the one input
that has killed more of these tests than any signal choice.

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
