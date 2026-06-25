"""
Verified configuration constants.

Every value here must trace back to SOURCES.md. Do not hand-edit a number
without first updating SOURCES.md with the link you verified it against.
"""

EXCHANGE_NAME = "Binance USDS-M Futures (public market data, no account needed)"
BASE_URL = "https://fapi.binance.com"

DEFAULT_SYMBOL = "BTCUSDT"

# --- Watchlist -----------------------------------------------------------
# The master list of perpetual futures symbols the bot is allowed to monitor
# and trade. The bot evaluates every symbol on every cycle and only enters a
# position when the 1h + 1d SMA signals agree. You can add or remove symbols
# from this list at any time; the state file adapts automatically.
#
# All symbols here must be valid USDⓈ-M perpetual futures on Binance.
# Verified to exist via /fapi/v1/exchangeInfo (public, no key required).
# Source: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

# --- Risk Management (Stop-Loss) ----------------------------------------
# Maximum loss allowed on any single open position, expressed as a fraction
# of the entry notional value (i.e. entry_price * qty).
#
#   STOP_LOSS_PCT = 0.02  →  close automatically if position loses ≥2%
#   STOP_LOSS_PCT = None  →  no stop-loss (position held until signal flips)
#
# Honest disclosure: a stop-loss limits the per-trade downside but can cause
# repeated small losses (whipsaw) in choppy, sideways markets. Every
# stop-loss close is logged and recorded in the CSV datasheet so nothing
# is hidden.
STOP_LOSS_PCT = 0.02   # 2% stop-loss per position

# --- Fees -----------------------------------------------------------------
# Source: https://www.binance.com/en/support/faq/detail/360033544231
# "Binance Futures Fee Structure & Fee Calculations" (updated 2026-05-01)
# Regular User (VIP 0), USDS-Margined Futures:
MAKER_FEE_RATE = 0.0002  # 0.02%
TAKER_FEE_RATE = 0.0005  # 0.05%

# --- Funding --------------------------------------------------------------
# Source: https://www.binance.com/en/support/faq/360033525031
FUNDING_INTERVAL_HOURS = 8
# Funding Fee = Position Notional Value * Funding Rate (see SOURCES.md #4).
# We use Binance's own settled `lastFundingRate`, never a re-derived guess.

# --- Public endpoints (no API key required) ------------------------------
# See SOURCES.md #2 for the official doc backing each path.
KLINES_PATH        = "/fapi/v1/klines"
DEPTH_PATH         = "/fapi/v1/depth"
PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
BOOK_TICKER_PATH   = "/fapi/v1/ticker/bookTicker"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"

# --- Paper account --------------------------------------------------------
STARTING_FAKE_BALANCE_USDT = 10_000.0

# Leverage is fixed at 1x until liquidation/maintenance-margin tiers are
# verified against an official source (see SOURCES.md #6). Do not raise this
# until that's done — doing so would mean simulating liquidations with
# unverified math, which breaks the "never fake a number" rule.
MAX_LEVERAGE = 1

# ── Indian Stock Market (NSE) ─────────────────────────────────────────────────
# The master list of NSE-listed stocks the bot is allowed to monitor and trade.
# Use the Yahoo Finance ticker format: NSE symbols end with ".NS".
# Additional stocks discovered via news headlines are merged in at runtime.
#
# These are liquid, high-volume NSE stocks spanning a wide price range —
# lower-priced stocks (e.g. TATASTEEL) will automatically result in larger
# share quantities being bought for the same budget ("bulk buying").
#
# Source for valid NSE tickers: https://www.nseindia.com/ (2026-06-24)
STOCK_WATCHLIST = [
    "RELIANCE.NS",    # Reliance Industries   ~₹3,000
    "TCS.NS",         # Tata Consultancy Svcs ~₹4,000
    "HDFCBANK.NS",    # HDFC Bank             ~₹1,900
    "INFY.NS",        # Infosys               ~₹1,800
    "TATASTEEL.NS",   # Tata Steel            ~₹165   → higher qty per budget
    "ITC.NS",         # ITC Ltd               ~₹490
    "SBIN.NS",        # State Bank of India   ~₹790
    "ONGC.NS",        # Oil & Natural Gas     ~₹275   → higher qty per budget
]

# ── Currency conversion (INR → USD) ──────────────────────────────────────────
# All portfolio accounting is in USD to unify crypto and stock capital.
# Update this rate whenever the INR/USD rate drifts significantly.
# Approximate rate as of 2026-06-24: 1 USD ≈ 83.50 INR
USD_INR_RATE = 83.50

# ── Indian Stock Delivery Fee Schedule ───────────────────────────────────────
# Source: https://zerodha.com/charges  (verified 2026-06-24)
# Zerodha Equity Delivery (NSE):
STOCK_STT_RATE      = 0.001       # 0.1%  STT on both buy and sell
STOCK_STAMP_RATE    = 0.00015     # 0.015% Stamp Duty on BUY side only
STOCK_EXCH_RATE     = 0.0000343   # 0.00343% NSE exchange transaction charge
STOCK_SEBI_RATE     = 0.000001    # 0.0001% SEBI turnover fee
STOCK_GST_RATE      = 0.18        # 18% GST on (exchange + SEBI charges)
STOCK_DP_CHARGE_INR = 15.93       # ₹15.93 flat per scrip on SELL (DP charges)
# Note: Brokerage = ₹0 for Equity Delivery at Zerodha.

# ── News Scanner Configuration ────────────────────────────────────────────────
# When enabled, the bot scans RSS feeds from Economic Times and Moneycontrol
# every NEWS_SCAN_INTERVAL_CYCLES polling cycles and adds any NSE-listed
# companies found in the headlines to the effective monitoring pool.
# The news does NOT auto-trigger trades — the SMA signal still decides entry.
NEWS_SCAN_ENABLED        = True
NEWS_SCAN_INTERVAL_CYCLES = 10    # Scan news once every 10 cycles (every ~10 min)
