from datetime import datetime

from config import *


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def bullish_structure(df, lookback):
    highs = df["high"].tail(lookback).values
    lows = df["low"].tail(lookback).values

    if len(highs) < 3 or len(lows) < 3:
        return False

    higher_high = highs[-1] > highs[-3]
    higher_low = lows[-1] > lows[-3]
    return higher_high and higher_low


def bearish_structure(df, lookback):
    highs = df["high"].tail(lookback).values
    lows = df["low"].tail(lookback).values

    if len(highs) < 3 or len(lows) < 3:
        return False

    lower_high = highs[-1] < highs[-3]
    lower_low = lows[-1] < lows[-3]
    return lower_high and lower_low


def strong_bull_candle(candle):
    body = abs(candle["close"] - candle["open"])
    full = candle["high"] - candle["low"]

    if full == 0:
        return False

    return candle["close"] > candle["open"] and (body / full) >= MIN_BODY_RATIO


def strong_bear_candle(candle):
    body = abs(candle["close"] - candle["open"])
    full = candle["high"] - candle["low"]

    if full == 0:
        return False

    return candle["close"] < candle["open"] and (body / full) >= MIN_BODY_RATIO


def ai_signal(df, use_news_filter=True, verbose=True):
    if df is None or len(df) < 60:
        return None

    if use_news_filter and datetime.utcnow().hour in NEWS_BLOCK_HOURS:
        if verbose:
            print("NEWS TIME")
        return None

    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW).mean()
    df["rsi"] = calculate_rsi(df["close"], RSI_PERIOD)
    df["range"] = df["high"] - df["low"]

    atr_current = df["range"].rolling(14).mean().iloc[-1]
    atr_average = df["range"].rolling(50).mean().iloc[-1]

    if atr_current <= 0 or atr_average <= 0:
        return None

    if atr_current < (atr_average * MIN_VOLATILITY_MULTIPLIER):
        if verbose:
            print("LOW VOL")
        return None

    if atr_current > (atr_average * MAX_VOLATILITY_MULTIPLIER):
        if verbose:
            print("HIGH VOL")
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    ema_fast = last["ema_fast"]
    ema_slow = last["ema_slow"]
    close = last["close"]
    rsi = last["rsi"]
    trend_strength = abs(ema_fast - ema_slow)

    if trend_strength < (atr_current * 0.05):
        if verbose:
            print("WEAK TREND")
        return None

    bullish = bullish_structure(df, STRUCTURE_LOOKBACK)
    bearish = bearish_structure(df, STRUCTURE_LOOKBACK)
    pullback_buy = prev["close"] < prev["ema_fast"]
    pullback_sell = prev["close"] > prev["ema_fast"]

    if (
        bullish
        and ema_fast > ema_slow
        and close > ema_fast
        and pullback_buy
        and strong_bull_candle(last)
        and rsi < RSI_BUY_MAX
    ):
        if verbose:
            print(f"PRO BUY | RSI {round(rsi, 2)}")
        return "BUY"

    if (
        bearish
        and ema_fast < ema_slow
        and close < ema_fast
        and pullback_sell
        and strong_bear_candle(last)
        and rsi > RSI_SELL_MIN
    ):
        if verbose:
            print(f"PRO SELL | RSI {round(rsi, 2)}")
        return "SELL"

    return None
