import asyncio
import ccxt.async_support as ccxt
import os
from dotenv import load_dotenv

load_dotenv()

# === CONFIGURATION ===
LEVEL = 114431        # breakout level to watch
RISK_AMOUNT = 150     # max $ risk per trade
SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "5m"

# === API CREDENTIALS ===
API_KEY = os.getenv("OKX_API_KEY")
API_SECRET = os.getenv("OKX_API_SECRET")
API_PASSWORD = os.getenv("OKX_API_PASSWORD")

exchange = ccxt.okx({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "password": API_PASSWORD,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

# === Helper Functions ===
async def get_last_n_candles(n=3):
    return await exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=n)

def three_candles_below_level(candles, level):
    for ts, o, h, l, c, v in candles:  # <- 6 elements unpack
        if h >= level:  # wick or body touches/exceeds level
            return False
    return True

async def get_lowest_wick_last_3h():
    candles = await exchange.fetch_ohlcv(SYMBOL, timeframe="5m", limit=36)
    lows = [l for ts, o, h, l, c, v in candles]  # use low/wick
    return min(lows)

# === Main Bot ===
async def main():
    print(f"👀 Watching breakout level {LEVEL} with {TIMEFRAME} candles...")

    # --- Wait for 3 candles below level ---
    while True:
        candles = await get_last_n_candles(3)
        if three_candles_below_level(candles, LEVEL):
            ticker = await exchange.fetch_ticker(SYMBOL)
            last_price = ticker["last"]

            if last_price > LEVEL:
                print(f"🚀 Breakout above {LEVEL}! Entering long at {last_price}")
                entry_price = last_price
                break
        await asyncio.sleep(10)

    # --- Stop loss ---
    lowest_wick = await get_lowest_wick_last_3h()
    stop_loss = lowest_wick * 0.999
    risk_per_btc = entry_price - stop_loss
    btc_size = RISK_AMOUNT / risk_per_btc
    contracts = round(btc_size * entry_price / 100, 0)
    half_contracts = contracts // 2 if contracts > 1 else 1

    print(f"📊 Position sizing:")
    print(f"   Entry: {entry_price}")
    print(f"   SL: {stop_loss}")
    print(f"   Risk per BTC: {risk_per_btc}")
    print(f"   Contracts: {contracts} (half: {half_contracts})")

    # --- Place long market order ---
    await exchange.create_order(
        SYMBOL, "market", "buy", contracts, None,
        {"posSide": "long"}
    )

    # --- Place initial stop loss ---
    stop_order = await exchange.create_order(
        SYMBOL, "stop_market", "sell", contracts, None,
        {
            "posSide": "long",
            "stopLossPrice": stop_loss,
            "reduceOnly": True
        }
    )

    # --- TP logic ---
    tp2 = entry_price + 2 * risk_per_btc
    tp3 = entry_price + 3 * risk_per_btc

    breakeven_activated = False
    trailing_activated = False

    while True:
        ticker = await exchange.fetch_ticker(SYMBOL)
        last_price = ticker["last"]

        # 2R logic
        if not breakeven_activated and last_price >= tp2:
            print(f"✅ 2R reached! Taking half profit + moving SL to breakeven.")
            await exchange.create_order(
                SYMBOL, "market", "sell", half_contracts, None,
                {"posSide": "long", "reduceOnly": True}
            )
            await exchange.cancel_order(stop_order["id"], SYMBOL)
            stop_order = await exchange.create_order(
                SYMBOL, "stop_market", "sell", half_contracts, None,
                {"posSide": "long", "stopLossPrice": entry_price, "reduceOnly": True}
            )
            breakeven_activated = True

        # 3R logic
        if breakeven_activated and not trailing_activated and last_price >= tp3:
            print(f"🏁 3R reached! Activating trailing stop (0.3%).")
            await exchange.cancel_order(stop_order["id"], SYMBOL)
            await exchange.create_order(
                SYMBOL, "market", "sell", half_contracts, None,
                {
                    "posSide": "long",
                    "reduceOnly": True,
                    "trailingStop": True,
                    "callbackRate": 0.3
                }
            )
            trailing_activated = True

        await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(exchange.close())
