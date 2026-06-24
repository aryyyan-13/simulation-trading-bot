"""
Backtest runner — fetches 30 days of real BTCUSDT data and runs three strategies.

Run with:
    python backtest_runner.py

What this script tells you:
- How each strategy performed against real historical prices, with real fees,
  real funding rates, and conservative estimated slippage, over the past 30 days.

What it does NOT tell you:
- What the strategy will do in the next 30 days.
- Whether a positive result reflects a genuine edge or lucky market conditions.
- Anything about performance at higher leverage (we use 1x only).

Honest context on these strategies:
SMA Crossover     — trend-following; loses badly in choppy/ranging markets.
RSI Mean-Reversion — works in ranging markets; hurts in strong trends.
Bollinger Bands   — also mean-reversion; similar caveats to RSI.

All three are among the most widely-tested strategies in quantitative finance.
None of them has a demonstrated durable edge in liquid markets after accounting
for costs. If one of them shows a good result here, the most likely explanation
is that the market happened to suit that strategy's style during this 30-day
window — not that you've found something other traders missed.
"""
from __future__ import annotations

import datetime
import time

import config
import exchange_client as ex
from backtest.backtester import BacktestMetrics, SLIPPAGE_RATE, run_backtest
from strategies.bollinger import BollingerBandsStrategy
from strategies.rsi import RSIStrategy
from strategies.sma import SMACrossoverStrategy

SYMBOL = config.DEFAULT_SYMBOL
INTERVAL = "1h"
LOOKBACK_DAYS = 30
MAX_KLINES_PER_REQUEST = 1500   # Binance hard limit for /fapi/v1/klines


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_candles(symbol: str, interval: str, days: int) -> list[dict]:
    """Fetch all 1h candles for the past `days` days, chunking if needed.

    The Binance klines limit is 1500 per request. 30 days × 24 = 720 candles,
    which fits in one call. The chunking logic is here for correctness if the
    interval or lookback ever changes.

    Source verified: SOURCES.md §2a — GET /fapi/v1/klines, max limit 1500.
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000

    all_candles: list[dict] = []
    cursor = start_ms

    while cursor < end_ms:
        chunk = ex.get_klines(
            symbol=symbol, interval=interval,
            start_ms=cursor, end_ms=end_ms,
            limit=MAX_KLINES_PER_REQUEST,
        )
        if not chunk:
            break
        all_candles.extend(chunk)
        cursor = chunk[-1]["close_time"] + 1   # next chunk starts after last close
        if len(chunk) < MAX_KLINES_PER_REQUEST:
            break  # got everything

    # Deduplicate (open_time is the kline unique key per Binance docs)
    seen: set[int] = set()
    unique: list[dict] = []
    for c in all_candles:
        if c["open_time"] not in seen:
            seen.add(c["open_time"])
            unique.append(c)
    return sorted(unique, key=lambda c: c["open_time"])


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch all historical funding rate settlements in [start_ms, end_ms].

    Source verified: SOURCES.md §2a — GET /fapi/v1/fundingRate, max limit 1000.
    30 days × 3 settlements/day = 90 events → fits in one call.
    """
    all_events: list = []
    cursor = start_ms

    while cursor < end_ms:
        chunk = ex.get_funding_rate_history(
            symbol=symbol, start_ms=cursor, end_ms=end_ms, limit=1000
        )
        if not chunk:
            break
        all_events.extend(chunk)
        cursor = chunk[-1].funding_time_ms + 1
        if len(chunk) < 1000:
            break

    seen: set[int] = set()
    unique: list = []
    for fe in all_events:
        if fe.funding_time_ms not in seen:
            seen.add(fe.funding_time_ms)
            unique.append(fe)
    return sorted(unique, key=lambda f: f.funding_time_ms)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _ts(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def print_metrics(m: BacktestMetrics) -> None:
    win_rate_str = f"{m.win_rate:.1%}" if m.win_rate is not None else "N/A"
    sharpe_str   = f"{m.sharpe_ratio:.3f}" if m.sharpe_ratio is not None else "N/A"
    print(f"\n{'=' * 62}")
    print(f"  Strategy : {m.strategy_name}")
    print(f"  Market   : {m.symbol}  |  Interval : {m.interval}")
    print(f"  Period   : {m.candles_tested} candles")
    print(f"{'─' * 62}")
    print(f"  Trades closed : {m.trades}  (W {m.wins} / L {m.losses})")
    print(f"  Win rate      : {win_rate_str}")
    print(f"  Start balance : ${m.starting_balance:>10,.2f}")
    print(f"  End balance   : ${m.ending_balance:>10,.2f}")
    print(f"  Total return  : {m.total_return_pct:>+8.2f}%  ← after all costs")
    print(f"  Max drawdown  : {m.max_drawdown_pct:>8.2f}%")
    print(f"  Sharpe (ann.) : {sharpe_str:>8}")
    print(f"{'─' * 62}")
    # Wrap the verdict at 60 chars
    words = m.note.split()
    line, lines = [], []
    for w in words:
        if sum(len(x) + 1 for x in line) + len(w) > 58:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    print("  VERDICT:")
    for l in lines:
        print(f"    {l}")
    print(f"{'=' * 62}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\nFetching {LOOKBACK_DAYS}d of {INTERVAL} {SYMBOL} candles from Binance...")
    candles = fetch_candles(SYMBOL, INTERVAL, LOOKBACK_DAYS)
    if len(candles) < 50:
        print(f"ERROR: Only {len(candles)} candles returned — something went wrong. Aborting.")
        raise SystemExit(1)

    print(
        f"  Got {len(candles)} candles  "
        f"({_ts(candles[0]['open_time'])} → {_ts(candles[-1]['close_time'])})"
    )

    start_ms = candles[0]["open_time"]
    end_ms   = candles[-1]["close_time"]

    print("Fetching funding rate history...")
    funding = fetch_funding(SYMBOL, start_ms, end_ms)
    expected = LOOKBACK_DAYS * 3  # 3 settlements per day
    print(
        f"  Got {len(funding)} funding events  "
        f"(expected ~{expected} for {LOOKBACK_DAYS}d)"
    )

    # Print cost assumptions before results so the reader can evaluate them
    print(f"\n{'─' * 62}")
    print(f"  COST ASSUMPTIONS (all verified — see SOURCES.md)")
    print(f"    Taker fee (entry + exit) : {config.TAKER_FEE_RATE * 100:.3f}% each side")
    print(f"    Slippage (estimated)     : {SLIPPAGE_RATE * 100:.2f}% each side  ← approximation")
    print(f"    Funding                  : real historical rates from /fapi/v1/fundingRate")
    print(f"    Leverage                 : 1× (no liquidation risk)")
    print(f"    Position size            : 95% of current balance per trade")
    print(f"    Starting balance         : ${config.STARTING_FAKE_BALANCE_USDT:,.0f} fake USDT")
    print(f"{'─' * 62}")

    strategies = [
        SMACrossoverStrategy(fast=9, slow=21),
        RSIStrategy(period=14, oversold=30, overbought=70),
        BollingerBandsStrategy(period=20, num_std=2.0),
    ]

    results: list[BacktestMetrics] = []
    for strat in strategies:
        name = strat.__class__.__name__
        print(f"\nRunning {name}...", end="", flush=True)
        m = run_backtest(strat, candles, funding, SYMBOL, INTERVAL)
        results.append(m)
        print(" done.")
        print_metrics(m)

    # Summary table
    print(f"\n\n{'=' * 62}")
    print(f"  COMPARISON — {LOOKBACK_DAYS}-day backtest, {SYMBOL} {INTERVAL}")
    print(f"{'=' * 62}")
    print(f"  {'Strategy':<28} {'Return':>8}  {'Trades':>6}  {'Sharpe':>7}")
    print(f"  {'─' * 55}")
    for m in results:
        s = f"{m.sharpe_ratio:.3f}" if m.sharpe_ratio is not None else "   N/A"
        print(f"  {m.strategy_name:<28} {m.total_return_pct:>+7.2f}%  {m.trades:>6}  {s:>7}")

    print(f"\n  OVERALL HONESTY NOTE")
    print(f"  ────────────────────")
    print(f"  These three are among the most widely-known strategies ever written.")
    print(f"  If any shows a strong positive result, ask first: was this market")
    print(f"  trending or ranging during this window? A trend-follower looks great")
    print(f"  in a trend, awful afterward. A mean-reverter is the opposite.")
    print(f"  To claim real edge you need hundreds of statistically independent")
    print(f"  trades across many different market regimes — not 30 days of crypto.")
    print(f"{'=' * 62}\n")


if __name__ == "__main__":
    main()
