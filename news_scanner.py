"""
News scanner for Indian stock markets.

Polls free RSS feeds from Economic Times and Moneycontrol to discover which
NSE-listed companies are mentioned in today's financial headlines.  Any
company found in the headlines is added to a "news candidate" pool that the
live trader can add to its effective stock watchlist for extra monitoring.

IMPORTANT HONEST DISCLOSURES:
- This is keyword matching on headline text, NOT semantic analysis.
- A negative headline ("Reliance reports loss") will still flag Reliance.
- The news candidates are ONLY monitored — they do not automatically trigger
  a trade. Actual trade entry still requires the SMA signal to agree (same
  as any other symbol).
- These are free public RSS feeds — no API key required.

Sources:
  Economic Times: https://economictimes.indiatimes.com/markets/stocks/rss.cms
  Moneycontrol:   https://www.moneycontrol.com/rss/latestnews.xml
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

try:
    import feedparser
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── RSS feed URLs ─────────────────────────────────────────────────────────────
_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
    "https://www.moneycontrol.com/rss/latestnews.xml",
]

# ── Known NSE companies: lower-case keyword → yfinance ticker ─────────────────
# This lookup table maps commonly-used company names / abbreviations found in
# Indian financial headlines to their NSE yfinance ticker symbols.
# Source: NSE official symbol list (nseindia.com) — tickers verified 2026-06-24.
KNOWN_COMPANIES: dict[str, str] = {
    "reliance":       "RELIANCE.NS",
    "ril":            "RELIANCE.NS",
    "tcs":            "TCS.NS",
    "tata consultancy": "TCS.NS",
    "infosys":        "INFY.NS",
    "infy":           "INFY.NS",
    "hdfc bank":      "HDFCBANK.NS",
    "hdfcbank":       "HDFCBANK.NS",
    "hdfc":           "HDFCBANK.NS",
    "icici bank":     "ICICIBANK.NS",
    "icicibank":      "ICICIBANK.NS",
    "sbi":            "SBIN.NS",
    "state bank":     "SBIN.NS",
    "wipro":          "WIPRO.NS",
    "itc":            "ITC.NS",
    "tatasteel":      "TATASTEEL.NS",
    "tata steel":     "TATASTEEL.NS",
    "ongc":           "ONGC.NS",
    "maruti":         "MARUTI.NS",
    "maruti suzuki":  "MARUTI.NS",
    "bajaj finance":  "BAJFINANCE.NS",
    "bajaj finserv":  "BAJAJFINSV.NS",
    "asian paints":   "ASIANPAINT.NS",
    "kotak":          "KOTAKBANK.NS",
    "kotak mahindra": "KOTAKBANK.NS",
    "hul":            "HINDUNILVR.NS",
    "hindustan unilever": "HINDUNILVR.NS",
    "ultratech":      "ULTRACEMCO.NS",
    "ultratech cement": "ULTRACEMCO.NS",
    "adani":          "ADANIENT.NS",
    "adani enterprises": "ADANIENT.NS",
    "ntpc":           "NTPC.NS",
    "power grid":     "POWERGRID.NS",
    "sun pharma":     "SUNPHARMA.NS",
    "sun pharmaceutical": "SUNPHARMA.NS",
    "drreddy":        "DRREDDY.NS",
    "dr reddy":       "DRREDDY.NS",
    "ltimindtree":    "LTIM.NS",
    "l&t":            "LT.NS",
    "larsen":         "LT.NS",
    "larsen & toubro": "LT.NS",
    "airtel":         "BHARTIARTL.NS",
    "bharti airtel":  "BHARTIARTL.NS",
    "zomato":         "ZOMATO.NS",
    "paytm":          "PAYTM.NS",
    "nykaa":          "NYKAA.NS",
    "dmart":          "DMART.NS",
    "avenue supermarts": "DMART.NS",
    "tata motors":    "TATAMOTORS.NS",
    "tatamotors":     "TATAMOTORS.NS",
    "m&m":            "M&M.NS",
    "mahindra":       "M&M.NS",
    "hero motocorp":  "HEROMOTOCO.NS",
    "hero":           "HEROMOTOCO.NS",
    "bajaj auto":     "BAJAJ-AUTO.NS",
    "cipla":          "CIPLA.NS",
    "divis":          "DIVISLAB.NS",
    "divis labs":     "DIVISLAB.NS",
    "jswsteel":       "JSWSTEEL.NS",
    "jsw steel":      "JSWSTEEL.NS",
    "hindalco":       "HINDALCO.NS",
    "vedanta":        "VEDL.NS",
    "coal india":     "COALINDIA.NS",
    "coalindia":      "COALINDIA.NS",
    "titan":          "TITAN.NS",
    "nestle":         "NESTLEIND.NS",
    "britannia":      "BRITANNIA.NS",
    "dabur":          "DABUR.NS",
    "godrej":         "GODREJCP.NS",
}

# ── Cache so we don't hammer the feeds on every 60-second cycle ───────────────
_last_scan_time:   float        = 0.0
_last_candidates:  list[str]    = []
_CACHE_TTL_SECS:   int          = 3600          # re-fetch RSS at most once per hour


def scan_news_candidates() -> list[str]:
    """Return a deduplicated list of NSE tickers found in today's headlines.

    Uses a 1-hour in-process cache so the feeds are not polled on every
    60-second trading cycle.

    Returns:
        List of ticker strings, e.g. ["RELIANCE.NS", "INFY.NS"].
        Returns [] if feedparser is not installed or feeds are unreachable.
    """
    global _last_scan_time, _last_candidates

    if not _FEEDPARSER_AVAILABLE:
        logger.warning(
            "[NEWS] feedparser is not installed. Run: pip install feedparser>=6.0"
        )
        return []

    now = time.time()
    if now - _last_scan_time < _CACHE_TTL_SECS and _last_candidates:
        return list(_last_candidates)

    found: set[str] = set()

    for feed_url in _FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                title = (entry.get("title") or "").lower()
                summary = (entry.get("summary") or "").lower()
                text = title + " " + summary
                for keyword, ticker in KNOWN_COMPANIES.items():
                    # Require the keyword to appear as a whole word (not a sub-string)
                    pattern = r"\b" + re.escape(keyword) + r"\b"
                    if re.search(pattern, text):
                        found.add(ticker)
        except Exception as exc:         # noqa: BLE001
            logger.warning(f"[NEWS] Could not fetch {feed_url}: {exc}")

    _last_candidates = sorted(found)
    _last_scan_time  = now

    if _last_candidates:
        logger.info(f"[NEWS] {len(_last_candidates)} stock candidates from headlines: "
                    f"{_last_candidates}")
    else:
        logger.info("[NEWS] No new stock candidates found in headlines this cycle.")

    return list(_last_candidates)


def get_effective_stock_watchlist(base_watchlist: list[str]) -> list[str]:
    """Merge the base stock watchlist with any news-discovered tickers.

    Tickers already in base_watchlist are not duplicated.

    Args:
        base_watchlist: The static STOCK_WATCHLIST from config.py.

    Returns:
        Deduplicated list of tickers to monitor this session.
    """
    candidates = scan_news_candidates()
    combined   = list(base_watchlist)
    for ticker in candidates:
        if ticker not in combined:
            combined.append(ticker)
    return combined
