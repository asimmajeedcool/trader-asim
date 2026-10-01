import os
import time
import requests
from datetime import datetime, timezone

BINANCE = "https://api.binance.com"
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TARGETS = [3, 6, 12, 24]


def telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()


def binance(path, params=None):
    r = requests.get(
        BINANCE + path,
        params=params or {},
        timeout=20
    )
    r.raise_for_status()
    return r.json()


def get_server_time():
    data = binance("/api/v3/time")
    return datetime.fromtimestamp(
        data["serverTime"] / 1000,
        tz=timezone.utc
    )


def get_symbols():
    data = binance("/api/v3/exchangeInfo")

    symbols = []

    for s in data["symbols"]:
        if (
            s["status"] == "TRADING"
            and s["quoteAsset"] == "USDT"
            and s["isSpotTradingAllowed"]
        ):
            symbols.append(s["symbol"])

    return symbols


def get_klines(symbol, interval, limit=250):
    return binance(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )


def closed_candles(klines):
    now_ms = int(time.time() * 1000)

    return [
        k for k in klines
        if k[6] < now_ms
    ]


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = sum(values[:period]) / period

    for price in values[period:]:
        value = (price - value) * multiplier + value

    return value


def rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def relative_volume(klines, period=20):
    if len(klines) < period + 1:
        return None

    volumes = [
        float(k[5])
        for k in klines[-period - 1:-1]
    ]

    average = sum(volumes) / len(volumes)

    if average == 0:
        return None

    return float(klines[-1][5]) / average


def swing_high(klines, index):
    if index < 2 or index + 2 >= len(klines):
        return False

    high = float(klines[index][2])

    return (
        high > float(klines[index - 1][2])
        and high > float(klines[index - 2][2])
        and high > float(klines[index + 1][2])
        and high > float(klines[index + 2][2])
    )


def swing_low(klines, index):
    if index < 2 or index + 2 >= len(klines):
        return False

    low = float(klines[index][3])

    return (
        low < float(klines[index - 1][3])
        and low < float(klines[index - 2][3])
        and low < float(klines[index + 1][3])
        and low < float(klines[index + 2][3])
    )


def bullish_regime():
    candles = closed_candles(
        get_klines("BTCUSDT", "4h", 250)
    )

    if len(candles) < 210:
        return False

    closes = [float(k[4]) for k in candles]

    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes)

    if ema50 is None or ema200 is None or rsi14 is None:
        return False

    if not (ema50 > ema200 and rsi14 >= 55):
        return False

    highs = []
    lows = []

    last_index = len(candles) - 3

    for i in range(2, last_index + 1):
        if swing_high(candles, i):
            highs.append(float(candles[i][2]))

        if swing_low(candles, i):
            lows.append(float(candles[i][3]))

    if len(highs) < 2 or len(lows) < 2:
        return False

    higher_high = highs[-1] > highs[-2]
    higher_low = lows[-1] > lows[-2]

    return higher_high and higher_low


def trigger(symbol):
    candles = closed_candles(
        get_klines(symbol, "15m", 120)
    )

    if len(candles) < 30:
        return None

    closes = [float(k[4]) for k in candles]

    current = closes[-1]
    rsi14 = rsi(closes)

    rv = relative_volume(candles)

    if rsi14 is None or rv is None:
        return None

    if rsi14 < 50 or rv < 1.0:
        return None

    swing_highs = []

    for i in range(2, len(candles) - 2):
        if swing_high(candles, i):
            swing_highs.append(float(candles[i][2]))

    if not swing_highs:
        return None

    resistance = swing_highs[-1]

    if current <= resistance:
        return None

    distance = ((current - resistance) / resistance) * 100

    if distance > 1.0:
        return None

    return {
        "entry": current,
        "rsi": rsi14,
        "volume": rv,
        "resistance": resistance
    }


def scan_symbol(symbol):
    try:
        result = trigger(symbol)

        if not result:
            return None

        entry = result["entry"]

        targets = [
            entry * 1.03,
            entry * 1.06,
            entry * 1.12,
            entry * 1.24
        ]

        return {
            "symbol": symbol,
            "entry": entry,
            "targets": targets,
            "rsi": result["rsi"],
            "volume": result["volume"]
        }

    except Exception:
        return None


def send_signal(signal):
    symbol = signal["symbol"]
    entry = signal["entry"]
    targets = signal["targets"]

    message = f"""
<b>🚨 TRADER ASIM — BUY SIGNAL</b>

<b>Pair:</b> {symbol}
<b>Setup:</b> 15M Trigger

<b>Entry:</b> {entry:.8f}

<b>Targets</b>
T1: {targets[0]:.8f}  (+3%)
T2: {targets[1]:.8f}  (+6%)
T3: {targets[2]:.8f}  (+12%)
T4: {targets[3]:.8f}  (+24%)

<b>RSI:</b> {signal["rsi"]:.2f}
<b>Relative Volume:</b> {signal["volume"]:.2f}x

⚠️ Alert system only — verify the setup before trading.
"""

    telegram(message)


def main():
    now = get_server_time()

    print("Trader Asim scanner started:", now.isoformat())

    symbols = get_symbols()

    print("USDT spot pairs:", len(symbols))

    # First production test: monitor liquid symbols only.
    # This prevents excessive Binance API requests.
    symbols = symbols[:50]

    if not bullish_regime():
        print("4H regime: NO BUY")
        return

    print("4H regime: BULLISH")

    found = 0

    for symbol in symbols:
        signal = scan_symbol(symbol)

        if signal:
            print("SIGNAL:", symbol)
            send_signal(signal)
            found += 1

        time.sleep(0.15)

    print("Signals sent:", found)


if __name__ == "__main__":
    main()
