import os
import sys
import yfinance as yf
import pandas as pd

# Ensure imports work from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_pipeline.indicators import TechnicalIndicators
from agents.screener_agent import ScreenerAgent
from core.config import TARGET_MULTIPLIER, STOP_LOSS_MULTIPLIER

def run_backtest(days_to_test=10):
    print(f"🚀 Starting Backtest for the last {days_to_test} trading days...")
    
    # Core liquid stocks for the simulation
    tickers = ["BBCA.JK", "BBRI.JK", "ADRO.JK", "ITMG.JK", "ASII.JK", "AMMN.JK", "BRPT.JK"]
    print("Fetching historical data...")
    
    # Fetch 3 months of data to ensure moving averages (MACD) have time to warm up
    raw_data = yf.download(tickers, period="3mo", group_by='ticker', progress=False)
    
    # Clean and align the data dates
    market_data = {}
    for ticker in tickers:
        df = raw_data[ticker].copy().dropna()
        if not df.empty:
            market_data[ticker] = df

    # 🆕 Safety net: Check if Yahoo gave us empty data
    if not market_data:
        print("❌ Error: No data fetched. Yahoo Finance is rate-limiting you.")
        print("Take a 10-15 minute coffee break and try running it again!")
        return

    # Find the common trading dates across these stocks
    # Use the first available ticker we actually successfully downloaded
    first_available_ticker = list(market_data.keys())[0]
    common_dates = market_data[first_available_ticker].index
    
    if len(common_dates) < days_to_test + 1:
        print("Not enough historical data to run the backtest.")
        return

    agent = ScreenerAgent()
    
    wins = 0
    losses = 0
    misses = 0

    # Step through history, stopping 1 day before the end (so we can check the 'next day' result)
    test_dates = common_dates[-(days_to_test + 1):-1]

    for i, current_date in enumerate(test_dates):
        print(f"\n--- Simulating Day: {current_date.strftime('%Y-%m-%d')} ---")
        
        candidates = []
        for ticker, df in market_data.items():
            # Hide the future: only look at data up to the 'current_date'
            historical_slice = df.loc[:current_date].copy()
            if len(historical_slice) < 10:
                continue
                
            historical_slice = TechnicalIndicators.add_all_indicators(historical_slice)
            
            today = historical_slice.iloc[-1]
            prev_days = historical_slice.iloc[-6:-1] # 5 days prior
            
            avg_vol = prev_days['Volume'].mean()
            if avg_vol == 0: continue
            
            vol_surge = today['Volume'] / avg_vol
            
            candidates.append({
                "ticker": ticker.replace(".JK", ""),
                "close_price": float(today['Close']),
                "volume_surge_ratio": round(float(vol_surge), 2),
                "RSI": float(today.get('RSI', 0)),
                "MACD_Hist": float(today.get('MACD_Hist', 0))
            })
            
        # Sort by volume surge and take top 5
        candidates.sort(key=lambda x: x['volume_surge_ratio'], reverse=True)
        top_candidates = candidates[:5]
        
        # Agent makes a decision
        decision = agent.analyze_candidates(top_candidates)
        
        if decision.get("action") == "BUY":
            target = decision['entry_price'] * TARGET_MULTIPLIER
            stop = decision['entry_price'] * STOP_LOSS_MULTIPLIER
            print(f"🤖 Agent says: BUY {decision['ticker']} @ {decision['entry_price']}")
            
            # Check the "Future" (The next trading day)
            next_date = common_dates[common_dates.get_loc(current_date) + 1]
            future_df = market_data[decision['ticker'] + ".JK"]
            next_day_action = future_df.loc[next_date]
            
            high_price = next_day_action['High']
            low_price = next_day_action['Low']
            
            if high_price >= target:
                print(f"🎯 RESULT: WIN! Hit target of {target} (Next day high: {high_price})")
                wins += 1
            elif low_price <= stop:
                print(f"🛑 RESULT: LOSS. Stopped out at {stop} (Next day low: {low_price})")
                losses += 1
            else:
                print(f"➖ RESULT: FLAT. Didn't hit target or stop. Closed at {next_day_action['Close']}")
                misses += 1
        else:
            print("ℹ️ Agent passed. No clear setups.")

    # Final Report
    print("\n===============================")
    print("🏆 BACKTEST COMPLETE")
    print(f"Total Trades: {wins + losses + misses}")
    print(f"Wins: {wins} | Losses: {losses} | Flat: {misses}")
    if (wins + losses) > 0:
        win_rate = (wins / (wins + losses)) * 100
        print(f"Win Rate (excluding flat): {win_rate:.1f}%")
    print("===============================")

if __name__ == "__main__":
    run_backtest(days_to_test=10)