"""
These tests use a hand-built, clearly-synthetic order book — NOT a claim
about real BTCUSDT prices. The shape (list of [price, qty] levels) matches
Binance's documented depth schema (see SOURCES.md #2), so the math is proven
correct; the actual numbers are just round, easy-to-check test fixtures.
"""
import math

import pytest

import config
from engine.execution import (
    CloseResult,
    Position,
    Side,
    close_position,
    compute_fee,
    compute_funding_payment,
    walk_book,
)
from engine.portfolio import PaperPortfolio


# A synthetic ask book: 3 levels, ascending price, limited size per level.
SYNTHETIC_ASKS = [
    (100.0, 1.0),  # 1 unit available at 100
    (101.0, 1.0),  # next unit at 101
    (102.0, 5.0),  # plenty after that
]
SYNTHETIC_BIDS = [
    (99.0, 1.0),
    (98.0, 1.0),
    (97.0, 5.0),
]


def test_small_order_fills_at_top_of_book():
    result = walk_book(SYNTHETIC_ASKS, qty_needed=0.5)
    assert result.avg_price == pytest.approx(100.0)
    assert result.fully_filled


def test_large_order_walks_multiple_levels_real_slippage():
    # 2.5 units needed: 1@100 + 1@101 + 0.5@102 = (100+101+51)/2.5 = 100.8
    result = walk_book(SYNTHETIC_ASKS, qty_needed=2.5)
    expected_avg = (1 * 100.0 + 1 * 101.0 + 0.5 * 102.0) / 2.5
    assert result.avg_price == pytest.approx(expected_avg)
    assert result.avg_price > 100.0, "a bigger order must pay worse than top-of-book price"
    assert result.fully_filled


def test_order_bigger_than_total_depth_is_never_faked():
    total_depth = sum(q for _, q in SYNTHETIC_ASKS)  # 7.0
    result = walk_book(SYNTHETIC_ASKS, qty_needed=total_depth + 5.0)
    assert not result.fully_filled
    assert result.shortfall == pytest.approx(5.0)
    # the filled portion must still be priced off real levels, not invented
    assert not math.isnan(result.avg_price)


def test_fees_match_verified_regular_user_rates():
    notional = 10_000.0
    assert compute_fee(notional, is_maker=True) == pytest.approx(10_000 * config.MAKER_FEE_RATE)
    assert compute_fee(notional, is_maker=False) == pytest.approx(10_000 * config.TAKER_FEE_RATE)
    # sanity-check against the worked example on Binance's own fee page
    # (SOURCES.md #3): 1 BTC * 10,104 USDT taker -> 5.052 USDT fee
    assert compute_fee(10_104.0, is_maker=False) == pytest.approx(5.052, rel=1e-6)


def test_funding_sign_convention_long_pays_when_positive():
    payment = compute_funding_payment(position_notional=10_000.0, funding_rate=0.0001, side=Side.LONG)
    assert payment < 0  # long pays out


def test_funding_sign_convention_short_receives_when_positive():
    payment = compute_funding_payment(position_notional=10_000.0, funding_rate=0.0001, side=Side.SHORT)
    assert payment > 0  # short receives


def test_funding_flips_when_rate_is_negative():
    long_payment = compute_funding_payment(10_000.0, -0.0001, Side.LONG)
    short_payment = compute_funding_payment(10_000.0, -0.0001, Side.SHORT)
    assert long_payment > 0  # long now receives
    assert short_payment < 0  # short now pays


def test_close_losing_trade_is_truthful_about_fees_and_funding():
    pos = Position(
        symbol="TEST",
        side=Side.LONG,
        qty=1.0,
        entry_price=100.0,
        entry_fee_paid=compute_fee(100.0, is_maker=False),
    )
    pos.funding_paid_total = -0.5  # paid out 0.5 in funding while open

    # price fell to 95 -> this is a losing trade. Exit book:
    losing_bids = [(95.0, 5.0)]
    result = close_position(pos, losing_bids)

    expected_gross = (95.0 - 100.0) * 1.0  # -5.0
    expected_exit_fee = compute_fee(95.0, is_maker=False)
    expected_net = expected_gross - pos.entry_fee_paid - expected_exit_fee + (-0.5)

    assert result.gross_pnl == pytest.approx(expected_gross)
    assert result.net_pnl == pytest.approx(expected_net)
    assert result.net_pnl < result.gross_pnl, (
        "fees and funding must make a loss look WORSE, never better"
    )
    assert any("LOSS" in n for n in result.notes)


def test_close_with_insufficient_exit_depth_leaves_position_honestly_open():
    pos = Position(symbol="TEST", side=Side.LONG, qty=10.0, entry_price=100.0, entry_fee_paid=0.2)
    thin_book = [(95.0, 3.0)]  # only 3 of the 10 units can actually be sold
    result = close_position(pos, thin_book)

    assert result.shortfall == pytest.approx(7.0)
    assert pos.is_open is True
    assert pos.qty == pytest.approx(7.0)
    assert any("PARTIALLY OPEN" in n for n in result.notes)


def test_portfolio_rejects_opening_a_position_it_cant_afford_the_fee_for():
    tiny_portfolio = PaperPortfolio(starting_balance=0.001)
    with pytest.raises(ValueError):
        tiny_portfolio.open_position("BTCUSDT", Side.LONG, qty=10.0, fill_price=100_000.0, is_maker=False)


def test_portfolio_end_to_end_losing_trade_reduces_balance_correctly():
    portfolio = PaperPortfolio(starting_balance=10_000.0)
    pos = portfolio.open_position("BTCUSDT", Side.LONG, qty=1.0, fill_price=100.0, is_maker=False)
    balance_after_open = portfolio.balance
    assert balance_after_open == pytest.approx(10_000.0 - compute_fee(100.0, False))

    portfolio.apply_funding("BTCUSDT", compute_funding_payment(100.0, 0.0001, Side.LONG))
    losing_bids = [(90.0, 5.0)]
    close_result = close_position(pos, losing_bids)
    portfolio.apply_close("BTCUSDT", close_result)

    assert portfolio.balance < balance_after_open, "a real loss must actually reduce the fake balance"
    summary = portfolio.summary()
    assert summary["trades_closed"] == 1
    assert summary["losses"] == 1
    assert summary["wins"] == 0
