"""
The honest backtester.

Design decisions — all disclosed upfront:

1. SLIPPAGE
   Historical order books are not available through Binance's public API for
   past time periods. We cannot walk real levels for past fills the way the
   live engine does. Instead we apply a fixed percentage penalty to the candle
   close price on every entry and exit (SLIPPAGE_RATE = 0.05%). This is an
   approximation — real slippage for larger orders would be higher. We call it
   out here and in SOURCES.md rather than pretending a clean close-price fill.

2. FEES
   Same verified taker rate from config.py (0.05%) as the live engine.
   Every entry and exit is charged; nothing is rounded away.

3. FUNDING
   Real historical rates from /fapi/v1/fundingRate are applied at the correct
   settlement time (every 8 hours). These are the exchange's own published
   numbers, not estimates.

4. POSITION SIZING
   Each trade uses POSITION_FRACTION (95%) of the current balance. This
   intentionally leaves a small cash buffer so a fee on a big position never
   fails to open due to rounding. It also means position size compounds or
   shrinks with the balance over time.

5. LOOK-AHEAD BIAS PREVENTION
   The strategy only sees candles up to and including the current bar. The
   trade is executed at the close of that same bar, which is a slight
   assumption (in practice you'd execute after the bar closes, getting the
   next bar's open price). Using close price is a tiny optimistic bias; it
   is partially offset by the slippage penalty.

6. OPEN POSITION AT END
   Any position still open at the last candle is force-closed at that candle's
   close price (with slippage). This makes the total return comparable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import config
from engine.execution import Side, close_position, compute_funding_payment
from engine.portfolio import PaperPortfolio
from strategies.base import BaseStrategy, Signal

# One-way slippage: 0.05% per entry and per exit.
# This is an approximation for historical fills (see module docstring).
SLIPPAGE_RATE = 0.0005

# Fraction of balance committed to each new position.
POSITION_FRACTION = 0.95


# ---------------------------------------------------------------------------
# Slippage helpers
# ---------------------------------------------------------------------------

def _entry_price(raw: float, side: Side) -> float:
    """Apply slippage to an entry fill price.

    LONG entry  = market buy  → you pay the ask → slightly higher price.
    SHORT entry = market sell → you receive the bid → slightly lower price.
    """
    if side == Side.LONG:
        return raw * (1.0 + SLIPPAGE_RATE)
    return raw * (1.0 - SLIPPAGE_RATE)


def _exit_book(raw: float, side: Side) -> list[tuple[float, float]]:
    """Build a synthetic single-level order book for the exit fill.

    The close_position() engine function walks a real book; we pass it a
    single level at the slipped price with effectively unlimited depth, so
    no partial-fill edge cases can arise in a backtest where we are
    testing signals, not book depth.

    LONG exit  = market sell → you receive the bid → slightly lower price.
    SHORT exit = market buy  → you pay the ask    → slightly higher price.
    """
    if side == Side.LONG:
        exit_px = raw * (1.0 - SLIPPAGE_RATE)
    else:
        exit_px = raw * (1.0 + SLIPPAGE_RATE)
    return [(exit_px, 1_000_000.0)]   # 1M units — enough for any position


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class BacktestMetrics:
    strategy_name: str
    symbol: str
    interval: str
    candles_tested: int
    trades: int
    wins: int
    losses: int
    win_rate: Optional[float]      # None if zero trades
    starting_balance: float
    ending_balance: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float]  # annualized; None if std-dev is zero or <2 candles
    note: str                      # honest plain-language verdict


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def run_backtest(
    strategy: BaseStrategy,
    candles: list[dict],
    funding_events: list,  # list[exchange_client.HistoricalFundingRate], sorted asc
    symbol: str = config.DEFAULT_SYMBOL,
    interval: str = "1h",
) -> BacktestMetrics:
    """Run a full backtest and return metrics.

    Args:
        strategy:       Any BaseStrategy instance.
        candles:        list[dict] from get_klines(), oldest first.
        funding_events: list[HistoricalFundingRate], sorted by funding_time_ms
                        ascending.  The backtester consumes these in order and
                        applies each settlement to the open position at that time.
        symbol:         Symbol string (e.g. 'BTCUSDT') — only used for labels
                        and portfolio keying.
        interval:       Candle interval string — used only for annualising Sharpe.
    """
    portfolio = PaperPortfolio()
    funding_idx = 0          # cursor into funding_events
    balance_history: list[float] = []

    for i, candle in enumerate(candles):
        close_px = candle["close"]
        candle_close_ms = candle["close_time"]

        # --- 1. Apply all funding settlements that occurred inside this candle ---
        while funding_idx < len(funding_events):
            fe = funding_events[funding_idx]
            if fe.funding_time_ms > candle_close_ms:
                break
            pos = portfolio.positions.get(symbol)
            if pos and pos.is_open:
                # Notional uses current close price, not entry price, because
                # Binance uses mark price at settlement (we use close as proxy
                # for the historical mark since we don't have per-hour marks).
                notional = pos.qty * close_px
                payment = compute_funding_payment(notional, fe.funding_rate, pos.side)
                portfolio.apply_funding(symbol, payment)
            funding_idx += 1

        # --- 2. Ask the strategy what to do ---
        signal = strategy.generate_signal(candles[: i + 1])

        pos = portfolio.positions.get(symbol)
        open_pos = pos if (pos and pos.is_open) else None

        # --- 3. Act on the signal ---
        if signal == Signal.LONG:
            # Close a SHORT that is in the way first
            if open_pos and open_pos.side == Side.SHORT:
                cr = close_position(open_pos, _exit_book(close_px, Side.SHORT))
                portfolio.apply_close(symbol, cr)
                open_pos = None
            # Open LONG if flat
            if open_pos is None:
                ep = _entry_price(close_px, Side.LONG)
                qty = (portfolio.balance * POSITION_FRACTION) / ep
                if qty > 0:
                    try:
                        portfolio.open_position(symbol, Side.LONG, qty, ep, is_maker=False)
                    except ValueError:
                        pass  # balance too small for the fee — stay flat

        elif signal == Signal.SHORT:
            # Close a LONG that is in the way first
            if open_pos and open_pos.side == Side.LONG:
                cr = close_position(open_pos, _exit_book(close_px, Side.LONG))
                portfolio.apply_close(symbol, cr)
                open_pos = None
            # Open SHORT if flat
            if open_pos is None:
                ep = _entry_price(close_px, Side.SHORT)
                qty = (portfolio.balance * POSITION_FRACTION) / ep
                if qty > 0:
                    try:
                        portfolio.open_position(symbol, Side.SHORT, qty, ep, is_maker=False)
                    except ValueError:
                        pass

        elif signal == Signal.CLOSE:
            if open_pos:
                cr = close_position(open_pos, _exit_book(close_px, open_pos.side))
                portfolio.apply_close(symbol, cr)

        # HOLD → do nothing

        # --- 4. Record balance for Sharpe / drawdown ---
        balance_history.append(portfolio.balance)

    # --- 5. Force-close any open position at end of history ---
    final_pos = portfolio.positions.get(symbol)
    if final_pos and final_pos.is_open:
        last_px = candles[-1]["close"]
        cr = close_position(final_pos, _exit_book(last_px, final_pos.side))
        portfolio.apply_close(symbol, cr)
        if balance_history:
            balance_history[-1] = portfolio.balance

    # --- 6. Compute metrics ---
    summary = portfolio.summary()
    ending = portfolio.balance
    starting = portfolio.starting_balance
    total_return_pct = (ending - starting) / starting * 100.0

    # Max drawdown (peak-to-trough on the balance curve)
    peak = starting
    max_dd = 0.0
    for b in balance_history:
        peak = max(peak, b)
        dd = (peak - b) / peak * 100.0
        max_dd = max(max_dd, dd)

    # Annualised Sharpe (per-candle balance returns, risk-free = 0)
    sharpe: Optional[float] = None
    if len(balance_history) > 2:
        rets = [
            (balance_history[k] - balance_history[k - 1]) / balance_history[k - 1]
            for k in range(1, len(balance_history))
        ]
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
        std_r = math.sqrt(var_r)
        if std_r > 1e-14:
            # Annualisation factors for common intervals
            ppy = {"1m": 525_600, "5m": 105_120, "15m": 35_040,
                   "30m": 17_520, "1h": 8_760, "4h": 2_190, "1d": 365}
            ann_factor = math.sqrt(ppy.get(interval, 8_760))
            sharpe = (mean_r / std_r) * ann_factor

    note = _verdict(
        total_return_pct, max_dd, sharpe,
        summary["trades_closed"], strategy.__class__.__name__
    )

    return BacktestMetrics(
        strategy_name=strategy.__class__.__name__,
        symbol=symbol,
        interval=interval,
        candles_tested=len(candles),
        trades=summary["trades_closed"],
        wins=summary["wins"],
        losses=summary["losses"],
        win_rate=summary["win_rate"],
        starting_balance=starting,
        ending_balance=ending,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe,
        note=note,
    )


# ---------------------------------------------------------------------------
# Honest interpretation
# ---------------------------------------------------------------------------

def _verdict(
    return_pct: float,
    max_dd_pct: float,
    sharpe: Optional[float],
    trades: int,
    strategy_name: str,
) -> str:
    """Plain-language, unvarnished verdict on what the numbers mean."""
    parts: list[str] = []

    if trades < 10:
        return (
            f"Only {trades} closed trade(s) — far too few for any statistical conclusion. "
            "Whether the result is positive or negative is basically random at this sample size. "
            "Do not draw any conclusions from this backtest."
        )

    if return_pct > 0:
        parts.append(
            f"Positive return ({return_pct:+.2f}%) after real fees ({config.TAKER_FEE_RATE*100:.3f}% "
            f"taker, both sides), estimated slippage ({SLIPPAGE_RATE*100:.2f}% per side), "
            "and real historical funding."
        )
    else:
        parts.append(
            f"Negative return ({return_pct:+.2f}%) — the strategy does not clear its costs "
            "over this period. Fees and slippage alone would have required a raw edge to overcome."
        )

    if sharpe is not None:
        if sharpe < 0:
            parts.append(f"Sharpe {sharpe:.2f}: negative — the strategy lost money on a risk-adjusted basis.")
        elif sharpe < 0.5:
            parts.append(
                f"Sharpe {sharpe:.2f}: low. Returns are too small or too variable to be "
                "worth the risk."
            )
        elif sharpe < 1.0:
            parts.append(
                f"Sharpe {sharpe:.2f}: modest. Not strong evidence of real edge — "
                "could easily disappear out-of-sample."
            )
        else:
            parts.append(
                f"Sharpe {sharpe:.2f}: looks good, but this is one short window of crypto data. "
                "High autocorrelation means a single trending period can produce inflated Sharpe "
                "that vanishes when the market regime changes."
            )

    if max_dd_pct > 20:
        parts.append(
            f"Max drawdown {max_dd_pct:.1f}%: severe. You would have seen your paper account "
            "fall by more than a fifth at some point — uncomfortable even with fake money."
        )
    elif max_dd_pct > 10:
        parts.append(f"Max drawdown {max_dd_pct:.1f}%: significant. Worth noting.")

    parts.append(
        f"30-day backtests of simple, well-known strategies have low statistical power "
        "in crypto. Any result — positive or negative — says more about the specific market "
        "conditions in this window than about the strategy's long-run edge."
    )

    return " ".join(parts)
