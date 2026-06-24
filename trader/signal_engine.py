"""
Multi-timeframe signal engine.

Computes SMA crossover signals for three timeframes from real closed-candle data.

SMA periods (chosen to match common practitioner usage and verified against the
backtester results from this project — SMA crossover was the only strategy that
cleared its costs in the 30-day test):

  1h : fast=9,  slow=21  → execution frame;  fires the actual trade
  1d : fast=7,  slow=30  → direction filter;  must agree with 1h
  1w : fast=4,  slow=12  → macro context;     displayed, does NOT gate trades
                            (too slow to react meaningfully to 1h entries)

Decision logic (honest, mechanical, no prediction):
  ┌───────────┬───────────┬──────────────────────────────────────────┐
  │  1h SMA   │  1d SMA   │  Trade signal                            │
  ├───────────┼───────────┼──────────────────────────────────────────┤
  │  LONG     │  LONG     │  LONG  (both timeframes agree: uptrend)  │
  │  SHORT    │  SHORT    │  SHORT (both agree: downtrend)           │
  │  LONG     │  SHORT    │  CLOSE / flat (conflicting — stay out)   │
  │  SHORT    │  LONG     │  CLOSE / flat (conflicting — stay out)   │
  │  HOLD*    │  any      │  HOLD  (not enough candle data yet)      │
  └───────────┴───────────┴──────────────────────────────────────────┘
  * HOLD = fewer candles than the slow SMA period

The filter will cause missed trades during regime changes when 1h and 1d
haven't aligned yet.  That is an acceptable cost for fewer whipsaw losses.
No back-adjustments are made to disguise this; the ledger will show every
outcome honestly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from strategies.sma import _sma
from strategies.rsi import _wilder_rsi

# ── SMA periods per timeframe ──────────────────────────────────────────────
_TF: dict[str, dict[str, int]] = {
    "1h": {"fast": 9,  "slow": 21},
    "1d": {"fast": 7,  "slow": 30},
    "1w": {"fast": 4,  "slow": 12},
}

# ── Result types ───────────────────────────────────────────────────────────

@dataclass
class TimeframeSignal:
    interval:    str
    fast_period: int
    slow_period: int
    fast_sma:    Optional[float]
    slow_sma:    Optional[float]
    last_close:  float
    rsi_14:      Optional[float]   # Wilder 14-period RSI — for display only
    signal:      str               # "LONG", "SHORT", or "HOLD"
    num_candles: int


@dataclass
class MultiTimeframeResult:
    hourly:       TimeframeSignal
    daily:        TimeframeSignal
    weekly:       TimeframeSignal
    trade_signal: str    # "LONG", "SHORT", "CLOSE", or "HOLD"
    trade_reason: str    # plain-English explanation, no jargon


# ── Internal helpers ───────────────────────────────────────────────────────

def _analyse_tf(closes: list[float], interval: str) -> TimeframeSignal:
    """Compute SMA + RSI signal for one timeframe from a close-price list."""
    p = _TF[interval]
    fast = _sma(closes, p["fast"])
    slow = _sma(closes, p["slow"])

    if fast is None or slow is None:
        sig = "HOLD"
    elif fast > slow:
        sig = "LONG"
    else:
        sig = "SHORT"

    rsi = _wilder_rsi(closes, 14) if len(closes) >= 15 else None
    last_close = closes[-1] if closes else float("nan")

    return TimeframeSignal(
        interval    = interval,
        fast_period = p["fast"],
        slow_period = p["slow"],
        fast_sma    = fast,
        slow_sma    = slow,
        last_close  = last_close,
        rsi_14      = rsi,
        signal      = sig,
        num_candles = len(closes),
    )


# ── Public API ─────────────────────────────────────────────────────────────

def compute_signals(
    candles_1h: list[dict],
    candles_1d: list[dict],
    candles_1w: list[dict],
) -> MultiTimeframeResult:
    """Compute multi-timeframe signals from lists of CLOSED candle dicts.

    Callers must pass only fully-closed candles (close_time < now_ms).
    This function never fetches data — it only processes what is given.
    """
    tf_1h = _analyse_tf([c["close"] for c in candles_1h], "1h")
    tf_1d = _analyse_tf([c["close"] for c in candles_1d], "1d")
    tf_1w = _analyse_tf([c["close"] for c in candles_1w], "1w")

    h, d = tf_1h.signal, tf_1d.signal

    # ── Decision table ────────────────────────────────────────────────────
    if h == "HOLD" or d == "HOLD":
        trade = "HOLD"
        reason = (
            "Not enough candle history on one or more timeframes. "
            f"(1h has {tf_1h.num_candles} candles, needs ≥{_TF['1h']['slow']}; "
            f"1d has {tf_1d.num_candles}, needs ≥{_TF['1d']['slow']})"
        )

    elif h == "LONG" and d == "LONG":
        trade = "LONG"
        reason = (
            f"Both 1h and 1d trend upward. "
            f"1h: SMA-{tf_1h.fast_period}={tf_1h.fast_sma:,.0f} > "
            f"SMA-{tf_1h.slow_period}={tf_1h.slow_sma:,.0f}.  "
            f"1d: SMA-{tf_1d.fast_period}={tf_1d.fast_sma:,.0f} > "
            f"SMA-{tf_1d.slow_period}={tf_1d.slow_sma:,.0f}."
        )

    elif h == "SHORT" and d == "SHORT":
        trade = "SHORT"
        reason = (
            f"Both 1h and 1d trend downward. "
            f"1h: SMA-{tf_1h.fast_period}={tf_1h.fast_sma:,.0f} < "
            f"SMA-{tf_1h.slow_period}={tf_1h.slow_sma:,.0f}.  "
            f"1d: SMA-{tf_1d.fast_period}={tf_1d.fast_sma:,.0f} < "
            f"SMA-{tf_1d.slow_period}={tf_1d.slow_sma:,.0f}."
        )

    else:
        trade = "CLOSE"
        reason = (
            f"1h says {h} but 1d says {d} — timeframes conflict. "
            "Closing any open position and staying flat until they agree. "
            "This avoids trading against one of your own filters."
        )

    return MultiTimeframeResult(
        hourly       = tf_1h,
        daily        = tf_1d,
        weekly       = tf_1w,
        trade_signal = trade,
        trade_reason = reason,
    )
