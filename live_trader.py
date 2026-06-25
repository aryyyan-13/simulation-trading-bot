#!/usr/bin/env python3
"""
Multi-Symbol Live Paper Trader (v4).

Monitors crypto (Binance USDS-M Futures) AND Indian stocks (NSE via yfinance)
across three timeframes (1h, 1d, 1w) simultaneously.  Executes paper trades
when the 1h and 1d SMA signals agree.  Automatically cuts losing positions if
they breach the configured stop-loss threshold.

New in v4:
  • Indian stock support — NSE symbols ending in ".NS" (e.g. RELIANCE.NS)
  • News-driven watchlist expansion — RSS headlines from Economic Times and
    Moneycontrol flag companies; SMA signal still gates actual entry
  • Indian market hours guard — skips stock analysis outside 9:15–15:30 IST
  • Zerodha-style delivery fees for stocks (STT, Stamp Duty, GST, DP charges)
  • No funding settlements for stocks (stocks have no perpetual funding)
  • All accounting in USD; INR prices converted at config.USD_INR_RATE

Monitors all symbols in config.WATCHLIST across three timeframes (1h, 1d, 1w)
with real Binance data and executes paper trades when the 1h and 1d SMA
signals agree.  Automatically cuts losing positions if they breach the
configured stop-loss threshold.  All fills use real fees (verified), estimated
slippage (disclosed), and real funding settlements.  Every closed trade is
written to data/trade_datasheet.csv for future reference.

Usage
─────
  python live_trader.py                        # one cycle, print status, exit
  python live_trader.py --loop                 # continuous loop (default 60 s)
  python live_trader.py --loop --interval 30   # 30 s polling
  python live_trader.py --reset                # wipe state, start fresh
  python live_trader.py --dry-run              # show signals only, no trades
  python live_trader.py --status               # show current state, no fetch
  python live_trader.py --symbols BTCUSDT,ETHUSDT  # override watchlist for this run

Design rules (inherited from the project rule):
  • No fake price, no fake fill, no fake profit.
  • Any number used in accounting must trace to a verified source.
  • Slippage is an APPROXIMATION (0.05 % per side) — labelled as such.
  • If the exchange can't be reached for a symbol, that symbol is skipped;
    others continue normally.
  • Stop-loss closes are clearly flagged in logs, state, and the CSV datasheet.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import sys
import time
from typing import Optional

import config
import exchange_client as ex
import stock_client as sc
from engine.execution import Side, close_position, compute_funding_payment
from engine.stock_fees import compute_stock_buy_fee, compute_stock_sell_fee
from news_scanner import get_effective_stock_watchlist
from stock_client import StockClientError, is_nse_market_open
from trader.signal_engine import MultiTimeframeResult, compute_signals
from trader.state import ClosedTradeRecord, TraderState

import socket
socket.setdefaulttimeout(15.0)  # prevent indefinite hangs in blocking network requests

# ── Constants ──────────────────────────────────────────────────────────────
SLIPPAGE_RATE = 0.0005   # 0.05 % per side — estimate, not a verified number
POSITION_FRAC = 0.95     # fraction of allocated budget committed per trade
CANDLE_COUNTS = {"1h": 100, "1d": 50, "1w": 24}

# ── Asset class helpers ────────────────────────────────────────────────────

def _is_stock(symbol: str) -> bool:
    """Return True if the symbol is an Indian NSE stock (ends with '.NS')."""
    return symbol.upper().endswith(".NS")


def _inr_to_usd(amount_inr: float) -> float:
    """Convert INR to USD using the configured exchange rate."""
    return amount_inr / config.USD_INR_RATE


def _usd_to_inr(amount_usd: float) -> float:
    """Convert USD to INR using the configured exchange rate."""
    return amount_usd * config.USD_INR_RATE



# ── Slippage helpers ───────────────────────────────────────────────────────

def _entry_px(raw: float, side: Side) -> float:
    """Apply SLIPPAGE_RATE to an entry fill price.
    LONG entry  = market buy  → you pay the ask → slightly higher.
    SHORT entry = market sell → you receive the bid → slightly lower.
    """
    return raw * (1.0 + SLIPPAGE_RATE) if side == Side.LONG else raw * (1.0 - SLIPPAGE_RATE)


def _exit_book(raw: float, side: Side) -> list[tuple[float, float]]:
    """Synthetic single-level order book for the exit fill.
    LONG exit  = sell at bid → slightly lower.
    SHORT exit = buy at ask  → slightly higher.
    1 000 000 qty ensures close_position() never reports a partial fill.
    """
    px = raw * (1.0 - SLIPPAGE_RATE) if side == Side.LONG else raw * (1.0 + SLIPPAGE_RATE)
    return [(px, 1_000_000.0)]


# ── Capital allocation ─────────────────────────────────────────────────────

def _allocated_budget(state: TraderState, watchlist: list[str]) -> float:
    """Return the USDT budget allocated to each symbol.

    Formula: total_equity / number_of_symbols_in_watchlist.
    total_equity = realized cash balance (does NOT include unrealized P&L,
    because mark prices for all positions are not fetched here — that would
    require an extra API call per position per cycle and is not worth the
    complexity for paper trading at 1x leverage).

    Keeping it simple is both honest and safe: we may slightly undersize
    entries when positions are in profit, which is conservative.
    """
    n = max(1, len(watchlist))
    return state.balance / n


# ── Stop-loss check ────────────────────────────────────────────────────────

def _is_stop_loss_triggered(op, mark_price: float) -> bool:
    """Return True if the open position has lost more than STOP_LOSS_PCT.

    If config.STOP_LOSS_PCT is None, always returns False (disabled).
    """
    if config.STOP_LOSS_PCT is None:
        return False
    if op.side == "LONG":
        loss_pct = (op.entry_price - mark_price) / op.entry_price
    else:
        loss_pct = (mark_price - op.entry_price) / op.entry_price
    return loss_pct >= config.STOP_LOSS_PCT


# ── Close a position (shared logic) ───────────────────────────────────────

def _close_position_for_symbol(
    state:      TraderState,
    symbol:     str,
    mark_price: float,
    reason:     str,            # "signal" | "stop_loss"
    dry_run:    bool = False,
) -> str:
    """Close the open position for `symbol` at mark_price.

    Returns a human-readable description of what happened.
    Mutates state in-place.
    """
    op = state.open_positions.get(symbol)
    if op is None:
        return "no open position to close"

    portfolio = state.to_portfolio()
    pos       = portfolio.positions.get(symbol)
    if pos is None or not pos.is_open:
        return "no open position to close"

    open_side = Side[op.side]
    book      = _exit_book(mark_price, open_side)
    cr        = close_position(pos, book)

    if not dry_run:
        portfolio.apply_close(symbol, cr)
        now_ms  = int(time.time() * 1000)
        pnl_str = f"+${cr.net_pnl:.2f}" if cr.net_pnl >= 0 else f"-${abs(cr.net_pnl):.2f}"

        state.closed_trades.append(ClosedTradeRecord(
            symbol        = symbol,
            side          = op.side,
            qty           = op.qty,
            entry_price   = op.entry_price,
            exit_price    = cr.exit_price,
            entry_fee     = cr.entry_fee,
            exit_fee      = cr.exit_fee,
            funding_total = cr.funding_total,
            net_pnl       = cr.net_pnl,
            entry_time_ms = op.entry_time_ms,
            exit_time_ms  = now_ms,
            exit_reason   = reason,
            asset_class   = getattr(op, "asset_class", "crypto"),
        ))
        state.sync_from_portfolio(portfolio, symbol)
        tag = "[STOP LOSS] " if reason == "stop_loss" else ""
        state.log(
            f"{tag}CLOSED {op.side} {symbol} {op.qty:.6f} @ ${cr.exit_price:,.2f}"
            f"  net {pnl_str}"
        )
        return (
            f"{tag}CLOSED {op.side} @ ${cr.exit_price:,.2f}  net PnL {pnl_str}"
        )
    else:
        simulated_px = _exit_book(mark_price, open_side)[0][0]
        return f"[DRY-RUN] would CLOSE {op.side} @ ~${simulated_px:,.2f}"


# ── Open a position (shared logic) ────────────────────────────────────────

def _open_position_for_symbol(
    state:      TraderState,
    symbol:     str,
    new_side:   Side,
    mark_price: float,
    budget:     float,
    dry_run:    bool = False,
) -> str:
    """Open a new position for `symbol` using at most `budget` USDT.

    Returns a human-readable description of what happened.
    Mutates state in-place.
    """
    portfolio = state.to_portfolio()
    pos_now   = portfolio.positions.get(symbol)
    if pos_now and pos_now.is_open and pos_now.side == new_side:
        return f"already {new_side.value} — holding"

    ep  = _entry_px(mark_price, new_side)
    qty = (budget * POSITION_FRAC) / ep

    if qty <= 0:
        return "SKIPPED: budget too small to open a position"

    if not dry_run:
        try:
            portfolio.open_position(symbol, new_side, qty, ep, is_maker=False)
            now_ms = int(time.time() * 1000)
            state.sync_from_portfolio(portfolio, symbol, new_entry_ms=now_ms)
            state.log(
                f"OPENED {new_side.value} {symbol} {qty:.6f} @ ${ep:,.2f} "
                f"(slippage ±{SLIPPAGE_RATE*100:.2f}%)"
            )
            return f"OPENED {new_side.value} {qty:.6f} @ ${ep:,.2f}"
        except ValueError as exc:
            state.log(f"OPEN FAILED {symbol}: {exc}")
            return f"OPEN FAILED: {exc}"
    else:
        return f"[DRY-RUN] would OPEN {new_side.value} {qty:.6f} @ ~${ep:,.2f}"


# ── Execute trade signal for one symbol ───────────────────────────────────

def _execute(
    state:      TraderState,
    symbol:     str,
    signal:     str,            # "LONG", "SHORT", "CLOSE", "HOLD"
    mark_price: float,
    budget:     float,
    dry_run:    bool = False,
) -> str:
    """Apply the signal for one symbol. Returns a summary string."""
    if signal == "HOLD":
        return "HOLD — waiting for clearer trend"

    op          = state.open_positions.get(symbol)
    open_pos_ok = op is not None
    action_log: list[str] = []

    must_close = (
        signal == "CLOSE"
        or (signal == "LONG"  and open_pos_ok and op.side == "SHORT")
        or (signal == "SHORT" and open_pos_ok and op.side == "LONG")
    )
    must_open_long  = signal == "LONG"  and (not open_pos_ok or must_close)
    must_open_short = signal == "SHORT" and (not open_pos_ok or must_close)

    if must_close and open_pos_ok:
        result = _close_position_for_symbol(
            state, symbol, mark_price, reason="signal", dry_run=dry_run
        )
        action_log.append(result)
        open_pos_ok = False

    if must_open_long or must_open_short:
        new_side = Side.LONG if must_open_long else Side.SHORT
        result   = _open_position_for_symbol(
            state, symbol, new_side, mark_price, budget, dry_run=dry_run
        )
        action_log.append(result)

    return " | ".join(action_log) if action_log else "no change"


# ── Data helpers ───────────────────────────────────────────────────────────

def _fetch_closed_candles(symbol: str, interval: str, limit: int) -> list[dict]:
    """Fetch candles for `symbol` from the right client and filter to closed bars."""
    now_ms = int(time.time() * 1000)
    if _is_stock(symbol):
        raw = sc.get_nse_klines(symbol, interval, limit=limit)
        # yfinance close_time equals open_time; all historical rows are closed
        return raw
    else:
        raw = ex.get_klines(symbol, interval, limit=limit)
        return [c for c in raw if c["close_time"] < now_ms]


def _latest_closed(candles: list[dict]) -> Optional[dict]:
    now_ms = int(time.time() * 1000)
    closed = [c for c in candles if c["close_time"] < now_ms]
    return closed[-1] if closed else None


# ── Display helpers ────────────────────────────────────────────────────────

def _sig_arrow(sig: str) -> str:
    return {"LONG": "↑ LONG", "SHORT": "↓ SHORT", "HOLD": "○ HOLD", "CLOSE": "✕ CLOSE"}.get(sig, sig)


def _dur(ms: int) -> str:
    secs = max(0, (time.time() * 1000 - ms) / 1000)
    h, r = divmod(int(secs), 3600)
    m    = r // 60
    return f"{h}h {m:02d}m"


def _funding_countdown(next_ms: int) -> str:
    secs = max(0, (next_ms - time.time() * 1000) / 1000)
    h, r = divmod(int(secs), 3600)
    m    = r // 60
    return f"{h}h {m:02d}m"


# ── Status panel ───────────────────────────────────────────────────────────

def _print_status(
    state:      TraderState,
    watchlist:  list[str],
    analyses:   dict[str, MultiTimeframeResult],
    mf_map:     dict[str, ex.MarkAndFunding],
    actions:    dict[str, str],
    new_candles: dict[str, dict[str, bool]],
) -> None:
    """Print the full multi-symbol status panel to stdout."""
    W       = 76
    now_str = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    perf    = state.performance_summary()

    def sep(char: str = "─") -> None:
        print(char * W)

    print()
    sep("═")
    print(f"  ₿  MULTI-SYMBOL PAPER TRADER              {now_str:>26}")
    sep("═")

    # ── Portfolio overview ────────────────────────────────────────────────
    net_pnl = perf["net_realized"]
    ret     = perf["total_return"]
    nr_str  = f"+${net_pnl:,.2f}" if net_pnl >= 0 else f"-${abs(net_pnl):,.2f}"
    print(f"  Balance (realized) : ${state.balance:>12,.2f}")
    print(f"  Starting balance   : ${state.starting_balance:>12,.2f}")
    print(f"  Net realized P&L   : {nr_str:>14}   ({ret:+.2f}%)")
    print(f"  Trades closed      : {perf['trades_closed']}  "
          f"(W {perf['wins']} / L {perf['losses']}  "
          f"Stop-loss exits: {perf['stop_loss_exits']})")
    next_str = _funding_countdown(state.next_funding_time_ms)
    print(f"  Next funding       : in {next_str}")
    print()

    # ── Per-symbol signals + position ────────────────────────────────────
    for sym in watchlist:
        mf = mf_map.get(sym)
        an = analyses.get(sym)
        if mf is None:
            print(f"  [{sym}]  ⚠  Could not fetch data this cycle — skipped.")
            print()
            continue

        budget = _allocated_budget(state, watchlist)
        op     = state.open_positions.get(sym)

        # Price line
        fr_sign = "+" if mf.last_funding_rate >= 0 else ""
        print(f"  [{sym}]  Mark ${mf.mark_price:,.2f}   "
              f"Funding {fr_sign}{mf.last_funding_rate:.4%}")

        if an:
            # Signal table
            print(f"  {'TF':<4} {'Fast SMA':>12} {'Slow SMA':>12} {'RSI-14':>7}  {'Signal':>12}  {'New?':>5}")
            print(f"  {'─'*4} {'─'*12} {'─'*12} {'─'*7}  {'─'*12}  {'─'*5}")
            for tf_sig, label in [
                (an.hourly, "1h"),
                (an.daily,  "1d"),
                (an.weekly, "1w"),
            ]:
                fast_s = f"${tf_sig.fast_sma:>10,.0f}" if tf_sig.fast_sma else "         N/A"
                slow_s = f"${tf_sig.slow_sma:>10,.0f}" if tf_sig.slow_sma else "         N/A"
                rsi_s  = f"{tf_sig.rsi_14:>6.1f}" if tf_sig.rsi_14 is not None else "   N/A"
                new_mk = "✓ NEW" if new_candles.get(sym, {}).get(label) else "  ─  "
                print(f"  {label:<4} {fast_s:>12} {slow_s:>12} {rsi_s:>7}  "
                      f"{_sig_arrow(tf_sig.signal):>12}  {new_mk:>5}")

            arrow = {"LONG": "▲", "SHORT": "▼", "CLOSE": "✕", "HOLD": "○"}.get(an.trade_signal, "?")
            print(f"  Signal: {arrow} {an.trade_signal}  │  {an.trade_reason[:60]}")

        # Open position for this symbol
        if op:
            unrealized = (
                (mf.mark_price - op.entry_price) * op.qty
                if op.side == "LONG"
                else (op.entry_price - mf.mark_price) * op.qty
            )
            sl_status = ""
            if config.STOP_LOSS_PCT is not None:
                if op.side == "LONG":
                    loss_pct = (op.entry_price - mf.mark_price) / op.entry_price
                else:
                    loss_pct = (mf.mark_price - op.entry_price) / op.entry_price
                pct_to_sl = config.STOP_LOSS_PCT - loss_pct
                sl_status = (f"  SL at {config.STOP_LOSS_PCT:.1%} | "
                             f"{loss_pct:.2%} loss | {pct_to_sl:.2%} to trigger")
            side_arrow = "▲" if op.side == "LONG" else "▼"
            unr_str    = f"+${unrealized:,.2f}" if unrealized >= 0 else f"-${abs(unrealized):,.2f}"
            print(f"  {side_arrow} OPEN: {op.side} {op.qty:.6f} {sym} @ "
                  f"${op.entry_price:,.2f}  Unrealized: {unr_str}  Age: {_dur(op.entry_time_ms)}")
            if sl_status:
                print(f" {sl_status}")
        else:
            print(f"  No open position  (budget: ${budget:,.2f})")

        # Action this cycle
        act = actions.get(sym, "─")
        print(f"  → This cycle: {act}")
        sep()

    # ── Recent activity log ───────────────────────────────────────────────
    recent_log = state.activity_log[-6:]
    if recent_log:
        print(f"  RECENT ACTIVITY")
        for entry in recent_log:
            print(f"  {entry}")
    print()
    print(f"  State saved → data/trader_state.json")
    print(f"  Datasheet   → data/trade_datasheet.csv  ({len(state.closed_trades)} rows)")
    sep("═")
    print()


# ── One polling cycle for one symbol ──────────────────────────────────────

def _run_symbol_cycle(
    state:    TraderState,
    symbol:   str,
    watchlist: list[str],
    dry_run:  bool,
) -> tuple[Optional[ex.MarkAndFunding], Optional[MultiTimeframeResult], dict[str, bool], str]:
    """
    Run one monitoring + execution cycle for a single symbol.

    For crypto (Binance) symbols: fetches mark price + funding, applies
    funding settlements, checks stop-loss, computes SMA signals, executes.

    For Indian stock (.NS) symbols: checks NSE market hours first. If the
    market is closed, skips this symbol entirely. If open, fetches price via
    yfinance (INR → USD conversion), no funding, applies Zerodha-model fees.

    Returns: (MarkAndFunding | None, MultiTimeframeResult | None,
               new_candle_flags, action_string)
    """
    is_stock = _is_stock(symbol)

    # ── STOCK PATH ─────────────────────────────────────────────────────────
    if is_stock:
        # 1. Market hours guard
        if not is_nse_market_open():
            return None, None, {}, "[NSE CLOSED] market hours 9:15–15:30 IST (Mon–Fri)"

        # 2. Fetch live price in INR → convert to USD
        try:
            price_inr = sc.get_nse_price(symbol)
        except StockClientError as exc:
            print(f"  [WARNING] {symbol}: could not fetch NSE price — {exc}")
            return None, None, {}, "fetch error — skipped"

        price_usd = _inr_to_usd(price_inr)

        # 3. Stop-loss check (uses USD price for consistency)
        op = state.open_positions.get(symbol)
        if op and _is_stop_loss_triggered(op, price_usd) and not dry_run:
            action = _close_position_for_symbol(
                state, symbol, price_usd, reason="stop_loss", dry_run=False
            )
            return None, None, {}, f"[STOP LOSS] {action}"

        # 4. Candles
        try:
            c1h = _fetch_closed_candles(symbol, "1h", CANDLE_COUNTS["1h"])
            c1d = _fetch_closed_candles(symbol, "1d", CANDLE_COUNTS["1d"])
            c1w = _fetch_closed_candles(symbol, "1w", CANDLE_COUNTS["1w"])
        except StockClientError as exc:
            print(f"  [WARNING] {symbol}: could not fetch NSE candles — {exc}")
            return None, None, {}, "candle fetch error — skipped"

        # Convert candle close prices from INR to USD so the signal engine
        # works with USD-normalised values
        def _convert(candles: list[dict]) -> list[dict]:
            return [{**c, "close": _inr_to_usd(c["close"])} for c in candles]

        c1h_usd = _convert(c1h)
        c1d_usd = _convert(c1d)
        c1w_usd = _convert(c1w)

        # 5. Detect new candles
        sym_times = state.get_candle_times(symbol)
        lat1h = _latest_closed(c1h)
        lat1d = _latest_closed(c1d)
        lat1w = _latest_closed(c1w)
        new1h = lat1h is not None and lat1h["open_time"] != sym_times["1h"]
        new1d = lat1d is not None and lat1d["open_time"] != sym_times["1d"]
        new1w = lat1w is not None and lat1w["open_time"] != sym_times["1w"]

        # 6. Compute signals
        analysis = compute_signals(c1h_usd, c1d_usd, c1w_usd)

        # 7. Execute on new 1h candle
        budget = _allocated_budget(state, watchlist)
        action = "monitoring — waiting for a new 1h candle to close"
        if new1h:
            action = _execute(
                state, symbol, analysis.trade_signal,
                price_usd, budget, dry_run=dry_run,
            )
            sym_times["1h"] = lat1h["open_time"]

        # 8. Update other timeframe timestamps
        if new1d and lat1d:
            sym_times["1d"] = lat1d["open_time"]
            state.log(f"[{symbol}] New 1d candle closed @ ₹{lat1d['close']:,.2f} (${_inr_to_usd(lat1d['close']):.2f})")
        if new1w and lat1w:
            sym_times["1w"] = lat1w["open_time"]
            state.log(f"[{symbol}] New 1w candle closed @ ₹{lat1w['close']:,.2f} (${_inr_to_usd(lat1w['close']):.2f})")

        # Build a MarkAndFunding-like object for display (stocks have no funding)
        # We reuse the crypto dataclass but set funding fields to zero
        mf_stock = ex.MarkAndFunding(
            symbol              = symbol,
            mark_price          = price_usd,
            index_price         = price_usd,
            last_funding_rate   = 0.0,      # stocks have no funding
            next_funding_time_ms = 0,
            fetched_at          = time.time(),
        )
        return mf_stock, analysis, {"1h": new1h, "1d": new1d, "1w": new1w}, action

    # ── CRYPTO PATH (unchanged) ─────────────────────────────────────────────
    # 1. Live mark price + funding
    try:
        mf = ex.get_mark_and_funding(symbol)
    except ex.ExchangeError as exc:
        print(f"  [WARNING] {symbol}: could not fetch price — {exc}")
        return None, None, {}, "fetch error — skipped"

    # 2. Stop-loss check (priority: runs before candle logic)
    op = state.open_positions.get(symbol)
    if op and _is_stop_loss_triggered(op, mf.mark_price) and not dry_run:
        action = _close_position_for_symbol(
            state, symbol, mf.mark_price, reason="stop_loss", dry_run=False
        )
        return mf, None, {}, f"[STOP LOSS] {action}"

    # 3. Apply funding settlement if nextFundingTime has advanced
    if (
        mf.next_funding_time_ms > state.next_funding_time_ms
        and state.next_funding_time_ms > 0
        and op is not None
    ):
        portfolio = state.to_portfolio()
        pos       = portfolio.positions.get(symbol)
        if pos and pos.is_open:
            notional = pos.qty * mf.mark_price
            payment  = compute_funding_payment(notional, mf.last_funding_rate, pos.side)
            portfolio.apply_funding(symbol, payment)
            state.sync_from_portfolio(portfolio, symbol)
            direction = "received" if payment >= 0 else "paid"
            state.log(
                f"Funding {symbol}: {direction} ${abs(payment):.4f} "
                f"(rate {mf.last_funding_rate:+.4%})"
            )

    # 4. Candles for all three timeframes
    try:
        c1h = _fetch_closed_candles(symbol, "1h", CANDLE_COUNTS["1h"])
        c1d = _fetch_closed_candles(symbol, "1d", CANDLE_COUNTS["1d"])
        c1w = _fetch_closed_candles(symbol, "1w", CANDLE_COUNTS["1w"])
    except ex.ExchangeError as exc:
        print(f"  [WARNING] {symbol}: could not fetch candles — {exc}")
        return mf, None, {}, "candle fetch error — skipped"

    # 5. Detect new closed candles
    sym_times = state.get_candle_times(symbol)
    lat1h     = _latest_closed(c1h)
    lat1d     = _latest_closed(c1d)
    lat1w     = _latest_closed(c1w)
    new1h     = lat1h is not None and lat1h["open_time"] != sym_times["1h"]
    new1d     = lat1d is not None and lat1d["open_time"] != sym_times["1d"]
    new1w     = lat1w is not None and lat1w["open_time"] != sym_times["1w"]

    # 6. Compute signals
    analysis = compute_signals(c1h, c1d, c1w)

    # 7. Execute on new 1h candle
    budget = _allocated_budget(state, watchlist)
    action = "monitoring — waiting for a new 1h candle to close"
    if new1h:
        action = _execute(
            state, symbol, analysis.trade_signal,
            mf.mark_price, budget, dry_run=dry_run,
        )
        sym_times["1h"] = lat1h["open_time"]

    # 8. Update other timeframe timestamps + log notable closes
    if new1d and lat1d:
        sym_times["1d"] = lat1d["open_time"]
        state.log(f"[{symbol}] New 1d candle closed @ ${lat1d['close']:,.2f}")
    if new1w and lat1w:
        sym_times["1w"] = lat1w["open_time"]
        state.log(f"[{symbol}] New 1w candle closed @ ${lat1w['close']:,.2f}")

    return mf, analysis, {"1h": new1h, "1d": new1d, "1w": new1w}, action



# ── Full polling cycle (all symbols) ──────────────────────────────────────

def run_cycle(
    state:    TraderState,
    watchlist: list[str],
    dry_run:  bool = False,
) -> TraderState:
    """Fetch live data for every symbol, check stop-losses, evaluate signals,
    optionally execute, print the combined status panel, and save state."""

    mf_map:     dict[str, ex.MarkAndFunding]    = {}
    analyses:   dict[str, MultiTimeframeResult] = {}
    actions:    dict[str, str]                  = {}
    new_candles: dict[str, dict[str, bool]]     = {}

    for symbol in watchlist:
        mf, an, new_flags, action = _run_symbol_cycle(
            state, symbol, watchlist, dry_run
        )
        if mf:
            mf_map[symbol]      = mf
            state.next_funding_time_ms = mf.next_funding_time_ms
        if an:
            analyses[symbol]    = an
        actions[symbol]         = action
        new_candles[symbol]     = new_flags

    _print_status(state, watchlist, analyses, mf_map, actions, new_candles)

    if not dry_run:
        state.save()

    return state


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Multi-symbol live multi-timeframe paper trader"
    )
    ap.add_argument("--loop",     action="store_true",
                    help="Run continuously until Ctrl-C")
    ap.add_argument("--interval", type=int, default=60,
                    help="Polling interval in seconds (default 60)")
    ap.add_argument("--reset",    action="store_true",
                    help="Wipe portfolio state and start fresh")
    ap.add_argument("--dry-run",  action="store_true",
                    help="Fetch data and show signals but do not execute trades")
    ap.add_argument("--status",   action="store_true",
                    help="Print current portfolio state from file only (no Binance call)")
    ap.add_argument("--symbols",  type=str, default=None,
                    help="Comma-separated symbol list, e.g. BTCUSDT,ETHUSDT "
                         "(overrides config.WATCHLIST for this run)")
    args = ap.parse_args()

    # ── Watchlist ─────────────────────────────────────────────────────────
    if args.symbols:
        watchlist = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        watchlist = config.WATCHLIST

    if not watchlist:
        print("ERROR: watchlist is empty. Set config.WATCHLIST or pass --symbols.")
        sys.exit(1)

    # ── Reset ─────────────────────────────────────────────────────────────
    if args.reset:
        state = TraderState.reset()
        print(f"Portfolio state reset.  Starting balance: ${TraderState().starting_balance:,.2f} paper money.")
        print(f"Crypto watchlist : {config.WATCHLIST}")
        print(f"Stock watchlist  : {config.STOCK_WATCHLIST}")
        state.save()
        return

    # ── Status-only (no network) ──────────────────────────────────────────
    if args.status:
        state = TraderState.load()
        perf  = state.performance_summary()
        print(f"\n── Portfolio State ──────────────────────────────────────────")
        print(f"  Balance  : ${state.balance:,.2f}")
        print(f"  Trades   : {perf['trades_closed']}  "
              f"(W {perf['wins']} / L {perf['losses']}  "
              f"Stop-loss exits: {perf['stop_loss_exits']})")
        print(f"  Net P&L  : ${perf['net_realized']:+,.2f}  ({perf['total_return']:+.2f}%)")
        if state.open_positions:
            for sym, op in state.open_positions.items():
                mkt = getattr(op, "asset_class", "crypto")
                print(f"  Open pos : {sym} [{mkt}]  {op.side} {op.qty:.6f} @ ${op.entry_price:,.2f}")
        else:
            print(f"  Open pos : None")
        print(f"  Datasheet: data/trade_datasheet.csv  ({len(state.closed_trades)} rows)")
        print(f"────────────────────────────────────────────────────────────\n")
        return

    # ── Normal run ────────────────────────────────────────────────────────
    state = TraderState.load()

    if args.dry_run:
        print(f"\n  [DRY-RUN MODE] Signals computed — no trades executed.\n")

    # Build effective watchlist: crypto + stocks (+ news candidates if enabled)
    crypto_list = config.WATCHLIST
    if config.NEWS_SCAN_ENABLED:
        stock_list = get_effective_stock_watchlist(config.STOCK_WATCHLIST)
    else:
        stock_list = list(config.STOCK_WATCHLIST)
    combined_watchlist = crypto_list + stock_list

    print(f"  Crypto     : {crypto_list}")
    print(f"  Stocks     : {stock_list}")
    print(f"  Total      : {len(combined_watchlist)} symbols")
    print(f"  Stop-loss  : {config.STOP_LOSS_PCT:.1%} per position" if config.STOP_LOSS_PCT else "  Stop-loss  : disabled")
    print(f"  Slippage   : ±{SLIPPAGE_RATE*100:.2f}% (estimate — see SOURCES.md)")
    print(f"  INR→USD    : 1 USD = ₹{config.USD_INR_RATE:.2f} (update config.USD_INR_RATE as needed)")
    print(f"  News scan  : {'every ' + str(config.NEWS_SCAN_INTERVAL_CYCLES) + ' cycles' if config.NEWS_SCAN_ENABLED else 'disabled'}")
    print()

    _cycle_count = 0

    if args.loop:
        print(f"  Starting live monitoring loop (interval: {args.interval}s).  Ctrl-C to stop.\n")
        try:
            while True:
                _cycle_count += 1
                # Refresh news candidates periodically
                if (config.NEWS_SCAN_ENABLED
                        and _cycle_count % config.NEWS_SCAN_INTERVAL_CYCLES == 0):
                    stock_list = get_effective_stock_watchlist(config.STOCK_WATCHLIST)
                    combined_watchlist = crypto_list + stock_list

                state = run_cycle(state, combined_watchlist, dry_run=args.dry_run)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n\n  Monitoring stopped by user.  State is saved.\n")
    else:
        run_cycle(state, combined_watchlist, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

