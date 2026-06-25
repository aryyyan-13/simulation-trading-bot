"""
Zerodha Equity Delivery fee calculator for Indian NSE stocks.

All constants are sourced from config.py, which traces every number back to
zerodha.com/charges (verified 2026-06-24).  This module never invents a fee
value — every component is explicitly named and traceable.

Fee structure for Equity Delivery (Buy):
  STT          = 0.1% of trade value
  Stamp Duty   = 0.015% of trade value (buy side only)
  Exch Charge  = 0.00343% of trade value (NSE)
  SEBI Charge  = 0.0001% of trade value
  GST          = 18% of (Exch Charge + SEBI Charge)   [brokerage = ₹0]
  Total Buy    ≈ 0.1191% of trade value

Fee structure for Equity Delivery (Sell):
  STT          = 0.1% of trade value
  Exch Charge  = 0.00343% of trade value (NSE)
  SEBI Charge  = 0.0001% of trade value
  GST          = 18% of (Exch Charge + SEBI Charge)
  DP Charge    = flat ₹15.93 per scrip sold
  Total Sell   ≈ 0.1041% of trade value + ₹15.93 flat
"""
from __future__ import annotations

import config


def compute_stock_buy_fee(trade_value_inr: float) -> float:
    """Return total buy-side fee in INR for an Indian equity delivery trade.

    Args:
        trade_value_inr: The total trade value in INR (price × qty).

    Returns:
        Total fee in INR (always >= 0).
    """
    stt          = trade_value_inr * config.STOCK_STT_RATE
    stamp        = trade_value_inr * config.STOCK_STAMP_RATE
    exch         = trade_value_inr * config.STOCK_EXCH_RATE
    sebi         = trade_value_inr * config.STOCK_SEBI_RATE
    gst          = (exch + sebi)   * config.STOCK_GST_RATE
    total        = stt + stamp + exch + sebi + gst
    return total


def compute_stock_sell_fee(trade_value_inr: float) -> float:
    """Return total sell-side fee in INR for an Indian equity delivery trade.

    Args:
        trade_value_inr: The total trade value in INR (price × qty).

    Returns:
        Total fee in INR (always >= 0).
    """
    stt          = trade_value_inr * config.STOCK_STT_RATE
    exch         = trade_value_inr * config.STOCK_EXCH_RATE
    sebi         = trade_value_inr * config.STOCK_SEBI_RATE
    gst          = (exch + sebi)   * config.STOCK_GST_RATE
    dp           = config.STOCK_DP_CHARGE_INR          # flat per scrip
    total        = stt + exch + sebi + gst + dp
    return total
