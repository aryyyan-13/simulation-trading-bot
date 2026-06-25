"""
Indian Stock Market (NSE) data client.

Wraps yfinance to fetch live prices and OHLCV candles for NSE-listed stocks
(tickers with the `.NS` suffix, e.g. "RELIANCE.NS").

Honest disclosures (see SOURCES.md):
  - yfinance is an unofficial, free library that scrapes Yahoo Finance data.
  - Yahoo Finance data for NSE stocks is subject to a ~15-minute exchange delay.
  - If data cannot be fetched, this module raises StockClientError loudly —
    it never substitutes a fake price.
  - Market hours guard is based on IST (Asia/Kolkata).  Standard NSE holidays
    for 2026 are statically listed.  The NSE publishes the full holiday list
    annually at nseindia.com; update this file at the start of each year.

Source for NSE market hours: https://www.nseindia.com/
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# ── NSE trading holidays for 2026 ─────────────────────────────────────────────
# Source: NSE official holiday list (nseindia.com) — update each January.
# These are CLOSING holidays (market fully closed all day).
_NSE_HOLIDAYS_2026: set[datetime.date] = {
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 26),   # Holi (provisional — check NSE official list)
    datetime.date(2026, 4, 3),    # Good Friday (provisional)
    datetime.date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day / Labour Day
    datetime.date(2026, 8, 15),   # Independence Day
    datetime.date(2026, 10, 2),   # Gandhi Jayanti
    datetime.date(2026, 10, 23),  # Dussehra (provisional)
    datetime.date(2026, 11, 11),  # Diwali Laxmi Puja (provisional)
    datetime.date(2026, 11, 12),  # Diwali Balipratipada (provisional)
    datetime.date(2026, 11, 25),  # Gurunanak Jayanti (provisional)
    datetime.date(2026, 12, 25),  # Christmas Day
}


class StockClientError(RuntimeError):
    """Raised when NSE stock data cannot be fetched.  Never substitutes fake data."""


def is_nse_market_open(dt: Optional[datetime.datetime] = None) -> bool:
    """Return True if NSE is currently open for trading.

    Args:
        dt: Datetime to check (IST-aware).  Defaults to now.

    Returns:
        True if Mon–Fri, 9:15 AM – 3:30 PM IST, and not a listed holiday.
    """
    if dt is None:
        dt = datetime.datetime.now(_IST)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)

    # Weekends
    if dt.weekday() >= 5:
        return False

    # Market hours: 9:15 AM – 3:30 PM IST
    market_open  = dt.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = dt.replace(hour=15, minute=30, second=0, microsecond=0)
    if not (market_open <= dt <= market_close):
        return False

    # Official NSE holidays
    if dt.date() in _NSE_HOLIDAYS_2026:
        return False

    return True


def get_nse_price(symbol: str) -> float:
    """Fetch the most recent NSE closing price for a stock, in INR.

    Uses yfinance fast_info which returns the last available price without
    downloading a full history payload.

    Args:
        symbol: NSE ticker with .NS suffix, e.g. "RELIANCE.NS".

    Returns:
        Last price in INR (float).

    Raises:
        StockClientError if the price cannot be fetched.
    """
    if not _YF_AVAILABLE:
        raise StockClientError(
            "yfinance is not installed. Run: pip install yfinance>=0.2.40"
        )

    try:
        ticker = yf.Ticker(symbol)
        price  = ticker.fast_info.last_price
        if price is None or price <= 0:
            raise StockClientError(
                f"yfinance returned no valid price for {symbol}. "
                "Market may be closed or ticker is invalid."
            )
        return float(price)
    except StockClientError:
        raise
    except Exception as exc:
        raise StockClientError(
            f"Could not fetch NSE price for {symbol}: {exc}"
        ) from exc


def get_nse_klines(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    """Fetch OHLCV candles for an NSE stock using yfinance.

    The returned format matches exchange_client.get_klines() so the existing
    signal_engine.compute_signals() works without modification.

    Args:
        symbol:   NSE ticker, e.g. "RELIANCE.NS".
        interval: yfinance interval string ("1h", "1d", "1wk").
                  NOTE: yfinance uses "1wk" for weekly — we convert automatically.
        limit:    Number of candles to return.

    Returns:
        List of dicts with keys: open_time (ms int), open, high, low, close,
        volume (floats), close_time (ms int).

    Raises:
        StockClientError if data cannot be fetched.
    """
    if not _YF_AVAILABLE:
        raise StockClientError(
            "yfinance is not installed. Run: pip install yfinance>=0.2.40"
        )

    # Map our interval names to yfinance interval strings
    _INTERVAL_MAP = {
        "1h":  "1h",
        "1d":  "1d",
        "1w":  "1wk",   # yfinance uses "1wk" for weekly bars
        "1wk": "1wk",
    }
    yf_interval = _INTERVAL_MAP.get(interval, interval)

    # Determine the period to download based on limit + interval
    _PERIOD_MAP = {
        "1h":  "30d",    # ~720 hours available; 30 days gives plenty
        "1d":  "120d",   # 120 trading days
        "1wk": "3y",     # ~3 years of weekly bars
    }
    period = _PERIOD_MAP.get(yf_interval, "60d")

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=yf_interval)

        if df is None or df.empty:
            raise StockClientError(
                f"yfinance returned no data for {symbol} at interval={yf_interval}. "
                "Market may be closed or ticker is invalid."
            )

        candles = []
        for ts, row in df.iterrows():
            # ts is a pandas Timestamp; convert to UTC ms
            open_time_ms  = int(ts.timestamp() * 1000)
            close_time_ms = open_time_ms   # yfinance doesn't give close_time; use open_time
            candles.append({
                "open_time":  open_time_ms,
                "open":       float(row["Open"]),
                "high":       float(row["High"]),
                "low":        float(row["Low"]),
                "close":      float(row["Close"]),
                "volume":     float(row["Volume"]),
                "close_time": close_time_ms,
            })

        # Return only the most recent `limit` candles
        return candles[-limit:]

    except StockClientError:
        raise
    except Exception as exc:
        raise StockClientError(
            f"Could not fetch NSE klines for {symbol} [{yf_interval}]: {exc}"
        ) from exc
