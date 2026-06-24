"""
Tests for multi-symbol risk management and state/CSV logic.

These tests verify:
 1. Capital allocation formula (budget = balance / N)
 2. Stop-loss trigger detection
 3. Closed trade CSV row format
 4. TraderState v2 → v3 migration (loading old state file without errors)
 5. TraderState round-trip: save → load for v3 schema
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# ── Unit-under-test imports ───────────────────────────────────────────────
import config
from trader.state import (
    ClosedTradeRecord,
    OpenPositionSnapshot,
    TraderState,
    _trade_to_csv_row,
)
from live_trader import (
    _allocated_budget,
    _is_stop_loss_triggered,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_op(symbol="BTCUSDT", side="LONG", entry_price=60_000.0, qty=0.1):
    return OpenPositionSnapshot(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_price=entry_price,
        entry_fee_paid=3.0,
        funding_paid_total=0.0,
        entry_time_ms=int(time.time() * 1000),
    )


def _make_state(balance=10_000.0, positions=None):
    s = TraderState(balance=balance)
    if positions:
        s.open_positions = positions
    return s


# ═══════════════════════════════════════════════════════════════════════════
# 1. Capital Allocation
# ═══════════════════════════════════════════════════════════════════════════

class TestCapitalAllocation(unittest.TestCase):

    def test_single_symbol_gets_full_balance(self):
        state = _make_state(balance=10_000.0)
        budget = _allocated_budget(state, ["BTCUSDT"])
        self.assertAlmostEqual(budget, 10_000.0, places=2)

    def test_four_symbols_split_equally(self):
        state = _make_state(balance=10_000.0)
        watchlist = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        budget = _allocated_budget(state, watchlist)
        self.assertAlmostEqual(budget, 2_500.0, places=2)

    def test_budget_reflects_current_balance(self):
        state = _make_state(balance=7_500.0)
        budget = _allocated_budget(state, ["BTCUSDT", "ETHUSDT"])
        self.assertAlmostEqual(budget, 3_750.0, places=2)

    def test_empty_watchlist_does_not_divide_by_zero(self):
        """Empty watchlist must not raise ZeroDivisionError."""
        state = _make_state(balance=10_000.0)
        budget = _allocated_budget(state, [])
        self.assertGreater(budget, 0)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Stop-Loss Trigger
# ═══════════════════════════════════════════════════════════════════════════

class TestStopLoss(unittest.TestCase):

    def test_long_triggers_when_price_drops_by_stop_loss_pct(self):
        """LONG position should trigger stop-loss when mark price drops ≥ 2%."""
        op = _make_op(side="LONG", entry_price=60_000.0)
        mark_price = 60_000.0 * (1 - 0.02)   # exactly 2% below entry
        with patch.object(config, "STOP_LOSS_PCT", 0.02):
            self.assertTrue(_is_stop_loss_triggered(op, mark_price))

    def test_long_does_not_trigger_when_loss_is_below_threshold(self):
        op = _make_op(side="LONG", entry_price=60_000.0)
        mark_price = 60_000.0 * (1 - 0.015)   # only 1.5% drop
        with patch.object(config, "STOP_LOSS_PCT", 0.02):
            self.assertFalse(_is_stop_loss_triggered(op, mark_price))

    def test_short_triggers_when_price_rises_by_stop_loss_pct(self):
        """SHORT position should trigger stop-loss when mark price rises ≥ 2%."""
        op = _make_op(side="SHORT", entry_price=60_000.0)
        mark_price = 60_000.0 * (1 + 0.02)   # exactly 2% above entry
        with patch.object(config, "STOP_LOSS_PCT", 0.02):
            self.assertTrue(_is_stop_loss_triggered(op, mark_price))

    def test_short_does_not_trigger_when_in_profit(self):
        op = _make_op(side="SHORT", entry_price=60_000.0)
        mark_price = 58_000.0   # price dropped → SHORT is profitable
        with patch.object(config, "STOP_LOSS_PCT", 0.02):
            self.assertFalse(_is_stop_loss_triggered(op, mark_price))

    def test_stop_loss_disabled_when_none(self):
        """STOP_LOSS_PCT=None must never trigger regardless of price."""
        op = _make_op(side="LONG", entry_price=60_000.0)
        mark_price = 1.0   # catastrophic loss — should still return False
        with patch.object(config, "STOP_LOSS_PCT", None):
            self.assertFalse(_is_stop_loss_triggered(op, mark_price))

    def test_long_triggers_above_threshold_not_below(self):
        op = _make_op(side="LONG", entry_price=100.0)
        with patch.object(config, "STOP_LOSS_PCT", 0.05):
            self.assertTrue(_is_stop_loss_triggered(op, 94.0))   # 6% drop ≥ 5%
            self.assertFalse(_is_stop_loss_triggered(op, 96.0))  # 4% drop < 5%


# ═══════════════════════════════════════════════════════════════════════════
# 3. CSV Row Generation
# ═══════════════════════════════════════════════════════════════════════════

class TestCSVRow(unittest.TestCase):

    def _sample_trade(self, net_pnl=100.0, exit_reason="signal"):
        return ClosedTradeRecord(
            symbol="BTCUSDT",
            side="LONG",
            qty=0.1,
            entry_price=60_000.0,
            exit_price=61_000.0,
            entry_fee=3.0,
            exit_fee=3.05,
            funding_total=0.5,
            net_pnl=net_pnl,
            entry_time_ms=1_700_000_000_000,
            exit_time_ms=1_700_003_600_000,
            exit_reason=exit_reason,
        )

    def test_csv_row_has_all_required_fields(self):
        from trader.state import _CSV_FIELDS
        row = _trade_to_csv_row(self._sample_trade(), balance_after=10_100.0)
        for field in _CSV_FIELDS:
            self.assertIn(field, row, f"Missing CSV field: {field}")

    def test_stop_loss_exit_reason_preserved(self):
        row = _trade_to_csv_row(self._sample_trade(exit_reason="stop_loss"), balance_after=9_900.0)
        self.assertEqual(row["exit_reason"], "stop_loss")

    def test_return_pct_formula(self):
        """return_pct = net_pnl / (entry_price * qty) * 100."""
        trade = self._sample_trade(net_pnl=60.0)   # 60 / (60000 * 0.1) * 100 = 1%
        row   = _trade_to_csv_row(trade, balance_after=10_060.0)
        self.assertAlmostEqual(float(row["return_pct"]), 1.0, places=2)

    def test_balance_after_recorded_correctly(self):
        row = _trade_to_csv_row(self._sample_trade(), balance_after=12_345.67)
        self.assertAlmostEqual(float(row["balance_after"]), 12_345.67, places=2)


# ═══════════════════════════════════════════════════════════════════════════
# 4. State v2 → v3 Migration
# ═══════════════════════════════════════════════════════════════════════════

_V2_STATE = {
    "version": 2,
    "balance": 9800.0,
    "starting_balance": 10_000.0,
    "open_position": {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.15,
        "entry_price": 62_000.0,
        "entry_fee_paid": 4.65,
        "funding_paid_total": 0.5,
        "entry_time_ms": 1_780_000_000_000,
    },
    "closed_trades": [],
    "last_candle_time": {"1h": 111, "1d": 222, "1w": 333},
    "next_funding_time_ms": 1_780_100_000_000,
    "activity_log": ["[2026-06-01] old log entry"],
}


class TestStateMigration(unittest.TestCase):

    def test_v2_loads_without_error(self):
        """Loading a v2 state file must not raise any exception."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "trader_state.json"
            state_path.write_text(json.dumps(_V2_STATE))

            from trader import state as state_module
            original = state_module.STATE_FILE
            state_module.STATE_FILE = state_path
            try:
                s = TraderState.load()
            finally:
                state_module.STATE_FILE = original

            # The BTCUSDT position should have been migrated
            self.assertIn("BTCUSDT", s.open_positions)
            self.assertEqual(s.open_positions["BTCUSDT"].side, "LONG")
            self.assertAlmostEqual(s.balance, 9800.0)

    def test_v2_candle_times_migrated_per_symbol(self):
        """v2 flat candle times must be available under the symbol key after migration."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "trader_state.json"
            state_path.write_text(json.dumps(_V2_STATE))

            from trader import state as state_module
            original = state_module.STATE_FILE
            state_module.STATE_FILE = state_path
            try:
                s = TraderState.load()
            finally:
                state_module.STATE_FILE = original

            times = s.get_candle_times("BTCUSDT")
            self.assertEqual(times["1h"], 111)
            self.assertEqual(times["1d"], 222)
            self.assertEqual(times["1w"], 333)


# ═══════════════════════════════════════════════════════════════════════════
# 5. State v3 Save / Load Round-Trip
# ═══════════════════════════════════════════════════════════════════════════

class TestStateV3RoundTrip(unittest.TestCase):

    def _with_temp_state(self, state: TraderState):
        """Context manager that patches STATE_FILE and DATASHEET_FILE to temp files."""
        import trader.state as sm
        return sm

    def test_save_and_reload_preserves_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            import trader.state as sm
            orig_state = sm.STATE_FILE
            orig_sheet = sm.DATASHEET_FILE
            sm.STATE_FILE     = Path(tmp) / "trader_state.json"
            sm.DATASHEET_FILE = Path(tmp) / "trade_datasheet.csv"
            try:
                s = TraderState()
                s.open_positions["BTCUSDT"] = _make_op("BTCUSDT", "SHORT", 64_000.0)
                s.open_positions["ETHUSDT"] = _make_op("ETHUSDT", "LONG", 3_500.0)
                s.save()

                loaded = TraderState.load()
                self.assertIn("BTCUSDT", loaded.open_positions)
                self.assertIn("ETHUSDT", loaded.open_positions)
                self.assertEqual(loaded.open_positions["BTCUSDT"].side, "SHORT")
                self.assertEqual(loaded.open_positions["ETHUSDT"].side, "LONG")
            finally:
                sm.STATE_FILE     = orig_state
                sm.DATASHEET_FILE = orig_sheet

    def test_csv_written_on_save_with_closed_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            import trader.state as sm
            orig_state = sm.STATE_FILE
            orig_sheet = sm.DATASHEET_FILE
            sm.STATE_FILE     = Path(tmp) / "trader_state.json"
            sm.DATASHEET_FILE = Path(tmp) / "trade_datasheet.csv"
            try:
                s = TraderState()
                s.closed_trades.append(ClosedTradeRecord(
                    symbol="ETHUSDT", side="LONG", qty=1.0,
                    entry_price=3_400.0, exit_price=3_500.0,
                    entry_fee=1.7, exit_fee=1.75,
                    funding_total=0.1, net_pnl=96.65,
                    entry_time_ms=1_780_000_000_000,
                    exit_time_ms=1_780_003_600_000,
                    exit_reason="signal",
                ))
                s.save()

                # CSV file must exist and have 2 lines (header + 1 row)
                lines = sm.DATASHEET_FILE.read_text().strip().splitlines()
                self.assertEqual(len(lines), 2)
                self.assertIn("ETHUSDT", lines[1])
            finally:
                sm.STATE_FILE     = orig_state
                sm.DATASHEET_FILE = orig_sheet


if __name__ == "__main__":
    unittest.main()
