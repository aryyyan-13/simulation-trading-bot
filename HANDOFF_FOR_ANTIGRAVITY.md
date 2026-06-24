# Project Handoff Briefing — read this first, then SOURCES.md and README.md

You're continuing a paper-trading bot project that another AI (Claude) started.
Paste this whole file to your coding agent as the first message. The project
files are in this same folder — open the folder as the workspace root.

## The one rule that overrides everything else
**Never fake a fill, a price, or a profit.** No formula, fee, API detail, or
number may be used unless it's verified against an official primary source,
with a link. If something can't be verified, say so plainly and name the page
to check — don't guess. This rule shaped every file in this project; keep
following it.

## What this project is
A bot that trades **real, live prices** with **fake money**, charging itself
real fees, real funding, and real slippage, so that what it reports is true.
Order: (1) project setup [done], (2) honest execution engine [done for the
core math, see below], (3) a few simple strategies + a backtester that scores
them on real history and only keeps ones that clear their costs [not started],
(4) fractional-Kelly position sizing + a reset-and-learn loop for big losses +
a live browser dashboard [not started], (5) run it on paper, report honestly,
explain what would have to change before real money [not started].

## Key decision already made, and why
Market: **Binance USDⓈ-M Futures** (perpetual contracts), public market data
only, no account or API key needed. Chosen specifically because the user
asked for real **funding** modeling — funding is a specific, exchange-published
mechanism unique to perpetual futures; there's no free, no-signup equivalent
for forex or stocks. Full reasoning and every verified number/endpoint is in
`SOURCES.md` — do not change any of those numbers without updating that file
with a new verified link first.

## Important environmental difference vs. where this was built
Claude's build sandbox could not reach `fapi.binance.com` (network allowlist
blocked it — proven, not assumed). That's likely **not** a problem in
Antigravity if your environment has normal internet access. That means you
should actually be able to run `connectivity_check.py` for real and get a
live price back — do that first and confirm it against
https://www.binance.com/en/futures/BTCUSDT before trusting anything built on
top of it.

## Current state of the code (all in this folder)
- `SOURCES.md` — every verified fact, with official links and the date checked
  (2026-06-17). Read this before touching `config.py`.
- `config.py` — fees (maker 0.02%/taker 0.05%, Regular ), funding interval,
  endpoint paths, starting fake balance ($10,000), leverage locked at 1x.
- `exchange_client.py` — real HTTP calls to Binance's public futures
  market-data endpoints (order book, mark price + funding, klines). No API key
  used anywhere.
- `engine/execution.py` — walks real order-book levels for honest slippage,
  computes verified fees and funding, closes positions truthfully (a loss can
  never be shown smaller than it is; if the book can't fully fill a close, the
  position is left honestly partially-open instead of faking the rest).
- `engine/portfolio.py` — fake-money balance + full trade ledger.
- `tests/test_execution.py` — 11 tests, all passing, proving the above (run
  `python -m pytest tests/ -v` to re-verify before building further).
- `connectivity_check.py` — run this on a real-internet machine to prove live
  data works; it deliberately fails loudly instead of faking a price if the
  connection doesn't work.

## What is explicitly NOT done yet, and must not be assumed
- No trading strategies exist yet.
- No backtester exists yet. Historical data approach (how to actually pull
  klines for backtesting) was not yet finalized when this handoff was written
  — verify the approach against `SOURCES.md` and Binance's klines docs before
  building it.
- No leverage/liquidation math — deliberately locked at 1x because
  maintenance-margin tiers haven't been verified against an official source
  yet. Don't add leverage until that verification happens.
- No Kelly-criterion sizing, no reset-and-learn loop, no dashboard.
- Nothing has touched real money or will, until the user explicitly says so
  after seeing honest paper-trading results.

## How to keep working honestly
1. Before adding any new formula/fee/API call: find the official source,
   read it yourself, add it to `SOURCES.md` with the link and date, then code
   it.
2. After building any piece: write a test, run it, show the passing output.
   Don't claim something works without having run it.
3. When you build the backtester (step 3): it should be capable of rejecting
   every strategy you try. That's a feature, not a bug — say so plainly if it
   happens, rather than tuning the test until something passes.
4. Keep the user's plain-language, no-jargon style: short steps, one-line
   meaning for any technical term, straight talk about risk and luck vs. edge.
