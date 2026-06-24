"""
Tests for strategies and the backtester.

Every assertion here uses a known, hand-calculated synthetic dataset so the
correct answer can be verified by reading the code — no live API calls are made.

Coverage:
  - _sma()          helper (tested indirectly via SMACrossoverStrategy)
  - _wilder_rsi()   — tested directly with a known series
  - _bollinger()    — tested directly with a known series
  - Signal routing  — each strategy emits the expected signal on crafted candles
  - Backtester      — applies slippage, fees, and funding correctly on a tiny run
"""
from __future__ import annotations

import math
import pytest

from strategies.base import Signal
from strategies.sma import SMACrossoverStrategy, _sma
from strategies.rsi import RSIStrategy, _wilder_rsi
from strategies.bollinger import BollingerBandsStrategy, _bollinger
from backtest.backtester import (
    SLIPPAGE_RATE,
    BacktestMetrics,
    _entry_price,
    _exit_book,
    run_backtest,
)
from exchange_client import HistoricalFundingRate
import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candles(closes: list[float]) -> list[dict]:
    """Wrap a list of close prices in minimal candle dicts."""
    return [
        {
            "open_time": i * 3_600_000,
            "open":  c,
            "high":  c,
            "low":   c,
            "close": c,
            "volume": 100.0,
            "close_time": i * 3_600_000 + 3_599_999,
        }
        for i, c in enumerate(closes)
    ]


# ---------------------------------------------------------------------------
# _sma
# ---------------------------------------------------------------------------

class TestSMA:
    def test_exact_value(self):
        assert _sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)

    def test_returns_none_when_insufficient(self):
        assert _sma([1.0, 2.0], 5) is None

    def test_full_window(self):
        assert _sma([10.0, 20.0, 30.0], 3) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# _wilder_rsi
# ---------------------------------------------------------------------------

class TestWilderRSI:
    def test_returns_none_when_insufficient(self):
        # Need period+1 values; period=14 means 15 values minimum
        assert _wilder_rsi([50.0] * 14, 14) is None

    def test_all_up_returns_100(self):
        # All gains → avg_loss stays at zero → RSI = 100
        closes = [float(i) for i in range(1, 20)]
        rsi = _wilder_rsi(closes, 14)
        assert rsi == pytest.approx(100.0)

    def test_all_down_returns_0(self):
        # All losses → avg_gain stays at zero → RSI = 0
        closes = [float(20 - i) for i in range(20)]
        rsi = _wilder_rsi(closes, 14)
        assert rsi == pytest.approx(0.0)

    def test_flat_prices_return_100(self):
        # No movement → avg_loss = 0 → RSI = 100
        closes = [100.0] * 20
        rsi = _wilder_rsi(closes, 14)
        assert rsi == pytest.approx(100.0)

    def test_result_in_valid_range(self):
        import random
        random.seed(42)
        closes = [100.0 + random.gauss(0, 1) for _ in range(50)]
        rsi = _wilder_rsi(closes, 14)
        assert rsi is not None
        assert 0.0 <= rsi <= 100.0


# ---------------------------------------------------------------------------
# _bollinger
# ---------------------------------------------------------------------------

class TestBollinger:
    def test_returns_none_when_insufficient(self):
        assert _bollinger([1.0, 2.0], 20, 2.0) is None

    def test_flat_prices_have_zero_bandwidth(self):
        closes = [100.0] * 20
        lower, mid, upper = _bollinger(closes, 20, 2.0)
        assert mid   == pytest.approx(100.0)
        assert lower == pytest.approx(100.0)
        assert upper == pytest.approx(100.0)

    def test_bands_symmetric(self):
        closes = [99.0, 100.0, 101.0] * 7  # 21 values
        lower, mid, upper = _bollinger(closes, 20, 2.0)
        assert upper - mid == pytest.approx(mid - lower, rel=1e-9)

    def test_known_values(self):
        # [1, 2, 3, 4, 5]  → mean=3, pop-std=sqrt(2)≈1.4142
        closes = [1.0, 2.0, 3.0, 4.0, 5.0]
        lower, mid, upper = _bollinger(closes, 5, 2.0)
        assert mid   == pytest.approx(3.0)
        assert upper == pytest.approx(3.0 + 2 * math.sqrt(2))
        assert lower == pytest.approx(3.0 - 2 * math.sqrt(2))


# ---------------------------------------------------------------------------
# SMACrossoverStrategy signals
# ---------------------------------------------------------------------------

class TestSMACrossover:
    def test_hold_when_insufficient_data(self):
        strat = SMACrossoverStrategy(fast=3, slow=5)
        # only 4 closes — not enough for slow=5
        candles = _make_candles([100.0, 101.0, 102.0, 103.0])
        assert strat.generate_signal(candles) == Signal.HOLD

    def test_long_when_fast_above_slow(self):
        strat = SMACrossoverStrategy(fast=2, slow=4)
        # rising prices: fast SMA > slow SMA
        closes = [100.0, 101.0, 102.0, 110.0, 120.0]
        candles = _make_candles(closes)
        signal = strat.generate_signal(candles)
        assert signal == Signal.LONG

    def test_short_when_fast_below_slow(self):
        strat = SMACrossoverStrategy(fast=2, slow=4)
        # falling prices: fast SMA < slow SMA
        closes = [120.0, 110.0, 100.0, 90.0, 80.0]
        candles = _make_candles(closes)
        signal = strat.generate_signal(candles)
        assert signal == Signal.SHORT

    def test_rejects_bad_params(self):
        with pytest.raises(ValueError):
            SMACrossoverStrategy(fast=21, slow=9)


# ---------------------------------------------------------------------------
# RSIStrategy signals
# ---------------------------------------------------------------------------

class TestRSIStrategy:
    def test_hold_when_insufficient_data(self):
        strat = RSIStrategy(period=14)
        candles = _make_candles([100.0] * 15)  # need period+2 = 16
        assert strat.generate_signal(candles) == Signal.HOLD

    def test_long_on_oversold_crossover(self):
        strat = RSIStrategy(period=5, oversold=50.0)
        # A declining then recovering series so RSI crosses up through 50
        closes_declining = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0]
        closes_bounce    = [91.0, 93.0, 95.0]
        candles = _make_candles(closes_declining + closes_bounce)
        # Check that at some point a LONG is generated
        signals = [strat.generate_signal(candles[:k]) for k in range(8, len(candles) + 1)]
        assert Signal.LONG in signals

    def test_hold_on_flat_prices(self):
        # Flat prices → RSI stays at 100 (no crossing), never fires LONG or CLOSE
        strat = RSIStrategy(period=5)
        candles = _make_candles([100.0] * 20)
        signals = {strat.generate_signal(candles[:k]) for k in range(8, 21)}
        # RSI=100 does not cross oversold(30) or overbought(70) from below, so HOLD
        assert signals == {Signal.HOLD}


# ---------------------------------------------------------------------------
# BollingerBandsStrategy signals
# ---------------------------------------------------------------------------

class TestBollingerStrategy:
    def test_hold_when_insufficient(self):
        strat = BollingerBandsStrategy(period=20)
        candles = _make_candles([100.0] * 20)  # need period+1 = 21
        assert strat.generate_signal(candles) == Signal.HOLD

    def test_long_below_lower_band(self):
        strat = BollingerBandsStrategy(period=5, num_std=1.0)
        # Stable prices then a sharp drop below the lower band
        closes = [100.0] * 5 + [100.0] * 5 + [80.0]
        candles = _make_candles(closes)
        signal = strat.generate_signal(candles)
        assert signal == Signal.LONG

    def test_short_above_upper_band(self):
        strat = BollingerBandsStrategy(period=5, num_std=1.0)
        # Stable prices then a sharp rise above the upper band
        closes = [100.0] * 5 + [100.0] * 5 + [120.0]
        candles = _make_candles(closes)
        signal = strat.generate_signal(candles)
        assert signal == Signal.SHORT


# ---------------------------------------------------------------------------
# Slippage helpers
# ---------------------------------------------------------------------------

class TestSlippageHelpers:
    def test_long_entry_pays_more(self):
        from engine.execution import Side
        price = 100.0
        assert _entry_price(price, Side.LONG) == pytest.approx(price * (1 + SLIPPAGE_RATE))

    def test_short_entry_receives_less(self):
        from engine.execution import Side
        price = 100.0
        assert _entry_price(price, Side.SHORT) == pytest.approx(price * (1 - SLIPPAGE_RATE))

    def test_long_exit_book_lower(self):
        from engine.execution import Side
        price = 100.0
        book = _exit_book(price, Side.LONG)
        assert book[0][0] == pytest.approx(price * (1 - SLIPPAGE_RATE))

    def test_short_exit_book_higher(self):
        from engine.execution import Side
        price = 100.0
        book = _exit_book(price, Side.SHORT)
        assert book[0][0] == pytest.approx(price * (1 + SLIPPAGE_RATE))


# ---------------------------------------------------------------------------
# Backtester end-to-end
# ---------------------------------------------------------------------------

class TestBacktester:
    def test_sma_completes_and_returns_metrics(self):
        """A simple rising price series should produce a few trades and valid metrics."""
        closes = (
            [100.0] * 5 +                          # flat → slow SMA catches up
            [101.0, 102.0, 103.0, 104.0, 105.0,   # rising → fast > slow → LONG
             106.0, 107.0, 108.0, 109.0, 110.0,
             111.0, 112.0, 113.0, 114.0, 115.0,
             116.0, 117.0, 118.0, 119.0, 120.0,
             119.0, 118.0, 117.0, 116.0, 115.0,   # falling → triggers SHORT
             114.0, 113.0, 112.0, 111.0, 110.0]
        )
        candles = _make_candles(closes)
        strat = SMACrossoverStrategy(fast=3, slow=5)
        m = run_backtest(strat, candles, [], symbol="TESTUSDT", interval="1h")

        assert isinstance(m, BacktestMetrics)
        assert m.candles_tested == len(candles)
        assert m.starting_balance == pytest.approx(config.STARTING_FAKE_BALANCE_USDT)
        # Return must be a real number, not NaN
        assert not math.isnan(m.total_return_pct)
        assert not math.isnan(m.max_drawdown_pct)

    def test_fees_reduce_balance_on_losing_trades(self):
        """Even in a flat market (zero price change), fees must reduce the balance."""
        closes = [100.0] * 30  # completely flat
        candles = _make_candles(closes)
        strat = SMACrossoverStrategy(fast=2, slow=4)
        m = run_backtest(strat, candles, [], symbol="TESTUSDT", interval="1h")

        # SMA crossover signals on flat market may produce no flip; but if any
        # trades are made, balance must be lower due to fees.
        if m.trades > 0:
            assert m.ending_balance < m.starting_balance, (
                "Any trade on a flat price must reduce balance due to fees"
            )

    def test_funding_payment_applied_correctly(self):
        """A large positive funding event should reduce a LONG holder's balance."""
        closes = [100.0] * 30
        candles = _make_candles(closes)

        # One funding event: settlement at close_time of candle 5, big positive rate
        # (positive rate means longs pay)
        funding = [
            HistoricalFundingRate(
                symbol="TESTUSDT",
                funding_rate=0.01,          # 1% — abnormally large to be detectable
                funding_time_ms=candles[5]["close_time"],
                mark_price=100.0,
            )
        ]

        # Run two backtests: same strategy, with and without the funding event,
        # using a strategy that goes LONG early and stays there.
        # We need a strategy that goes LONG on first bar, so use fast < slow
        # with ascending prices. Let's just patch: create a trivial strategy.
        class AlwaysLong(BollingerBandsStrategy):
            def generate_signal(self, candles):
                return Signal.LONG if len(candles) >= 1 else Signal.HOLD

        strat_no_fund  = AlwaysLong(period=5, num_std=2.0)
        strat_with_fund = AlwaysLong(period=5, num_std=2.0)

        m_no   = run_backtest(strat_no_fund,   candles, [],      symbol="TESTUSDT", interval="1h")
        m_with = run_backtest(strat_with_fund, candles, funding, symbol="TESTUSDT", interval="1h")

        assert m_with.ending_balance < m_no.ending_balance, (
            "A large positive funding event must reduce a long holder's ending balance"
        )

    def test_max_drawdown_non_negative(self):
        """Max drawdown must always be >= 0."""
        closes = [100.0 + i for i in range(50)]  # monotonically rising
        candles = _make_candles(closes)
        strat = SMACrossoverStrategy(fast=3, slow=5)
        m = run_backtest(strat, candles, [], symbol="TESTUSDT", interval="1h")
        assert m.max_drawdown_pct >= 0.0

    def test_win_rate_consistent_with_wins_losses(self):
        closes = [float(100 + i % 10) for i in range(50)]
        candles = _make_candles(closes)
        strat = RSIStrategy(period=5)
        m = run_backtest(strat, candles, [], symbol="TESTUSDT", interval="1h")
        if m.trades > 0:
            assert m.wins + m.losses == m.trades
            assert m.win_rate == pytest.approx(m.wins / m.trades)
        else:
            assert m.win_rate is None
