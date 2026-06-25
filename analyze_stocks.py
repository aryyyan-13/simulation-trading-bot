#!/usr/bin/env python3
"""
Analysis script for the 50 watched NSE Indian stocks.

Downloads the past 30 days of 1-hour candles for all 50 stocks in STOCK_WATCHLIST,
runs a simulated backtest of the multi-timeframe SMA crossover strategy (9-period fast,
21-period slow), applies real Zerodha delivery fee schedules, and ranks the stocks
to identify the best performers to invest in.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import Optional
import yfinance as yf

import config
from engine.stock_fees import compute_stock_buy_fee, compute_stock_sell_fee
from strategies.sma import SMACrossoverStrategy
from strategies.base import Signal

SLIPPAGE_RATE = 0.0005  # 0.05% slippage per execution
POSITION_FRACTION = 0.95  # commit 95% of balance to each trade


@dataclass
class StockBacktestMetrics:
    symbol: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    starting_balance: float
    ending_balance: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float]


def fetch_stock_candles(symbol: str) -> list[dict]:
    """Fetch past 30 days of hourly candles in INR."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="30d", interval="1h")
        if df is None or df.empty:
            return []
        
        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "open_time": int(ts.timestamp() * 1000),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
                "close_time": int(ts.timestamp() * 1000)
            })
        return candles
    except Exception:
        return []


def run_stock_backtest(symbol: str, candles: list[dict]) -> Optional[StockBacktestMetrics]:
    """Run a simulated SMA crossover backtest on a stock in USD."""
    if len(candles) < 22:  # need enough candles for slow SMA (21)
        return None

    # Conversion helper
    def to_usd(inr: float) -> float:
        return inr / config.USD_INR_RATE

    def to_inr(usd: float) -> float:
        return usd * config.USD_INR_RATE

    starting_balance = 10000.0  # start with $10,000 USD
    balance = starting_balance
    
    # Position tracking
    position_qty = 0.0
    position_entry_px_usd = 0.0
    position_side = None  # "LONG" or "SHORT"
    position_fee_paid_usd = 0.0

    trades_closed = 0
    wins = 0
    losses = 0
    balance_history: list[float] = []

    strategy = SMACrossoverStrategy(fast=9, slow=21)

    for i in range(len(candles)):
        close_px_inr = candles[i]["close"]
        close_px_usd = to_usd(close_px_inr)
        
        # Ask strategy for signal
        signal = strategy.generate_signal(candles[:i + 1])
        
        # Handle state changes
        if signal == Signal.LONG:
            # Close SHORT first if open
            if position_side == "SHORT":
                exit_val_inr = to_inr(position_qty * close_px_usd * (1.0 + SLIPPAGE_RATE))
                exit_fee_usd = to_usd(compute_stock_sell_fee(exit_val_inr))
                pnl = (position_entry_px_usd - close_px_usd * (1.0 + SLIPPAGE_RATE)) * position_qty - position_fee_paid_usd - exit_fee_usd
                balance += pnl
                trades_closed += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                position_qty = 0.0
                position_side = None

            # Open LONG if flat
            if position_side is None:
                entry_px_usd = close_px_usd * (1.0 + SLIPPAGE_RATE)
                qty = (balance * POSITION_FRACTION) / entry_px_usd
                entry_val_inr = to_inr(qty * entry_px_usd)
                entry_fee_usd = to_usd(compute_stock_buy_fee(entry_val_inr))
                
                # Check if we can afford the fee
                if balance > entry_fee_usd:
                    balance -= entry_fee_usd
                    position_qty = qty
                    position_entry_px_usd = entry_px_usd
                    position_side = "LONG"
                    position_fee_paid_usd = entry_fee_usd

        elif signal == Signal.SHORT:
            # Close LONG first if open
            if position_side == "LONG":
                exit_val_inr = to_inr(position_qty * close_px_usd * (1.0 - SLIPPAGE_RATE))
                exit_fee_usd = to_usd(compute_stock_sell_fee(exit_val_inr))
                pnl = (close_px_usd * (1.0 - SLIPPAGE_RATE) - position_entry_px_usd) * position_qty - position_fee_paid_usd - exit_fee_usd
                balance += pnl
                trades_closed += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                position_qty = 0.0
                position_side = None

            # Open SHORT if flat
            if position_side is None:
                entry_px_usd = close_px_usd * (1.0 - SLIPPAGE_RATE)
                qty = (balance * POSITION_FRACTION) / entry_px_usd
                entry_val_inr = to_inr(qty * entry_px_usd)
                entry_fee_usd = to_usd(compute_stock_buy_fee(entry_val_inr))
                
                if balance > entry_fee_usd:
                    balance -= entry_fee_usd
                    position_qty = qty
                    position_entry_px_usd = entry_px_usd
                    position_side = "SHORT"
                    position_fee_paid_usd = entry_fee_usd

        elif signal == Signal.CLOSE:
            # Close whatever is open
            if position_side == "LONG":
                exit_val_inr = to_inr(position_qty * close_px_usd * (1.0 - SLIPPAGE_RATE))
                exit_fee_usd = to_usd(compute_stock_sell_fee(exit_val_inr))
                pnl = (close_px_usd * (1.0 - SLIPPAGE_RATE) - position_entry_px_usd) * position_qty - position_fee_paid_usd - exit_fee_usd
                balance += pnl
                trades_closed += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                position_qty = 0.0
                position_side = None
            elif position_side == "SHORT":
                exit_val_inr = to_inr(position_qty * close_px_usd * (1.0 + SLIPPAGE_RATE))
                exit_fee_usd = to_usd(compute_stock_sell_fee(exit_val_inr))
                pnl = (position_entry_px_usd - close_px_usd * (1.0 + SLIPPAGE_RATE)) * position_qty - position_fee_paid_usd - exit_fee_usd
                balance += pnl
                trades_closed += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                position_qty = 0.0
                position_side = None

        balance_history.append(balance)

    # Force close at end
    if position_side == "LONG":
        close_px_usd = to_usd(candles[-1]["close"])
        exit_val_inr = to_inr(position_qty * close_px_usd * (1.0 - SLIPPAGE_RATE))
        exit_fee_usd = to_usd(compute_stock_sell_fee(exit_val_inr))
        pnl = (close_px_usd * (1.0 - SLIPPAGE_RATE) - position_entry_px_usd) * position_qty - position_fee_paid_usd - exit_fee_usd
        balance += pnl
        trades_closed += 1
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        balance_history[-1] = balance
    elif position_side == "SHORT":
        close_px_usd = to_usd(candles[-1]["close"])
        exit_val_inr = to_inr(position_qty * close_px_usd * (1.0 + SLIPPAGE_RATE))
        exit_fee_usd = to_usd(compute_stock_sell_fee(exit_val_inr))
        pnl = (position_entry_px_usd - close_px_usd * (1.0 + SLIPPAGE_RATE)) * position_qty - position_fee_paid_usd - exit_fee_usd
        balance += pnl
        trades_closed += 1
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        balance_history[-1] = balance

    total_return_pct = (balance - starting_balance) / starting_balance * 100.0

    # Max Drawdown
    peak = starting_balance
    max_dd = 0.0
    for b in balance_history:
        peak = max(peak, b)
        dd = (peak - b) / peak * 100.0
        max_dd = max(max_dd, dd)

    # Annualised Sharpe (Hourly interval = 8760 factor)
    sharpe = None
    if len(balance_history) > 2:
        rets = [
            (balance_history[k] - balance_history[k - 1]) / balance_history[k - 1]
            for k in range(1, len(balance_history))
        ]
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
        std_r = math.sqrt(var_r)
        if std_r > 1e-14:
            sharpe = (mean_r / std_r) * math.sqrt(8760)

    win_rate = wins / trades_closed if trades_closed > 0 else 0.0

    return StockBacktestMetrics(
        symbol           = symbol,
        trades           = trades_closed,
        wins             = wins,
        losses           = losses,
        win_rate         = win_rate,
        starting_balance = starting_balance,
        ending_balance   = balance,
        total_return_pct = total_return_pct,
        max_drawdown_pct = max_dd,
        sharpe_ratio     = sharpe
    )


def main() -> None:
    print(f"============================================================")
    print(f"      NSE STOCK STRATEGY PERFORMANCE ANALYSER")
    print(f"============================================================")
    print(f"  Analysing 30-day historical data for {len(config.STOCK_WATCHLIST)} stocks...")
    print(f"  Strategy: SMA Crossover (Fast: 9h, Slow: 21h)")
    print(f"  Includes: Zerodha fees, 0.05% slippage, 1 USD = ₹{config.USD_INR_RATE}")
    print(f"============================================================")

    results: list[StockBacktestMetrics] = []
    
    for idx, symbol in enumerate(config.STOCK_WATCHLIST, 1):
        print(f" [{idx:02d}/{len(config.STOCK_WATCHLIST)}] Fetching & testing {symbol}...", end="", flush=True)
        candles = fetch_stock_candles(symbol)
        if not candles:
            print(" skipped (no data).")
            continue
        
        metrics = run_stock_backtest(symbol, candles)
        if metrics is None:
            print(" skipped (too few candles).")
            continue
        
        results.append(metrics)
        print(f" done. ({metrics.trades} trades, Return: {metrics.total_return_pct:+.2f}%)")

    if not results:
        print("\nERROR: No stock data could be fetched.")
        return

    # Sort by total return percentage descending
    results.sort(key=lambda r: r.total_return_pct, reverse=True)

    # Save results to a CSV datasheet
    import csv
    from pathlib import Path
    analysis_file = Path("data/stock_backtest_analysis.csv")
    analysis_file.parent.mkdir(parents=True, exist_ok=True)
    with open(analysis_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Rank", "Symbol", "Return %", "Trades", "Win Rate %", "Max DD %", "Sharpe Ratio"])
        for rank, r in enumerate(results, 1):
            sharpe_val = f"{r.sharpe_ratio:.2f}" if r.sharpe_ratio is not None else "N/A"
            writer.writerow([
                rank,
                r.symbol,
                f"{r.total_return_pct:+.2f}",
                r.trades,
                f"{r.win_rate * 100:.1f}",
                f"{r.max_drawdown_pct:.2f}",
                sharpe_val
            ])

    print("\n\n" + "=" * 80)
    print(f"  RANKED NSE STOCK WATCHLIST ANALYSIS (30-Day lookback)")
    print("=" * 80)
    print(f"  {'Rank':<4} {'Symbol':<16} {'Return %':<10} {'Trades':<8} {'Win Rate':<10} {'Max DD %':<10} {'Sharpe':<8}")
    print("  " + "─" * 75)
    for rank, r in enumerate(results, 1):
        sharpe_str = f"{r.sharpe_ratio:.2f}" if r.sharpe_ratio is not None else "N/A"
        print(f"  {rank:<4} {r.symbol:<16} {r.total_return_pct:>+8.2f}% {r.trades:>8} {r.win_rate:>9.1%} {r.max_drawdown_pct:>9.2f}% {sharpe_str:>8}")
    print("=" * 80)

    print("\n🏆 TOP 5 STOCKS TO INVEST IN (Based on 30-Day SMA Backtest):")
    for idx, r in enumerate(results[:5], 1):
        sharpe_val = f"{r.sharpe_ratio:.2f}" if r.sharpe_ratio is not None else "N/A"
        print(f"  {idx}. {r.symbol:<15} Return: {r.total_return_pct:>+7.2f}%  |  Win Rate: {r.win_rate:.1%}  |  Sharpe: {sharpe_val}")
    print("=" * 80)
    print("  *DISCLAIMER: Historical performance does not guarantee future returns.")
    print("  SMA crossover is a trend-following strategy; top performers represent")
    print("  assets that had the strongest, cleanest trends over the past 30 days.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
