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
LEVELS = [118100]        # Trigger level(s) for long
CANDLE_COUNT = 4         # Consecutive candles below trigger
LEVERAGE = 10
MAX_RISK = 150            # Max loss in USD

TP1_PERCENT = 0.0175     # 1.75%
TP2_PERCENT = 0.03       # 3%

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
    # All candles (open and close) AND wicks below the level
    return all(c[1] < level and c[4] < level for c in candles)

async def get_last_hour_low():
    candles = await fetch_candles(limit=12)  # 12 x 5min = 1 hour
    lows = [c[3] for c in candles]           # c[3] = low wick
    return min(lows)

# === TRADE EXECUTION ===
async def place_long_with_tp_sl(entry_price, level):
    try:
        await exchange.set_leverage(LEVERAGE, SYMBOL)

        # 1. Determine last hour's lowest wick & set SL
        last_hour_low = await get_last_hour_low()
        sl_price = round(last_hour_low * 0.999, 2)  # 0.1% below low
        risk_per_btc = entry_price - sl_price

        if risk_per_btc <= 0:
            print("❌ Invalid SL calculation — entry below or equal to SL.")
            return

        # 2. Calculate BTC size to risk MAX_RISK
        trade_size_btc = MAX_RISK / risk_per_btc
        contracts = max(1, round(trade_size_btc / 0.01))  # OKX: 1 contract = 0.01 BTC
        half_amount = max(1, round(contracts / 2))

        tp1_price = round(entry_price * (1 + TP1_PERCENT), 2)
        tp2_price = round(entry_price * (1 + TP2_PERCENT), 2)
        usd_value = trade_size_btc * entry_price

        print(f"📈 Longing {contracts} contracts (~{trade_size_btc:.4f} BTC = ${usd_value:.2f}) at {entry_price}")
        print(f"🎯 TP1 at {tp1_price}, TP2 at {tp2_price}, SL at {sl_price} (Risk: ${MAX_RISK})")

        # Entry
        await exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='buy',
            amount=contracts,
            params={'posSide': 'long'}
        )

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
            type='stop-market',
            side='sell',
            amount=contracts,
            params={'posSide': 'long', 'stopLossPrice': sl_price, 'reduceOnly': True}
        )

        print("✅ TP/SL orders placed.")
    except ccxt.BaseError as e:
        print(f"❌ Error placing long trade: {e}")
        if hasattr(e, 'response'):
            print(f"Response from API: {e.response}")

# === MAIN LOOP ===
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
