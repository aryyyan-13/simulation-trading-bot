"""
Run this on YOUR machine (not inside Claude's sandbox) to prove the
connection to real live data actually works.

    pip install -r requirements.txt
    python connectivity_check.py

How to verify it yourself (don't take my word for it):
1. Open https://www.binance.com/en/futures/BTCUSDT in your browser.
2. Compare the "Mark Price" shown there to the mark_price this script prints.
   They should match to within a few seconds of price movement.
3. Check the funding countdown timer on that page against next_funding_time
   printed below.
"""
import datetime

import config
import exchange_client as ex


def main():
    print(f"Connecting to {config.BASE_URL} ({config.EXCHANGE_NAME})...")
    print(f"Symbol: {config.DEFAULT_SYMBOL}\n")

    try:
        mf = ex.get_mark_and_funding(config.DEFAULT_SYMBOL)
    except ex.ExchangeError as exc:
        print(f"FAILED to get a real price: {exc}")
        print("This script does not fall back to a fake price. Fix the connection and retry.")
        raise SystemExit(1)

    next_funding = datetime.datetime.fromtimestamp(mf.next_funding_time_ms / 1000)
    print(f"Mark price:        {mf.mark_price}")
    print(f"Index price:       {mf.index_price}")
    print(f"Last funding rate: {mf.last_funding_rate:.6%}")
    print(f"Next funding time: {next_funding} (local time)")

    book = ex.get_order_book(config.DEFAULT_SYMBOL, limit=10)
    print(f"\nTop of book — best bid: {book.bids[0].price}  best ask: {book.asks[0].price}")
    print(f"(fetched {len(book.bids)} bid levels / {len(book.asks)} ask levels)")

    print("\nNow go compare this to https://www.binance.com/en/futures/" + config.DEFAULT_SYMBOL)


if __name__ == "__main__":
    main()
