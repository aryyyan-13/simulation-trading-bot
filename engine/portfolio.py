"""
Paper-money portfolio. Tracks a fake USDT balance and a full ledger of every
trade, so nothing about performance can be quietly forgotten or smoothed over.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

import config
from engine.execution import CloseResult, Position, Side, compute_fee


@dataclass
class LedgerEntry:
    timestamp: float
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float | None
    entry_fee: float
    exit_fee: float
    funding_total: float
    net_pnl: float | None
    is_open: bool
    notes: list[str] = field(default_factory=list)


class PaperPortfolio:
    def __init__(self, starting_balance: float = config.STARTING_FAKE_BALANCE_USDT):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.positions: dict[str, Position] = {}
        self.ledger: list[LedgerEntry] = []

    # ---- opening -------------------------------------------------------
    def open_position(
        self, symbol: str, side: Side, qty: float, fill_price: float, is_maker: bool
    ) -> Position:
        if symbol in self.positions and self.positions[symbol].is_open:
            raise ValueError(f"Already have an open position in {symbol}; close it first.")

        notional = qty * fill_price
        fee = compute_fee(notional, is_maker)

        if fee > self.balance:
            raise ValueError(
                f"Cannot open: fee {fee:.4f} exceeds fake balance {self.balance:.4f}. "
                f"We do not let the paper account go negative on a fee."
            )

        self.balance -= fee
        pos = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=fill_price,
            entry_fee_paid=fee,
        )
        self.positions[symbol] = pos
        self.ledger.append(
            LedgerEntry(
                timestamp=time.time(),
                symbol=symbol,
                side=side.value,
                qty=qty,
                entry_price=fill_price,
                exit_price=None,
                entry_fee=fee,
                exit_fee=0.0,
                funding_total=0.0,
                net_pnl=None,
                is_open=True,
            )
        )
        return pos

    # ---- funding ---------------------------------------------------------
    def apply_funding(self, symbol: str, funding_payment: float) -> None:
        """funding_payment follows compute_funding_payment's sign convention:
        positive = trader receives, negative = trader pays."""
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open:
            return
        pos.funding_paid_total += funding_payment
        self.balance += funding_payment

    # ---- closing ---------------------------------------------------------
    def apply_close(self, symbol: str, close_result: CloseResult) -> None:
        pos = self.positions[symbol]
        self.balance += close_result.net_pnl

        # update the most recent open ledger entry for this symbol
        for entry in reversed(self.ledger):
            if entry.symbol == symbol and entry.is_open:
                entry.exit_price = close_result.exit_price
                entry.exit_fee = close_result.exit_fee
                entry.funding_total = close_result.funding_total
                entry.net_pnl = close_result.net_pnl
                entry.is_open = pos.is_open  # False unless book left a shortfall
                entry.notes = close_result.notes
                break

    # ---- reporting -------------------------------------------------------
    def summary(self) -> dict:
        realized = [e for e in self.ledger if e.net_pnl is not None]
        wins = [e for e in realized if e.net_pnl > 0]
        losses = [e for e in realized if e.net_pnl <= 0]
        return {
            "starting_balance": self.starting_balance,
            "current_balance": self.balance,
            "total_realized_pnl": sum(e.net_pnl for e in realized),
            "trades_closed": len(realized),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(realized)) if realized else None,
        }

    def ledger_as_json(self) -> str:
        return json.dumps([asdict(e) for e in self.ledger], indent=2, default=str)
