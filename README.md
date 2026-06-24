# Honest Paper-Trading Bot — Step 1 + start of Step 2

## What this is, in plain words
A bot that watches **real, live Binance Futures prices** and trades with
**fake money**, charging itself the **real fees** and **real funding** a real
trader would pay, and modeling **real slippage** from the real order book.
Every number it uses is written down in `SOURCES.md` with the official page
it came from.

## The one honest limitation you need to know
The computer I (Claude) build code on cannot reach the internet except for a
short allowlist (GitHub, PyPI, npm, etc.) — no exchange is on it. I proved
this with a direct test (see `SOURCES.md` #5). That means:
- I can write, and unit-test, every piece of math here.
- I **cannot** personally run the live price feed — that has to happen on
  your computer, where normal internet access works.
- `connectivity_check.py` lets you verify it yourself in under a minute.

## What's built and proven so far
| Piece | Status |
|---|---|
| Verified fee/funding/endpoint facts, each with an official link | Done — `SOURCES.md` |
| Exchange client (live price, order book, funding, historical candles) | Written — needs your machine to actually run |
| Execution engine: real order-book slippage, fees, funding, truthful loss accounting | Done, 11/11 unit tests passing |
| Paper portfolio (fake balance + full trade ledger) | Done, tested |
| Connectivity self-check script | Done — fails loudly instead of faking data when blocked |
| Strategies + backtester that scores them on real history | Not started (Step 3) |
| Kelly-fraction position sizing + reset-and-learn loop | Not started (Step 4) |
| Live browser dashboard | Not started (Step 4) |
| Paper-trading run + honest report | Not started (Step 5) |

## How to run this yourself, right now
```bash
cd trading-bot
pip install -r requirements.txt
python connectivity_check.py      # proves it's pulling a REAL live price
python -m pytest tests/ -v        # proves the fee/slippage/funding math is correct
```

## Why Binance USDⓈ-M Futures specifically
You asked for real funding modeling. Funding is a specific, exchange-published
mechanism on crypto perpetual futures with no equivalent free/no-signup data
source in forex or stocks. Full reasoning in `SOURCES.md`.

## Real risks, said plainly (more detail as we build further)
- This is currently a fee/funding/slippage **calculator with a paper ledger**,
  not yet a strategy or a bot that decides anything — that's next.
- Leverage is locked at 1x until liquidation/margin-tier math is verified
  against an official source. Don't expect leverage yet.
- Paper trading proves the mechanics work. It proves nothing about whether
  any strategy we add later actually has an edge — that's what the
  backtester in Step 3 exists to test honestly, including the likely outcome
  that some (maybe most) strategies don't pass.
