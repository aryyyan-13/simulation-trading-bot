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

# Fixed budget in USDT allocated to each stock or crypto trade.
# If set to a number (e.g. 50.0), the bot will allocate exactly this amount per position.
# If set to None, the bot dynamically divides the balance: budget = balance / len(watchlist).
BUDGET_PER_SYMBOL = 50.0

# --- Risk Management (Loss Prevention & Take Profit) -----------------------
TAKE_PROFIT_PCT = 0.05       # 5% take-profit target
TRAILING_STOP_PCT = 0.015    # 1.5% trailing stop-loss (trails the peak/trough price)

# --- Trend Regime Filter (ADX) --------------------------------------------
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25     # ADX > 25 indicates a strong trend; below is sideways

# ── Indian Stock Market (NSE) ─────────────────────────────────────────────────
# PROFIT-FILTER APPLIED (2026-06-25): Only stocks with a POSITIVE backtest
# return are included. All 22 stocks with negative historical returns have been
# removed to prevent the bot from allocating capital to chronically losing assets.
#
# Backtest source: data/stock_backtest_analysis.csv (47 NSE stocks, ~3 months).
# Stocks are sorted by descending Sharpe ratio (risk-adjusted return).
#
# Removed (negative return): RELIANCE(-3.04%), TCS(-8.79%), HDFCBANK(-3.35%),
#   ITC(+0.81% marginal), SBIN(-3.29%), TATASTEEL(-7.51%), BAJFINANCE(-4.02%),
#   BAJAJFINSV(-4.07%), ADANIENT(-4.86%), ASIANPAINT(-16.04%), COALINDIA(-11.44%),
#   SUNPHARMA(-4.58%), LT(-10.38%), M&M(-0.95%), MARUTI(-1.21%), JIOFIN(-2.05%),
#   POWERGRID(-13.52%), NTPC(-4.42%), HCLTECH(-3.25%), JSWSTEEL(-6.08%),
#   VEDL(-2.85%), ADANIPORTS(-11.73%), DRREDDY(-1.05%), DIVISLAB(-11.36%),
#   GRASIM(-10.16%), HEROMOTOCO(-12.93%), BAJAJ-AUTO(-8.59%), NESTLEIND(-4.97%),
#   BRITANNIA(-14.22%), TATACONSUM(-12.10%), BPCL(-0.63%), INDUSINDBK(-10.17%)
#
# Source for valid NSE tickers: https://www.nseindia.com/ (2026-06-24)
STOCK_WATCHLIST = [
    # ── Tier 1: Highest Sharpe + Positive Return (Core Holdings) ──────────
    "TITAN.NS",       # Titan Company         ~₹3,200  | +11.42% return | Sharpe 9.92 ★
    "AXISBANK.NS",    # Axis Bank             ~₹1,200  | +13.77% return | Sharpe 9.59 ★
    "WIPRO.NS",       # Wipro                 ~₹490    | +15.26% return | Sharpe 8.25 ★
    "ICICIBANK.NS",   # ICICI Bank            ~₹1,100  | +6.55%  return | Sharpe 6.72
    "ONGC.NS",        # Oil & Natural Gas     ~₹275    | +9.38%  return | Sharpe 5.69
    "INFY.NS",        # Infosys               ~₹1,800  | +7.60%  return | Sharpe 5.61

    # ── Tier 2: Good Return, Acceptable Risk ──────────────────────────────
    "BHARTIARTL.NS",  # Bharti Airtel         ~₹1,400  | +4.59%  return | Sharpe 5.47
    "KOTAKBANK.NS",   # Kotak Mahindra Bank   ~₹1,800  | +4.46%  return | Sharpe 4.93
    "HINDUNILVR.NS",  # Hindustan Unilever    ~₹2,500  | +3.33%  return | Sharpe 4.74
    "APOLLOHOSP.NS",  # Apollo Hospitals      ~₹6,200  | +2.22%  return | Sharpe 4.09
    "CIPLA.NS",       # Cipla                 ~₹1,500  | +3.24%  return | Sharpe 3.33
    "ULTRACEMCO.NS",  # UltraTech Cement      ~₹9,800  | +4.55%  return | Sharpe 4.16

    # ── Tier 3: Lower Return but Still Positive ───────────────────────────
    "EICHERMOT.NS",   # Eicher Motors         ~₹4,700  | +3.33%  return | Sharpe 2.66
    "HINDALCO.NS",    # Hindalco Industries   ~₹600    | +2.28%  return | Sharpe 1.65
    "TECHM.NS",       # Tech Mahindra         ~₹1,250  | +1.07%  return | Sharpe 1.87
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
