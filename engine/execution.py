"""
The honest execution engine.

Three rules drive every function in this file:
1. A "fill" is only ever computed by walking real order-book levels. If the
   book doesn't have enough depth to fill an order, we say so — we never
   silently fill the remainder at the last touched price.
2. Fees and funding use only the verified rates in config.py (see
   SOURCES.md). No other number is allowed to sneak in.
3. Closing a position always nets out entry fee + exit fee + all funding
   paid/received while the position was open — a "loss" can never be shown
   smaller than it really was.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import config


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class FillResult:
    avg_price: float
    filled_qty: float
    requested_qty: float
    levels_used: int

    @property
    def fully_filled(self) -> bool:
        return self.filled_qty >= self.requested_qty - 1e-12

    @property
    def shortfall(self) -> float:
        return max(0.0, self.requested_qty - self.filled_qty)


def walk_book(levels: list[tuple[float, float]], qty_needed: float) -> FillResult:
    """Simulate consuming real order-book levels for a market (taker) order.

    `levels` must already be sorted best-price-first (asks ascending for a
    buy, bids descending for a sell) — the caller decides which side of the
    book to walk.

    This is what makes slippage real instead of guessed: a big order eats
    through multiple price levels and the average fill price reflects that.
    """
    if qty_needed <= 0:
        raise ValueError("qty_needed must be positive")

    remaining = qty_needed
    cost = 0.0
    filled = 0.0
    levels_used = 0

    for price, qty_at_level in levels:
        if remaining <= 1e-12:
            break
        take = min(remaining, qty_at_level)
        cost += take * price
        filled += take
        remaining -= take
        levels_used += 1

    avg_price = (cost / filled) if filled > 0 else float("nan")
    return FillResult(
        avg_price=avg_price,
        filled_qty=filled,
        requested_qty=qty_needed,
        levels_used=levels_used,
    )


def taker_buy_fill(asks: list[tuple[float, float]], qty: float) -> FillResult:
    """Market buy: walk the ASK side, lowest price first."""
    return walk_book(asks, qty)


def taker_sell_fill(bids: list[tuple[float, float]], qty: float) -> FillResult:
    """Market sell: walk the BID side, highest price first."""
    return walk_book(bids, qty)


def compute_fee(notional: float, is_maker: bool) -> float:
    """Fee in quote currency (USDT), using the verified Regular User rates."""
    rate = config.MAKER_FEE_RATE if is_maker else config.TAKER_FEE_RATE
    return abs(notional) * rate


def compute_funding_payment(position_notional: float, funding_rate: float, side: Side) -> float:
    """Return the cash flow TO the trader (negative = trader pays out).

    Formula and sign convention verified in SOURCES.md #4:
    Funding Fee = Position Notional Value * Funding Rate.
    Positive funding_rate -> longs pay shorts. Negative -> shorts pay longs.
    """
    raw = abs(position_notional) * funding_rate
    if side == Side.LONG:
        return -raw  # longs pay when raw > 0, receive when raw < 0
    else:
        return raw  # shorts receive when raw > 0, pay when raw < 0


@dataclass
class Position:
    symbol: str
    side: Side
    qty: float
    entry_price: float
    entry_fee_paid: float = 0.0
    funding_paid_total: float = 0.0  # running sum of compute_funding_payment results
    is_open: bool = True

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price

    def unrealized_pnl(self, mark_price: float) -> float:
        if self.side == Side.LONG:
            return (mark_price - self.entry_price) * self.qty
        else:
            return (self.entry_price - mark_price) * self.qty


@dataclass
class CloseResult:
    exit_price: float
    gross_pnl: float
    exit_fee: float
    entry_fee: float
    funding_total: float
    net_pnl: float
    shortfall: float  # >0 means the book couldn't fully fill the close order
    notes: list[str] = field(default_factory=list)


def close_position(
    position: Position,
    book_levels_for_exit: list[tuple[float, float]],
    is_maker_exit: bool = False,
    limit_price: float | None = None,
) -> CloseResult:
    """Close a position honestly: real exit fill, real fee, all funding netted in.

    If `is_maker_exit` is True, `limit_price` is used as the fill price
    (a resting limit order assumed matched) instead of walking the book.
    Otherwise this is a taker close and the book is walked for real slippage.
    """
    notes: list[str] = []

    if is_maker_exit:
        if limit_price is None:
            raise ValueError("limit_price required for a maker exit")
        exit_price = limit_price
        filled_qty = position.qty
        shortfall = 0.0
    else:
        fill = walk_book(book_levels_for_exit, position.qty)
        exit_price = fill.avg_price
        filled_qty = fill.filled_qty
        shortfall = fill.shortfall
        if shortfall > 1e-9:
            notes.append(
                f"Order book depth was insufficient: only {filled_qty}/{position.qty} "
                f"could be closed at a real price. This position is left PARTIALLY OPEN "
                f"— it is not pretended to be fully closed."
            )

    if position.side == Side.LONG:
        gross_pnl = (exit_price - position.entry_price) * filled_qty
    else:
        gross_pnl = (position.entry_price - exit_price) * filled_qty

    exit_notional = exit_price * filled_qty
    exit_fee = compute_fee(exit_notional, is_maker=is_maker_exit)

    net_pnl = gross_pnl - position.entry_fee_paid - exit_fee + position.funding_paid_total

    if net_pnl < 0:
        notes.append(
            f"Closed at a LOSS of {abs(net_pnl):.4f} (after fees and funding). "
            f"This number already includes entry fee {position.entry_fee_paid:.4f}, "
            f"exit fee {exit_fee:.4f}, and net funding {position.funding_paid_total:.4f} "
            f"— nothing here is hidden or rounded away."
        )

    if shortfall <= 1e-9:
        position.qty = 0.0
        position.is_open = False
    else:
        position.qty = shortfall  # remainder still open, honestly

    return CloseResult(
        exit_price=exit_price,
        gross_pnl=gross_pnl,
        exit_fee=exit_fee,
        entry_fee=position.entry_fee_paid,
        funding_total=position.funding_paid_total,
        net_pnl=net_pnl,
        shortfall=shortfall,
        notes=notes,
    )
