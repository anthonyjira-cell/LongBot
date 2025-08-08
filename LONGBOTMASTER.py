import asyncio
import ccxt.async_support as ccxt
import os
from dotenv import load_dotenv
import sys

load_dotenv()

# === ENVIRONMENT VARIABLES or fallback hardcoded credentials ===
API_KEY = os.getenv("OKX_API_KEY") or "your_api_key_here"
API_SECRET = os.getenv("OKX_API_SECRET") or "your_api_secret_here"
API_PASSWORD = os.getenv("OKX_API_PASSPHRASE") or "your_api_password_here"

# Debug prints to confirm credentials loaded
print(f"API_KEY: {API_KEY[:4]}***")
print(f"API_SECRET: {API_SECRET[:4]}***")
print(f"API_PASSWORD: {API_PASSWORD[:4]}***")

# Exit if any credential is missing or placeholder
if not API_KEY or API_KEY == "your_api_key_here" or \
   not API_SECRET or API_SECRET == "your_api_secret_here" or \
   not API_PASSWORD or API_PASSWORD == "your_api_password_here":
    print("❌ ERROR: Missing or invalid API credentials. Please set them in .env or hardcode before running.")
    sys.exit(1)

# === CONFIGURATION ===
SYMBOL = 'BTC/USDT:USDT'
LEVELS = [116259]  # Your target long level
CANDLE_COUNT = 4
TRADE_SIZE_BTC = 0.15  # Desired position size in BTC
LEVERAGE = 10

TP1_PERCENT = 0.0175
TP2_PERCENT = 0.03
STOP_LOSS_PERCENT = 0.005

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

# (rest of your script unchanged...)

async def fetch_candles():
    candles = await exchange.fetch_ohlcv(SYMBOL, timeframe='5m', limit=CANDLE_COUNT)
    return candles  # full candles

def is_confirmed(candles, level):
    return all(candle[1] < level and candle[4] < level for candle in candles)  # wick + body below

async def place_long_with_tp_sl(entry_price, level):
    try:
        await exchange.set_leverage(LEVERAGE, SYMBOL)

        tp1_price = round(entry_price * (1 + TP1_PERCENT), 2)
        tp2_price = round(entry_price * (1 + TP2_PERCENT), 2)
        sl_price = round(entry_price * (1 - STOP_LOSS_PERCENT), 2)

        contract_value = entry_price * 0.01  # Each contract = 0.01 BTC
        contracts = max(1, round(TRADE_SIZE_BTC / 0.01))  # Always use full contracts
        half_amount = max(1, round(contracts / 2))

        usd_value = TRADE_SIZE_BTC * entry_price
        print(f"📈 Longing {contracts} contracts (~{TRADE_SIZE_BTC} BTC = ${usd_value:.0f}) at ~{entry_price} triggered by level {level}")

        # Entry
        order = await exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='buy',
            amount=contracts,
            params={'posSide': 'long'}
        )
        print(f"✅ Entry order placed: {order}")

        # TP1
        await exchange.create_order(
            symbol=SYMBOL,
            type='limit',
            side='sell',
            amount=half_amount,
            price=tp1_price,
            params={'posSide': 'long', 'reduceOnly': True}
        )

        # TP2
        await exchange.create_order(
            symbol=SYMBOL,
            type='limit',
            side='sell',
            amount=half_amount,
            price=tp2_price,
            params={'posSide': 'long', 'reduceOnly': True}
        )

        # SL
        await exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=contracts,
            params={'posSide': 'long', 'stopLossPrice': sl_price, 'reduceOnly': True}
        )

        print(f"🎯 TP1 at {tp1_price}, TP2 at {tp2_price}, SL at {sl_price}")
        print(f"✅ TP/SL orders placed.")
    except ccxt.BaseError as e:
        print(f"❌ Error placing long trade for level {level}: {e}")
        if hasattr(e, 'response'):
            print(f"Response from API: {e.response}")

async def main():
    print(f"✅ API loaded. Monitoring LONG entries for: {LEVELS} on {SYMBOL}")
    triggered_levels = set()

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

                        # Wait for breakout
                        while True:
                            ticker = await exchange.fetch_ticker(SYMBOL)
                            current_price = ticker['last']
                            if current_price > level:
                                print(f"🚀 Breakout above {level} detected. Executing long.")
                                await place_long_with_tp_sl(current_price, level)
                                triggered_levels.add(level)
                                break
                            await asyncio.sleep(1)
                else:
                    print(f"⏳ Level {level}: Not confirmed with {CANDLE_COUNT} candles.")
            await asyncio.sleep(10)
        except Exception as e:
            print(f"⚠️ Error in main loop: {e}")
            await asyncio.sleep(5)

    print("🎉 All long levels triggered. Bot finished.")
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
