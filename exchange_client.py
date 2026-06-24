"""
A thin wrapper around Binance's public USDS-M Futures market-data endpoints.

No API key is used or needed anywhere in this file — every call here is a
public market-data endpoint (see SOURCES.md #1 and #2). This file makes real
HTTP requests. It will NOT work inside Claude's sandboxed build environment
(that network is locked down to a small allowlist) — it's meant to be run on
your own machine, where normal internet access works.

If a request fails, this code raises loudly rather than returning fake data.
That's intentional: a silent fallback to a made-up price would violate the
one rule that matters here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests

import config


class ExchangeError(RuntimeError):
    """Raised when we can't get a real answer from the exchange. We never
    substitute a fake value when this happens — the caller must handle it."""


def _get(path: str, params: dict | None = None, timeout: float = 10.0) -> dict | list:
    url = f"{config.BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params or {}, timeout=timeout)
    except requests.RequestException as exc:
        raise ExchangeError(f"Network error calling {url}: {exc}") from exc
    if resp.status_code != 200:
        raise ExchangeError(
            f"Binance returned HTTP {resp.status_code} for {url}: {resp.text[:300]}"
        )
    return resp.json()


@dataclass
class OrderBookLevel:
    price: float
    qty: float


@dataclass
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]  # sorted highest price first
    asks: list[OrderBookLevel]  # sorted lowest price first
    fetched_at: float


@dataclass
class MarkAndFunding:
    symbol: str
    mark_price: float
    index_price: float
    last_funding_rate: float  # already-settled rate published by Binance
    next_funding_time_ms: int
    fetched_at: float


@dataclass
class HistoricalFundingRate:
    symbol: str
    funding_rate: float
    funding_time_ms: int
    mark_price: float


def get_order_book(symbol: str = config.DEFAULT_SYMBOL, limit: int = 100) -> OrderBook:
    """GET /fapi/v1/depth — see SOURCES.md #2."""
    data = _get(config.DEPTH_PATH, {"symbol": symbol, "limit": limit})
    bids = [OrderBookLevel(float(p), float(q)) for p, q in data["bids"]]
    asks = [OrderBookLevel(float(p), float(q)) for p, q in data["asks"]]
    return OrderBook(symbol=symbol, bids=bids, asks=asks, fetched_at=time.time())


def get_mark_and_funding(symbol: str = config.DEFAULT_SYMBOL) -> MarkAndFunding:
    """GET /fapi/v1/premiumIndex — see SOURCES.md #2 and #4."""
    data = _get(config.PREMIUM_INDEX_PATH, {"symbol": symbol})
    return MarkAndFunding(
        symbol=symbol,
        mark_price=float(data["markPrice"]),
        index_price=float(data["indexPrice"]),
        last_funding_rate=float(data["lastFundingRate"]),
        next_funding_time_ms=int(data["nextFundingTime"]),
        fetched_at=time.time(),
    )


def get_klines(
    symbol: str = config.DEFAULT_SYMBOL,
    interval: str = "1h",
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = 500,
) -> list[dict]:
    """GET /fapi/v1/klines — see SOURCES.md #2.

    Returns a list of dicts (open_time, open, high, low, close, volume,
    close_time) instead of Binance's raw nested-array format, so callers
    don't have to remember field order by position.
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    raw = _get(config.KLINES_PATH, params)
    out = []
    for row in raw:
        out.append(
            {
                "open_time": row[0],
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": row[6],
            }
        )
    return out


def get_funding_rate_history(
    symbol: str = config.DEFAULT_SYMBOL,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = 1000,
) -> list[HistoricalFundingRate]:
    """GET /fapi/v1/fundingRate — see SOURCES.md.

    Returns a list of HistoricalFundingRate dataclass objects.
    """
    params = {"symbol": symbol, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    raw = _get("/fapi/v1/fundingRate", params)
    out = []
    for row in raw:
        out.append(
            HistoricalFundingRate(
                symbol=row["symbol"],
                funding_rate=float(row["fundingRate"]),
                funding_time_ms=int(row["fundingTime"]),
                mark_price=float(row.get("markPrice") or 0.0),
            )
        )
    return out
