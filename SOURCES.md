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
