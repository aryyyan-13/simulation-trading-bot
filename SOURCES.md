# Verified Sources

Rule for this project: nothing in `config.py` or the engine is allowed to be a
guess. Every number/endpoint below was checked against an official page on
2026-06-17. If Binance changes something later, this file is what you update
first — then the code.

## Market chosen: Binance USDⓈ-M Futures (perpetual, public data)

Why this market (not forex, not spot): you asked the engine to model real
**funding**, not just fees and slippage. Funding is a specific, exchange-published
number on crypto perpetual futures — there's no equivalent free, no-signup,
real-time data source for forex. Crypto futures markets are also open 24/7, so
the dashboard always has something live to show.

## 1. Public market data needs no account or API key
- Binance Spot "Market Data Only" doc confirms public endpoints require no
  authentication: https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only
- Same pattern holds for USDⓈ-M Futures: an MCP server built directly against
  this API lists ping/get_ticker/get_order_book/get_klines under
  "Market Data (no authentication required)":
  https://glama.ai/mcp/servers/Muvon/mcp-binance-futures
- Official Futures API base URL: `https://fapi.binance.com`

## 2. Endpoints used (all public, no key)
| Purpose | Method & Path | Official doc |
|---|---|---|
| Live mark price + last settled funding rate | `GET /fapi/v1/premiumIndex` | https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price |
| Order book (used for honest slippage) | `GET /fapi/v1/depth` | https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly (shows the exact call: `fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000`) |
| Best bid/ask | `GET /fapi/v1/ticker/bookTicker` | https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Order-Book-Ticker |
| Historical candles (for backtesting) | `GET /fapi/v1/klines` | https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data |
| Funding rate history (for backtesting) | `GET /fapi/v1/fundingRate` | https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History |

### 2a. Verified Schema details for Backtesting Endpoints (Checked: 2026-06-18)

#### **Historical Candles (GET /fapi/v1/klines)**
*   **Parameters**: `symbol` (string), `interval` (string, e.g., '1h'), `startTime` (long, optional), `endTime` (long, optional), `limit` (int, optional, max 1500).
*   **Response Array Structure** (nested list where each kline has indices):
    *   Index 0: Open time (long, ms)
    *   Index 1: Open price (string)
    *   Index 2: High price (string)
    *   Index 3: Low price (string)
    *   Index 4: Close price (string)
    *   Index 5: Volume (string)
    *   Index 6: Close time (long, ms)
    *   Index 7: Quote asset volume (string)
    *   Index 8: Number of trades (int)
    *   Index 9: Taker buy base asset volume (string)
    *   Index 10: Taker buy quote asset volume (string)
    *   Index 11: Ignore (string)

#### **Funding Rate History (GET /fapi/v1/fundingRate)**
*   **Parameters**: `symbol` (string, optional), `startTime` (long, optional), `endTime` (long, optional), `limit` (int, optional, max 1000).
*   **Response Object Structure**: List of objects containing:
    *   `symbol` (string)
    *   `fundingRate` (string)
    *   `fundingTime` (long, ms)
    *   `markPrice` (string, the mark price at settlement time)

## 3. Fees — Regular User, USDⓈ-M Futures
Official page, last updated 2026-05-01:
https://www.binance.com/en/support/faq/detail/360033544231
> "a Regular User's maker fee is 0.02% and a Regular User's taker fee is 0.05%"
> Trading Fee = Position Value × trading fee rate; Position Value = Contract Size × execution price.

Used in `config.py`: `MAKER_FEE_RATE = 0.0002`, `TAKER_FEE_RATE = 0.0005`.
(Note: real fees can be lower with BNB payment or VIP volume tiers — we use the
worst-case regular-user rate, which is the honest default for someone starting out.)

## 4. Funding mechanism
Official page: https://www.binance.com/en/support/faq/360033525031
("Introduction to Binance Futures Funding Rates")
- Funding is exchanged every 8 hours.
- Mechanism (corroborated independently by CoinGlass's explainer, since the
  exact payment formula isn't restated verbatim on every Binance page):
  **Funding Fee = Position Notional Value × Funding Rate at settlement.**
  https://www.coinglass.com/learn/how-to-judge-market-by-fr-en
- When the rate is positive, longs pay shorts; when negative, shorts pay longs.

Design decision: rather than re-implementing Binance's internal premium-index
averaging formula ourselves (which needs 5-second internal ticks we don't have
historically), the engine reads Binance's own **already-settled**
`lastFundingRate` field from `/fapi/v1/premiumIndex` and applies the formula
above. This means our funding numbers are never an approximation — they're the
exchange's own published value.

## 5. Known limitation, disclosed honestly
This Claude instance's code sandbox cannot reach `fapi.binance.com` (confirmed
by direct test: `host_not_allowed`). So:
- Endpoint paths and schemas above are verified from official docs, not from a
  live call I made myself.
- Unit tests in `tests/` use a **hand-built, clearly-labeled synthetic** order
  book/kline payload shaped exactly like the documented schema — this proves
  the math is correct, but it is not a live network test.
- The real live-data connection must be run on your machine. `connectivity_check.py`
  is provided so you can verify, yourself, that the price it prints matches
  what you see on binance.com right now.

## 6. Open items not yet verified (do not assume these — flagged honestly)
- Leverage / maintenance-margin / liquidation-price tiers: real and
  tier-based, not yet pulled from official docs. Until verified, the engine
  defaults to **1x leverage only**, so there is no liquidation math to get wrong.
  This will be verified before any leverage feature is added.
- Exact current numeric values of the 30-day VIP fee tiers above Regular User:
  not needed yet since we use Regular User rates only.

---

## Indian Stock Market Integration (Added 2026-06-24)

### 7. NSE / BSE Market Hours
Official source: https://www.nseindia.com/
- Trading hours: Monday–Friday, 9:15 AM to 3:30 PM Indian Standard Time (IST).
- Market is closed on Indian public holidays. The official annual holiday
  calendar is published by NSE before the start of each year at nseindia.com.
- The 2026 holiday list used in `stock_client.py` is a best-effort set of
  standard statutory holidays. **Verify against the official NSE 2026 holiday
  list at nseindia.com before relying on it.**

### 8. Zerodha Equity Delivery Fee Schedule
Official page: https://zerodha.com/charges (verified 2026-06-24)
> Equity Delivery: Brokerage = ₹0, STT = 0.1% on buy & sell,
> Stamp Duty = 0.015% on buy side only, NSE Exchange Transaction Charge = 0.00343%,
> SEBI Charges = 0.0001%, GST = 18% on (Exchange + SEBI charges),
> DP Charges = ₹13.5 + GST = ₹15.93 flat per scrip on sell.
Used in `config.py`:
```
STOCK_STT_RATE      = 0.001       # 0.1%
STOCK_STAMP_RATE    = 0.00015     # 0.015% (buy only)
STOCK_EXCH_RATE     = 0.0000343   # 0.00343% NSE
STOCK_SEBI_RATE     = 0.000001    # 0.0001%
STOCK_GST_RATE      = 0.18        # 18%
STOCK_DP_CHARGE_INR = 15.93       # ₹15.93 flat per sell
```

### 9. Stock Data Source — yfinance
Library: https://pypi.org/project/yfinance/
- An open-source Python library that scrapes Yahoo Finance.
- **Honest disclosure**: NSE stock data via Yahoo Finance has an
  exchange-mandated delay of approximately 15 minutes. It is NOT true
  real-time data. This is acceptable for a paper-trading system that signals
  on hourly and daily candles, but should be disclosed.
- NSE stocks use the `.NS` suffix in Yahoo Finance ticker format, e.g.,
  `RELIANCE.NS`, `TCS.NS`. Source: https://finance.yahoo.com/
- yfinance is unofficial and may break if Yahoo changes its internal API.
  Raise `StockClientError` loudly on failure — never substitute a fake price.

### 10. News Feed Sources (Free RSS, No API Key Required)
- Economic Times — Market/Stocks RSS:
  https://economictimes.indiatimes.com/markets/stocks/rss.cms
- Moneycontrol — Latest News RSS:
  https://www.moneycontrol.com/rss/latestnews.xml
- Parsed using the `feedparser` Python library: https://pypi.org/project/feedparser/
- **Honest disclosure**: The news scanner does keyword matching only — it is
  NOT semantic analysis or AI prediction. A negative headline mentioning
  "Reliance" will still flag Reliance as a candidate. The SMA trend signal
  is the actual guard for trade entry.
