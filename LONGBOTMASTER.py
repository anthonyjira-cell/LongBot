import asyncio
import ccxt.async_support as ccxt
import os
from dotenv import load_dotenv
import sys

load_dotenv()

# === ENVIRONMENT VARIABLES ===
API_KEY = os.getenv("OKX_API_KEY") or "your_api_key_here"
API_SECRET = os.getenv("OKX_API_SECRET") or "your_api_secret_here"
API_PASSWORD = os.getenv("OKX_API_PASSPHRASE") or "your_api_password_here"

print(f"API_KEY: {API_KEY[:4]}***")
print(f"API_SECRET: {API_SECRET[:4]}***")
print(f"API_PASSWORD: {API_PASSWORD[:4]}***")

if not API_KEY or API_KEY == "your_api_key_here" or \
   not API_SECRET or API_SECRET == "your_api_secret_here" or \
   not API_PASSWORD or API_PASSWORD == "your_api_password_here":
    print("❌ ERROR: Missing or invalid API credentials.")
    sys.exit(1)

# === CONFIGURATION ===
SYMBOL = 'BTC/USDT:USDT'
LEVELS = [114618]         # Trigger levels for long
CANDLE_COUNT = 4          # Consecutive candles below trigger
LEVERAGE = 10
MAX_RISK = 100             # Max loss in USD
TP1_PERCENT = 0.0175       # 1.75%
TP2_PERCENT = 0.03         # 3%

# === INIT EXCHANGE ===
exchange = ccxt.okx({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'password': API_PASSWORD,
    'enableRateLimit': True,
    'sandbox': False,
    'options': {
        'defaultType': 'swap',
    }
})

# === HELPER FUNCTIONS ===
async def fetch_candles(limit=CANDLE_COUNT):
    return await exchange.fetch_ohlcv(SYMBOL, timeframe='5m', limit=limit)

def is_confirmed(candles, level):
    return all(c[1] < level and c[4] < level for c in candles)

async def get_last_3h_low():
    """Return the lowest wick of the last 3 hours (36×5m candles)."""
    candles = await fetch_candles(limit=36)
    lows = [c[3] for c in candles]
    return min(lows)

# === BREAKEVEN MONITOR ===
async def monitor_breakeven(entry_price, risk_per_btc, contracts, stop_loss_order_id):
    try:
        target_price_for_breakeven = entry_price + (2 * risk_per_btc)  # ✅ move at 2R
        print(f"📡 Breakeven monitor started. Trigger price: {target_price_for_breakeven}")

        while True:
            ticker = await exchange.fetch_ticker(SYMBOL)
            current_price = ticker['last']

            if current_price >= target_price_for_breakeven:
                print(f"🔄 Price reached 2R profit ({current_price}). Moving SL to {entry_price}.")

                # Cancel old SL
                try:
                    await exchange.cancel_order(stop_loss_order_id, SYMBOL)
                except Exception as e:
                    print(f"⚠️ Could not cancel old SL: {e}")

                # Place new SL at breakeven
                await exchange.create_order(
                    symbol=SYMBOL,
                    type='stop-market',
                    side='sell',
                    amount=contracts,
                    params={'posSide': 'long', 'stopLossPrice': round(entry_price, 2), 'reduceOnly': True}
                )

                print("✅ Stop-loss moved to breakeven.")
                break

            await asyncio.sleep(2)

    except Exception as e:
        print(f"⚠️ Error in breakeven monitor: {e}")

# === TRADE EXECUTION ===
async def place_long_with_tp_sl(entry_price, level):
    try:
        await exchange.set_leverage(LEVERAGE, SYMBOL)

        # Stop loss calculation: lowest wick of last 3 hours - 0.1%
        last_3h_low = await get_last_3h_low()
        sl_price = round(last_3h_low * 0.999, 2)  # 0.1% below
        risk_per_btc = entry_price - sl_price

        print("📊 --- Risk Calculation Details ---")
        print(f"🕐 Last 3h Lowest Wick: {last_3h_low}")
        print(f"🔻 Stop Loss Price: {sl_price}")
        print(f"⚖ Risk per BTC: {risk_per_btc}")
        print(f"💵 Max Allowed Risk: ${MAX_RISK}")

        if risk_per_btc <= 0:
            print("❌ Invalid SL calculation — entry below or equal to SL.")
            return None

        # Calculate size
        trade_size_btc = MAX_RISK / risk_per_btc
        contracts = max(1, round(trade_size_btc / 0.01))
        half_amount = max(1, round(contracts / 2))

        print(f"📏 BTC Size: {trade_size_btc:.6f} BTC")
        print(f"📦 Contracts: {contracts} (Half: {half_amount})")

        # Targets
        tp1_price = round(entry_price * (1 + TP1_PERCENT), 2)
        tp2_price = round(entry_price * (1 + TP2_PERCENT), 2)
        print(f"📈 Entry Price: {entry_price}")
        print(f"🎯 TP1: {tp1_price}, TP2: {tp2_price}")

        # Entry order
        await exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='buy',
            amount=contracts,
            params={'posSide': 'long'}
        )

        # TP orders
        await exchange.create_order(
            symbol=SYMBOL,
            type='limit',
            side='sell',
            amount=half_amount,
            price=tp1_price,
            params={'posSide': 'long', 'reduceOnly': True}
        )
        await exchange.create_order(
            symbol=SYMBOL,
            type='limit',
            side='sell',
            amount=half_amount,
            price=tp2_price,
            params={'posSide': 'long', 'reduceOnly': True}
        )

        # Initial SL
        stop_loss_order = await exchange.create_order(
            symbol=SYMBOL,
            type='stop-market',
            side='sell',
            amount=contracts,
            params={'posSide': 'long', 'stopLossPrice': sl_price, 'reduceOnly': True}
        )

        print("✅ TP/SL orders placed.")
        # Return the breakeven monitor task
        return asyncio.create_task(monitor_breakeven(entry_price, risk_per_btc, contracts, stop_loss_order['id']))

    except Exception as e:
        print(f"❌ Error placing long trade: {e}")
        return None

# === MAIN LOOP ===
async def main():
    print(f"✅ API loaded. Monitoring LONG entries for: {LEVELS} on {SYMBOL}")
    triggered_levels = set()
    breakeven_tasks = []  # track monitors

    while len(triggered_levels) < len(LEVELS):
        try:
            candles = await fetch_candles()
            for level in LEVELS:
                if level in triggered_levels:
                    continue

                if is_confirmed(candles, level):
                    print(f"🔍 Level {level}: {CANDLE_COUNT} candles below")

                    ticker = await exchange.fetch_ticker(SYMBOL)
                    current_price = ticker['last']

                    if current_price < level:
                        print(f"⏳ Waiting for breakout above {level}")
                        while True:
                            ticker = await exchange.fetch_ticker(SYMBOL)
                            current_price = ticker['last']
                            if current_price > level:
                                print(f"🚀 Breakout above {level} detected. Executing long.")
                                task = await place_long_with_tp_sl(current_price, level)
                                if task:
                                    breakeven_tasks.append(task)
                                triggered_levels.add(level)
                                break
                            await asyncio.sleep(1)
                else:
                    print(f"⏳ Level {level}: Not confirmed with {CANDLE_COUNT} candles.")

            await asyncio.sleep(10)

        except Exception as e:
            print(f"⚠️ Error in main loop: {e}")
            await asyncio.sleep(5)

    # ✅ Wait for breakeven monitors to finish before shutdown
    if breakeven_tasks:
        print("⏳ Waiting for all breakeven monitors to finish...")
        await asyncio.gather(*breakeven_tasks)

    print("🎉 All long levels triggered. Bot finished.")
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())


