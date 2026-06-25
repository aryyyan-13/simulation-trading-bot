"""
Persistent state for the live paper trader (schema version 3).

All portfolio data is stored in data/trader_state.json so the process
can be killed and restarted without losing positions or history.

Version 3 changes vs version 2:
  - open_positions: dict[str, OpenPositionSnapshot]  (was: single open_position)
  - last_candle_time: dict[str, dict[str, int]]       (was: flat dict per interval)
  - closed_trades: list[ClosedTradeRecord]             (unchanged)
  - trade_datasheet CSV is kept in sync on every save

Nothing in this file invents prices or fills. If state is corrupt,
it raises loudly rather than silently inventing fresh numbers.
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import config
from engine.execution import Position, Side
from engine.portfolio import PaperPortfolio

# ── File locations ─────────────────────────────────────────────────────────
STATE_FILE     = Path(__file__).parent.parent / "data" / "trader_state.json"
DATASHEET_FILE = Path(__file__).parent.parent / "data" / "trade_datasheet.csv"
_VERSION       = 4          # bumped: added asset_class field to positions and trades


# ── Dataclass: one open position snapshot ─────────────────────────────────

@dataclass
class OpenPositionSnapshot:
    """Everything needed to reconstruct a live Position from disk."""
    symbol:             str
    side:               str    # "LONG" or "SHORT"
    qty:                float
    entry_price:        float
    entry_fee_paid:     float
    funding_paid_total: float
    entry_time_ms:      int    # wall-clock ms when the position was opened
    asset_class:        str = "crypto"   # "crypto" | "stock"


# ── Dataclass: one closed trade record ────────────────────────────────────

@dataclass
class ClosedTradeRecord:
    symbol:        str
    side:          str
    qty:           float
    entry_price:   float
    exit_price:    float
    entry_fee:     float
    exit_fee:      float
    funding_total: float
    net_pnl:       float
    entry_time_ms: int
    exit_time_ms:  int
    exit_reason:   str = "signal"   # "signal" | "stop_loss"
    asset_class:   str = "crypto"   # "crypto" | "stock"


# ── CSV datasheet columns (written in this exact order) ───────────────────
_CSV_FIELDS = [
    "timestamp",
    "symbol",
    "market",          # NEW in v4: "crypto" or "stock"
    "side",
    "qty",
    "entry_price",
    "exit_price",
    "entry_fee",
    "exit_fee",
    "funding_total",
    "net_pnl",
    "return_pct",
    "exit_reason",
    "balance_after",
]


def _trade_to_csv_row(t: ClosedTradeRecord, balance_after: float) -> dict:
    """Convert one ClosedTradeRecord to a CSV row dict."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.exit_time_ms / 1000))
    entry_notional = t.entry_price * t.qty
    return_pct = (t.net_pnl / entry_notional * 100) if entry_notional > 0 else 0.0
    return {
        "timestamp":    ts,
        "symbol":       t.symbol,
        "market":       getattr(t, "asset_class", "crypto"),   # v4 field
        "side":         t.side,
        "qty":          f"{t.qty:.8f}",
        "entry_price":  f"{t.entry_price:.4f}",
        "exit_price":   f"{t.exit_price:.4f}",
        "entry_fee":    f"{t.entry_fee:.4f}",
        "exit_fee":     f"{t.exit_fee:.4f}",
        "funding_total":f"{t.funding_total:.4f}",
        "net_pnl":      f"{t.net_pnl:.4f}",
        "return_pct":   f"{return_pct:.4f}",
        "exit_reason":  t.exit_reason,
        "balance_after":f"{balance_after:.4f}",
    }


# ── Main state class ────────────────────────────────────────────────────────

class TraderState:
    """All in-memory state for the live multi-symbol trading session.

    Call save() after any mutation to persist to disk.
    """

    def __init__(
        self,
        balance:              float                                     = config.STARTING_FAKE_BALANCE_USDT,
        starting_balance:     float                                     = config.STARTING_FAKE_BALANCE_USDT,
        open_positions:       Optional[dict[str, OpenPositionSnapshot]] = None,
        closed_trades:        Optional[list[ClosedTradeRecord]]         = None,
        last_candle_time:     Optional[dict[str, dict[str, int]]]       = None,
        next_funding_time_ms: int                                       = 0,
        activity_log:         Optional[list[str]]                       = None,
    ):
        self.balance              = balance
        self.starting_balance     = starting_balance
        self.open_positions       = open_positions or {}
        self.closed_trades        = closed_trades or []
        self.last_candle_time     = last_candle_time or {}
        self.next_funding_time_ms = next_funding_time_ms
        self.activity_log         = activity_log or []

    # ── Candle time helpers ────────────────────────────────────────────────

    def get_candle_times(self, symbol: str) -> dict[str, int]:
        """Return per-interval candle timestamps for a symbol; creates blank entry if missing."""
        if symbol not in self.last_candle_time:
            self.last_candle_time[symbol] = {"1h": 0, "1d": 0, "1w": 0}
        return self.last_candle_time[symbol]

    # ── Logging ────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        """Append a timestamped event; keep only the last 50."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.activity_log.append(f"[{ts}] {msg}")
        self.activity_log = self.activity_log[-50:]

    # ── Portfolio round-trip ───────────────────────────────────────────────

    def to_portfolio(self) -> PaperPortfolio:
        """Reconstruct a PaperPortfolio from this state.

        The returned portfolio has the correct balance and all open positions
        reconstructed as live Position objects. The ledger is empty (we
        maintain closed_trades ourselves) but that doesn't affect any of the
        execution-engine maths.
        """
        p = PaperPortfolio(starting_balance=self.starting_balance)
        p.balance = self.balance          # override the starting value

        for sym, op in self.open_positions.items():
            pos = Position(
                symbol             = op.symbol,
                side               = Side[op.side],
                qty                = op.qty,
                entry_price        = op.entry_price,
                entry_fee_paid     = op.entry_fee_paid,
                funding_paid_total = op.funding_paid_total,
            )
            p.positions[sym] = pos
        return p

    def sync_from_portfolio(
        self,
        portfolio:    PaperPortfolio,
        symbol:       str,
        new_entry_ms: Optional[int] = None,
    ) -> None:
        """Pull balance and one symbol's position back from a PaperPortfolio.

        new_entry_ms: pass the current wall-clock ms when a NEW position was
        just opened for this symbol; None to preserve the existing timestamp.
        """
        self.balance = portfolio.balance

        pos = portfolio.positions.get(symbol)
        if pos and pos.is_open:
            old = self.open_positions.get(symbol)
            if (
                new_entry_ms is None
                and old is not None
                and abs(old.entry_price - pos.entry_price) < 0.01
            ):
                entry_ms = old.entry_time_ms
            else:
                entry_ms = new_entry_ms or int(time.time() * 1000)

            asset_class = "stock" if symbol.endswith(".NS") else "crypto"
            self.open_positions[symbol] = OpenPositionSnapshot(
                symbol             = pos.symbol,
                side               = pos.side.value,
                qty                = pos.qty,
                entry_price        = pos.entry_price,
                entry_fee_paid     = pos.entry_fee_paid,
                funding_paid_total = pos.funding_paid_total,
                entry_time_ms      = entry_ms,
                asset_class        = asset_class,
            )
        else:
            self.open_positions.pop(symbol, None)

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self) -> None:
        """Atomically write state JSON and regenerate the CSV datasheet."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version":              _VERSION,
            "balance":              self.balance,
            "starting_balance":     self.starting_balance,
            "open_positions":       {s: asdict(op) for s, op in self.open_positions.items()},
            "closed_trades":        [asdict(t) for t in self.closed_trades],
            "last_candle_time":     self.last_candle_time,
            "next_funding_time_ms": self.next_funding_time_ms,
            "activity_log":         self.activity_log,
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(STATE_FILE)

        # Regenerate CSV datasheet from the authoritative closed_trades list.
        self._write_datasheet()

    def _write_datasheet(self) -> None:
        """Rewrite data/trade_datasheet.csv from scratch using closed_trades.

        We rebuild the whole file each time so there's no risk of duplicates
        or stale rows. The file is small enough that this is instant.
        """
        DATASHEET_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Reconstruct running balance after each trade so the sheet makes sense.
        running_balance = self.starting_balance
        rows = []
        for t in self.closed_trades:
            running_balance += t.net_pnl
            rows.append(_trade_to_csv_row(t, running_balance))

        with DATASHEET_FILE.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    @classmethod
    def load(cls) -> "TraderState":
        """Load state from disk. Returns a fresh state if the file is absent.
        Handles both v2 (single open_position) and v3 (open_positions dict)."""
        if not STATE_FILE.exists():
            s = cls()
            s.log("Fresh state created — starting balance $10,000 paper money.")
            return s

        try:
            raw = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"State file is corrupt ({exc}). "
                "Run with --reset to start fresh."
            ) from exc

        version = raw.get("version", 1)

        # ── Migrate v2/v3 → v4 ──────────────────────────────────────────
        if version < 4:
            open_positions: dict[str, OpenPositionSnapshot] = {}

            if raw.get("open_positions"):
                # v3 file already has the multi-symbol dict — just add asset_class
                for sym, op_raw in raw["open_positions"].items():
                    op_raw.pop("exit_reason", None)
                    op_raw.setdefault("asset_class", "crypto")   # v4 migration
                    open_positions[sym] = OpenPositionSnapshot(**op_raw)
            elif raw.get("open_position"):
                # v2 legacy: single open_position object
                op_raw = raw["open_position"]
                op_raw.pop("exit_reason", None)
                op_raw.setdefault("asset_class", "crypto")
                open_positions[op_raw["symbol"]] = OpenPositionSnapshot(**op_raw)

            # v2 last_candle_time was {"1h": int, "1d": int, "1w": int}
            # v3 last_candle_time was already per-symbol — preserve it if present
            raw_lct = raw.get("last_candle_time", {})
            if raw_lct and isinstance(next(iter(raw_lct.values()), None), dict):
                # Already per-symbol (v3 format) — carry forward as-is
                new_times = raw_lct
            else:
                # v2 format — assign old flat times to each symbol
                old_times = raw_lct or {"1h": 0, "1d": 0, "1w": 0}
                new_times = {}
                for sym in (open_positions.keys() or [config.DEFAULT_SYMBOL]):
                    new_times[sym] = dict(old_times)

            closed_raw = raw.get("closed_trades", [])
            closed = []
            for t in closed_raw:
                t.setdefault("exit_reason", "signal")
                t.setdefault("asset_class", "crypto")   # v4 migration
                closed.append(ClosedTradeRecord(**t))

            s = cls(
                balance              = raw["balance"],
                starting_balance     = raw["starting_balance"],
                open_positions       = open_positions,
                closed_trades        = closed,
                last_candle_time     = new_times,
                next_funding_time_ms = raw.get("next_funding_time_ms", 0),
                activity_log         = raw.get("activity_log", []),
            )
            s.log("State migrated from v2/v3 → v4 (added asset_class field).")
            return s

        # ── v3/v4 native load ────────────────────────────────────────────
        ops: dict[str, OpenPositionSnapshot] = {}
        for sym, op_raw in raw.get("open_positions", {}).items():
            op_raw.pop("exit_reason", None)   # exit_reason is only on ClosedTradeRecord
            op_raw.setdefault("asset_class", "crypto")   # v4 migration
            ops[sym] = OpenPositionSnapshot(**op_raw)

        closed_raw = raw.get("closed_trades", [])
        closed = []
        for t in closed_raw:
            t.setdefault("exit_reason", "signal")
            t.setdefault("asset_class", "crypto")   # v4 migration
            closed.append(ClosedTradeRecord(**t))

        return cls(
            balance              = raw["balance"],
            starting_balance     = raw["starting_balance"],
            open_positions       = ops,
            closed_trades        = closed,
            last_candle_time     = raw.get("last_candle_time", {}),
            next_funding_time_ms = raw.get("next_funding_time_ms", 0),
            activity_log         = raw.get("activity_log", []),
        )

    @classmethod
    def reset(cls) -> "TraderState":
        """Delete any existing state file and return a blank state."""
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        if DATASHEET_FILE.exists():
            DATASHEET_FILE.unlink()
        s = cls()
        s.log("State RESET — starting balance $10,000 paper money.")
        return s

    # ── Convenience summary ────────────────────────────────────────────────

    def performance_summary(self) -> dict:
        trades    = self.closed_trades
        wins      = [t for t in trades if t.net_pnl > 0]
        losses    = [t for t in trades if t.net_pnl <= 0]
        net_pnl   = sum(t.net_pnl for t in trades)
        sl_trades = [t for t in trades if t.exit_reason == "stop_loss"]
        return {
            "trades_closed":   len(trades),
            "wins":            len(wins),
            "losses":          len(losses),
            "stop_loss_exits": len(sl_trades),
            "win_rate":        len(wins) / len(trades) if trades else None,
            "net_realized":    net_pnl,
            "total_return":    net_pnl / self.starting_balance * 100,
        }
