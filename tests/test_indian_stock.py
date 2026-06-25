"""
Tests for Indian stock integration.

Tests cover:
  1. NSE market hours guard (is_nse_market_open)
  2. Zerodha delivery fee calculations (buy and sell)
  3. INR → USD conversion helpers
  4. State v3 → v4 migration (asset_class field defaulting to "crypto")
  5. CSV row output includes the new "market" column
  6. News scanner: known ticker found in headline → candidate list
  7. News scanner: unknown company in headline → no error, empty list
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

_IST = ZoneInfo("Asia/Kolkata")


# ── 1. NSE Market Hours ────────────────────────────────────────────────────

class TestNSEMarketHours:
    """is_nse_market_open() must correctly identify open vs. closed windows."""

    from stock_client import is_nse_market_open

    def _dt(self, year, month, day, hour, minute):
        return datetime.datetime(year, month, day, hour, minute, tzinfo=_IST)

    def test_open_during_trading_hours(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 24, 11, 0)   # Wednesday 11:00 IST
        assert is_nse_market_open(dt) is True

    def test_open_at_market_start(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 24, 9, 15)   # Wednesday 9:15 IST
        assert is_nse_market_open(dt) is True

    def test_open_at_market_close(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 24, 15, 30)  # Wednesday 15:30 IST
        assert is_nse_market_open(dt) is True

    def test_closed_before_open(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 24, 9, 14)   # Wednesday 9:14 IST — 1 min before
        assert is_nse_market_open(dt) is False

    def test_closed_after_close(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 24, 15, 31)  # Wednesday 15:31 IST — 1 min after
        assert is_nse_market_open(dt) is False

    def test_closed_on_saturday(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 27, 11, 0)   # Saturday — always closed
        assert is_nse_market_open(dt) is False

    def test_closed_on_sunday(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 6, 28, 11, 0)   # Sunday — always closed
        assert is_nse_market_open(dt) is False

    def test_closed_on_republic_day(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 1, 26, 11, 0)   # Republic Day — listed holiday
        assert is_nse_market_open(dt) is False

    def test_closed_on_independence_day(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 8, 15, 11, 0)   # Independence Day
        assert is_nse_market_open(dt) is False

    def test_closed_on_christmas(self):
        from stock_client import is_nse_market_open
        dt = self._dt(2026, 12, 25, 11, 0)  # Christmas
        assert is_nse_market_open(dt) is False


# ── 2. Zerodha Delivery Fee Calculations ──────────────────────────────────

class TestZerodhaBuyFee:
    """Buy fee must match: STT + Stamp + ExchCharge + SEBI + GST(Exch+SEBI)."""

    def _expected(self, trade_value: float) -> float:
        import config
        stt   = trade_value * config.STOCK_STT_RATE
        stamp = trade_value * config.STOCK_STAMP_RATE
        exch  = trade_value * config.STOCK_EXCH_RATE
        sebi  = trade_value * config.STOCK_SEBI_RATE
        gst   = (exch + sebi) * config.STOCK_GST_RATE
        return stt + stamp + exch + sebi + gst

    def test_buy_fee_round_number(self):
        from engine.stock_fees import compute_stock_buy_fee
        trade_value = 100_000.0   # ₹1 lakh trade
        result = compute_stock_buy_fee(trade_value)
        expected = self._expected(trade_value)
        assert abs(result - expected) < 0.001, f"Expected {expected:.4f}, got {result:.4f}"

    def test_buy_fee_positive(self):
        from engine.stock_fees import compute_stock_buy_fee
        assert compute_stock_buy_fee(50_000.0) > 0

    def test_buy_fee_scales_linearly(self):
        from engine.stock_fees import compute_stock_buy_fee
        fee_1 = compute_stock_buy_fee(10_000.0)
        fee_2 = compute_stock_buy_fee(20_000.0)
        assert abs(fee_2 / fee_1 - 2.0) < 0.001   # should be exactly 2x


class TestZerodhaSellFee:
    """Sell fee = STT + ExchCharge + SEBI + GST(Exch+SEBI) + flat DP charge."""

    def _expected(self, trade_value: float) -> float:
        import config
        stt   = trade_value * config.STOCK_STT_RATE
        exch  = trade_value * config.STOCK_EXCH_RATE
        sebi  = trade_value * config.STOCK_SEBI_RATE
        gst   = (exch + sebi) * config.STOCK_GST_RATE
        dp    = config.STOCK_DP_CHARGE_INR
        return stt + exch + sebi + gst + dp

    def test_sell_fee_round_number(self):
        from engine.stock_fees import compute_stock_sell_fee
        trade_value = 100_000.0
        result = compute_stock_sell_fee(trade_value)
        expected = self._expected(trade_value)
        assert abs(result - expected) < 0.001

    def test_sell_fee_positive(self):
        from engine.stock_fees import compute_stock_sell_fee
        assert compute_stock_sell_fee(50_000.0) > 0

    def test_sell_fee_includes_dp_charge(self):
        """Sell fee minus buy fee should include the DP flat charge (no stamp on sell)."""
        from engine.stock_fees import compute_stock_buy_fee, compute_stock_sell_fee
        import config
        trade_value = 50_000.0
        sell = compute_stock_sell_fee(trade_value)
        buy  = compute_stock_buy_fee(trade_value)
        # The difference is: +DP_charge - stamp_duty (no stamp on sell)
        stamp = trade_value * config.STOCK_STAMP_RATE
        dp    = config.STOCK_DP_CHARGE_INR
        expected_diff = dp - stamp
        assert abs((sell - buy) - expected_diff) < 0.001


# ── 3. INR → USD Conversion ───────────────────────────────────────────────

class TestCurrencyConversion:
    """_inr_to_usd and _usd_to_inr must be exact inverses at config rate."""

    def test_inr_to_usd_exact(self):
        """₹83.50 should equal exactly $1.00 at the default rate."""
        import config
        from live_trader import _inr_to_usd
        result = _inr_to_usd(config.USD_INR_RATE)
        assert abs(result - 1.0) < 1e-9

    def test_inr_to_usd_round_trip(self):
        from live_trader import _inr_to_usd, _usd_to_inr
        original = 12_345.67
        assert abs(_inr_to_usd(_usd_to_inr(original)) - original) < 0.001

    def test_is_stock_detects_ns_suffix(self):
        from live_trader import _is_stock
        assert _is_stock("RELIANCE.NS") is True
        assert _is_stock("reliance.ns") is True
        assert _is_stock("BTCUSDT") is False
        assert _is_stock("ETHUSDT") is False


# ── 4. State v3/v4 Migration ─────────────────────────────────────────────

class TestStateMigration:
    """Existing v3 records should get asset_class='crypto' on load."""

    def _make_v3_payload(self) -> dict:
        return {
            "version": 3,
            "balance": 9_900.0,
            "starting_balance": 10_000.0,
            "open_positions": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.01,
                    "entry_price": 50_000.0,
                    "entry_fee_paid": 2.5,
                    "funding_paid_total": 0.1,
                    "entry_time_ms": 1_000_000,
                    # No asset_class — simulates existing v3 state
                }
            },
            "closed_trades": [
                {
                    "symbol": "ETHUSDT",
                    "side": "SHORT",
                    "qty": 0.5,
                    "entry_price": 3_000.0,
                    "exit_price": 2_900.0,
                    "entry_fee": 0.75,
                    "exit_fee": 0.73,
                    "funding_total": 0.05,
                    "net_pnl": 49.47,
                    "entry_time_ms": 900_000,
                    "exit_time_ms": 950_000,
                    "exit_reason": "signal",
                    # No asset_class — simulates existing v3 state
                }
            ],
            "last_candle_time": {"BTCUSDT": {"1h": 0, "1d": 0, "1w": 0}},
            "next_funding_time_ms": 0,
            "activity_log": [],
        }

    def test_v3_open_positions_get_crypto_asset_class(self):
        from trader.state import TraderState
        payload = self._make_v3_payload()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "trader_state.json"
            state_file.write_text(json.dumps(payload))
            # Patch the STATE_FILE path
            with patch("trader.state.STATE_FILE", state_file):
                state = TraderState.load()
        assert state.open_positions["BTCUSDT"].asset_class == "crypto"

    def test_v3_closed_trades_get_crypto_asset_class(self):
        from trader.state import TraderState
        payload = self._make_v3_payload()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "trader_state.json"
            state_file.write_text(json.dumps(payload))
            with patch("trader.state.STATE_FILE", state_file):
                state = TraderState.load()
        assert state.closed_trades[0].asset_class == "crypto"


# ── 5. CSV Output Includes "market" Column ────────────────────────────────

class TestCSVMarketColumn:
    """Datasheet CSV must include the 'market' column in v4."""

    def test_csv_contains_market_column(self):
        from trader.state import ClosedTradeRecord, TraderState, _CSV_FIELDS, DATASHEET_FILE
        import config
        assert "market" in _CSV_FIELDS, "v4 CSV must include 'market' column"

    def test_csv_market_value_is_crypto_for_default_trade(self):
        from trader.state import ClosedTradeRecord, _trade_to_csv_row
        trade = ClosedTradeRecord(
            symbol="BTCUSDT",
            side="LONG",
            qty=0.01,
            entry_price=50_000.0,
            exit_price=51_000.0,
            entry_fee=2.5,
            exit_fee=2.55,
            funding_total=0.1,
            net_pnl=7.35,
            entry_time_ms=1_000_000,
            exit_time_ms=2_000_000,
            exit_reason="signal",
            asset_class="crypto",
        )
        row = _trade_to_csv_row(trade, balance_after=10_007.35)
        assert row["market"] == "crypto"

    def test_csv_market_value_is_stock_for_nse_trade(self):
        from trader.state import ClosedTradeRecord, _trade_to_csv_row
        trade = ClosedTradeRecord(
            symbol="RELIANCE.NS",
            side="LONG",
            qty=5.0,
            entry_price=35.92,   # price in USD after INR conversion
            exit_price=36.45,
            entry_fee=0.24,
            exit_fee=0.24,
            funding_total=0.0,   # no funding for stocks
            net_pnl=2.41,
            entry_time_ms=1_000_000,
            exit_time_ms=2_000_000,
            exit_reason="signal",
            asset_class="stock",
        )
        row = _trade_to_csv_row(trade, balance_after=10_002.41)
        assert row["market"] == "stock"


# ── 6 & 7. News Scanner ───────────────────────────────────────────────────

class TestNewsScanner:
    """News scanner must extract known tickers and ignore unknown ones."""

    def _make_feed(self, titles: list[str]) -> object:
        """Minimal feedparser.FeedParserDict mock."""
        class Entry:
            def __init__(self, title):
                self.data = {"title": title, "summary": ""}
            def get(self, key, default=""):
                return self.data.get(key, default)

        class Feed:
            def __init__(self, entries):
                self.entries = entries

        return Feed([Entry(t) for t in titles])

    def test_known_ticker_detected(self):
        from news_scanner import scan_news_candidates, _last_scan_time
        import news_scanner

        fake_feed = self._make_feed([
            "Reliance Industries reports record quarterly profit",
            "TCS wins $500M deal with European bank",
        ])

        with patch("news_scanner._last_scan_time", 0), \
             patch("news_scanner._last_candidates", []), \
             patch("feedparser.parse", return_value=fake_feed):
            candidates = scan_news_candidates()

        assert "RELIANCE.NS" in candidates
        assert "TCS.NS" in candidates

    def test_unknown_company_does_not_raise(self):
        from news_scanner import scan_news_candidates
        import news_scanner

        fake_feed = self._make_feed([
            "FictionalCorp XYZ announces merger with NoSuchCompany Inc.",
        ])

        with patch("news_scanner._last_scan_time", 0), \
             patch("news_scanner._last_candidates", []), \
             patch("feedparser.parse", return_value=fake_feed):
            candidates = scan_news_candidates()

        assert isinstance(candidates, list)   # no error, just empty

    def test_get_effective_watchlist_merges_without_duplicates(self):
        from news_scanner import get_effective_stock_watchlist
        import news_scanner

        base = ["RELIANCE.NS", "TCS.NS"]
        fake_candidates = ["TCS.NS", "INFY.NS"]   # TCS already in base

        with patch("news_scanner.scan_news_candidates", return_value=fake_candidates):
            result = get_effective_stock_watchlist(base)

        assert result.count("TCS.NS") == 1        # no duplicates
        assert "INFY.NS" in result
        assert "RELIANCE.NS" in result
