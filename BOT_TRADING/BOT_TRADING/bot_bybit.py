import os
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

import pandas as pd

from ai_model import ai_signal, calculate_rsi
from config import *
from notifier import notify
from utils import log

try:
    from pybit.unified_trading import HTTP
except ImportError:
    HTTP = None


balance = 0
profit = 0
running = False
last_signal = "NONE"
equity_history = []

settings = {
    "risk": RISK,
    "max_trades": MAX_TRADES,
    "ai": True,
}

session = None
instrument_cache = {}
forced_test_done = False
order_cooldowns = {}
last_trade_bar = {}
tracked_positions = {}
notified_closed_keys = set()
recorded_closed_keys = set()
last_closed_pnl_sync = 0
last_telegram_update_id = None
daily_stats = {
    "date": None,
    "start_balance": None,
    "realized_pnl": 0.0,
    "closed_trades": 0,
    "wins": 0,
    "losses": 0,
    "consecutive_losses": 0,
    "summary_sent": False,
}
monthly_stats = {
    "month": None,
    "start_balance": None,
    "realized_pnl": 0.0,
    "closed_trades": 0,
    "wins": 0,
    "losses": 0,
}


def env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def get_session():
    global session

    if session is not None:
        return session

    if HTTP is None:
        raise RuntimeError("pybit is not installed. Run: pip install pybit")

    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")

    if not api_key or not api_secret:
        raise RuntimeError("Missing BYBIT_API_KEY or BYBIT_API_SECRET")

    session = HTTP(
        testnet=env_bool("BYBIT_TESTNET", True),
        demo=env_bool("BYBIT_DEMO", False),
        api_key=api_key,
        api_secret=api_secret,
    )
    return session


def get_symbols():
    raw = os.getenv("BYBIT_SYMBOLS")
    if not raw:
        return BYBIT_SYMBOLS
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


def get_category():
    return os.getenv("BYBIT_CATEGORY", BYBIT_CATEGORY)


def get_quote_coin():
    return os.getenv("BYBIT_QUOTE_COIN", BYBIT_QUOTE_COIN)


def is_dry_run():
    return env_bool("BYBIT_DRY_RUN", True)


def is_demo_mode():
    return env_bool("BYBIT_DEMO", False)


def should_force_demo_order():
    return env_bool("BYBIT_FORCE_DEMO_ORDER", False)


def get_force_side():
    side = os.getenv("BYBIT_FORCE_SIDE", "BUY").upper()
    return side if side in ("BUY", "SELL") else "BUY"


def get_strategy_mode():
    return os.getenv("BYBIT_STRATEGY_MODE", "strict").lower()


def get_confirm_interval():
    return os.getenv("BYBIT_CONFIRM_INTERVAL", "15")


def get_order_cooldown_seconds():
    try:
        return int(os.getenv("BYBIT_ORDER_COOLDOWN_SECONDS", "300"))
    except ValueError:
        return 300


def get_float_env(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def get_be_trigger_atr():
    return get_float_env("BYBIT_BE_TRIGGER_ATR", 0.6)


def get_be_lock_atr():
    return get_float_env("BYBIT_BE_LOCK_ATR", 0.1)


def get_bybit_sl_atr():
    return get_float_env("BYBIT_SL_ATR", ATR_SL_MULTIPLIER)


def get_bybit_tp_atr():
    return get_float_env("BYBIT_TP_ATR", ATR_TP_MULTIPLIER)


def get_min_adx():
    return get_float_env("BYBIT_MIN_ADX", 18)


def get_max_runtime_risk():
    return get_float_env("BYBIT_MAX_RISK_PCT", 1.0)


def get_account_size():
    return get_float_env("BYBIT_ACCOUNT_SIZE_USDT", BYBIT_ACCOUNT_SIZE_USDT)


def get_fixed_qty():
    return get_float_env("BYBIT_FIXED_QTY", BYBIT_FIXED_QTY)


def get_effective_balance():
    account_size = get_account_size()
    if account_size <= 0:
        return balance
    if balance <= 0:
        return account_size
    return min(balance, account_size)


def is_trade_guard_enabled():
    return env_bool("BYBIT_TRADE_GUARD", False)


def get_monthly_max_loss_pct():
    return get_float_env("BYBIT_MONTHLY_MAX_LOSS_PCT", 5.0)


def get_min_monthly_win_rate():
    return get_float_env("BYBIT_MIN_MONTHLY_WIN_RATE", 35.0)


def get_monthly_win_rate_check_trades():
    try:
        return int(os.getenv("BYBIT_MONTHLY_WIN_RATE_CHECK_TRADES", "20"))
    except ValueError:
        return 20


def get_max_consecutive_losses():
    try:
        return int(os.getenv("BYBIT_MAX_CONSECUTIVE_LOSSES", "3"))
    except ValueError:
        return 3


def local_now():
    timezone_name = os.getenv("DAILY_SUMMARY_TZ", "Europe/Rome")
    try:
        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        return datetime.now(ZoneInfo("Europe/Rome"))


def local_timezone():
    timezone_name = os.getenv("DAILY_SUMMARY_TZ", "Europe/Rome")
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("Europe/Rome")


def get_stats_start_at():
    raw = os.getenv("BYBIT_STATS_START_AT") or os.getenv("BYBIT_STATS_START_DATE")
    if not raw:
        return None

    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError:
        log(f"INVALID BYBIT_STATS_START_AT: {raw}")
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=local_timezone())

    return value


def get_daily_summary_time():
    try:
        hour = int(os.getenv("DAILY_SUMMARY_HOUR", "23"))
        minute = int(os.getenv("DAILY_SUMMARY_MINUTE", "59"))
    except ValueError:
        hour = 23
        minute = 59

    return max(0, min(hour, 23)), max(0, min(minute, 59))


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_money(value):
    return f"{value:.2f} {get_quote_coin()}"


def api_ok(response):
    return isinstance(response, dict) and response.get("retCode") == 0


def get_instrument(symbol):
    if symbol in instrument_cache:
        return instrument_cache[symbol]

    response = get_session().get_instruments_info(category=get_category(), symbol=symbol)
    if not api_ok(response):
        raise RuntimeError(f"Instrument info failed {symbol}: {response}")

    items = response.get("result", {}).get("list", [])
    if not items:
        raise RuntimeError(f"Instrument not found: {symbol}")

    item = items[0]
    lot_filter = item.get("lotSizeFilter", {})
    price_filter = item.get("priceFilter", {})

    meta = {
        "qty_step": Decimal(str(lot_filter.get("qtyStep", "0.001"))),
        "min_qty": Decimal(str(lot_filter.get("minOrderQty", "0.001"))),
        "max_qty": Decimal(
            str(lot_filter.get("maxMktOrderQty") or lot_filter.get("maxOrderQty", "1000000"))
        ),
        "tick_size": Decimal(str(price_filter.get("tickSize", "0.01"))),
    }
    instrument_cache[symbol] = meta
    return meta


def quantize_down(value, step):
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def fmt_decimal(value):
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_price(symbol, price):
    meta = get_instrument(symbol)
    value = quantize_down(price, meta["tick_size"])
    return fmt_decimal(value)


def normalize_qty(symbol, qty):
    meta = get_instrument(symbol)
    value = quantize_down(qty, meta["qty_step"])

    max_qty = meta["max_qty"]
    env_max_qty = os.getenv("BYBIT_MAX_QTY")
    if env_max_qty:
        max_qty = min(max_qty, Decimal(str(env_max_qty)))

    value = max(meta["min_qty"], min(value, max_qty))
    return fmt_decimal(value)


def in_order_cooldown(symbol):
    last_attempt = order_cooldowns.get(symbol)
    if last_attempt is None:
        return False
    return (time.time() - last_attempt) < get_order_cooldown_seconds()


def start_order_cooldown(symbol):
    order_cooldowns[symbol] = time.time()


def get_data(symbol, interval=None, limit=100):
    response = get_session().get_kline(
        category=get_category(),
        symbol=symbol,
        interval=interval or os.getenv("BYBIT_INTERVAL", BYBIT_INTERVAL),
        limit=limit,
    )

    if not api_ok(response):
        log(f"KLINE FAILED {symbol}: {response}")
        return None

    rows = response.get("result", {}).get("list", [])
    if not rows:
        log(f"No kline data: {symbol}")
        return None

    df = pd.DataFrame(
        rows,
        columns=["time", "open", "high", "low", "close", "volume", "turnover"],
    )
    df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="ms")

    for column in ["open", "high", "low", "close", "volume", "turnover"]:
        df[column] = pd.to_numeric(df[column])

    df = df.sort_values("time").reset_index(drop=True)
    df["symbol"] = symbol
    return df


def compute_atr(df, period=14):
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean().iloc[-1]


def calculate_adx(df, period=14):
    if df is None or len(df) < period * 2 + 5:
        return None

    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down

    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    tr_sum = true_range.rolling(period).sum()
    plus_di = 100 * (plus_dm.rolling(period).sum() / tr_sum)
    minus_di = 100 * (minus_dm.rolling(period).sum() / tr_sum)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(period).mean().iloc[-1]

    if pd.isna(adx):
        return None

    return float(adx)


def candle_body_ratio(candle):
    full = candle["high"] - candle["low"]
    if full <= 0:
        return 0
    return abs(candle["close"] - candle["open"]) / full


def htf_trend(symbol):
    htf = get_data(symbol, interval=get_confirm_interval(), limit=160)
    if htf is None or len(htf) < 80:
        return None, "no htf data"

    htf = htf.copy()
    htf["ema_fast"] = htf["close"].ewm(span=21).mean()
    htf["ema_slow"] = htf["close"].ewm(span=55).mean()

    last = htf.iloc[-1]
    prev = htf.iloc[-5]
    slope = last["ema_fast"] - prev["ema_fast"]

    if last["ema_fast"] > last["ema_slow"] and slope > 0:
        return "BUY", "htf trend up"

    if last["ema_fast"] < last["ema_slow"] and slope < 0:
        return "SELL", "htf trend down"

    return None, "htf flat"


def relaxed_signal(df):
    if df is None or len(df) < 60:
        return None, "not enough data"

    data = df.copy()
    data["ema_fast"] = data["close"].ewm(span=9).mean()
    data["ema_slow"] = data["close"].ewm(span=21).mean()
    data["rsi"] = calculate_rsi(data["close"], RSI_PERIOD)
    data["range"] = data["high"] - data["low"]

    atr_current = data["range"].rolling(14).mean().iloc[-1]
    atr_average = data["range"].rolling(50).mean().iloc[-1]

    if pd.isna(atr_current) or pd.isna(atr_average) or atr_current <= 0 or atr_average <= 0:
        return None, "invalid atr"

    if atr_current < (atr_average * 0.25):
        return None, "very low volatility"

    last = data.iloc[-1]
    prev = data.iloc[-2]

    close = last["close"]
    prev_close = prev["close"]
    ema_fast = last["ema_fast"]
    ema_slow = last["ema_slow"]
    rsi = last["rsi"]

    if pd.isna(rsi):
        return None, "invalid rsi"

    trend_up = ema_fast > ema_slow and close > ema_fast
    trend_down = ema_fast < ema_slow and close < ema_fast
    momentum_up = close > prev_close
    momentum_down = close < prev_close

    if trend_up and momentum_up and 42 <= rsi <= 72:
        return "BUY", f"trend up rsi={round(rsi, 1)}"

    if trend_down and momentum_down and 28 <= rsi <= 58:
        return "SELL", f"trend down rsi={round(rsi, 1)}"

    return None, (
        f"trend_up={trend_up} trend_down={trend_down} "
        f"rsi={round(rsi, 1)} atr={round(atr_current, 3)}"
    )


def quality_signal(df):
    if df is None or len(df) < 80:
        return None, "not enough data"

    symbol = df["symbol"].iloc[-1]
    confirm_side, confirm_reason = htf_trend(symbol)

    if confirm_side is None:
        return None, confirm_reason

    data = df.copy()
    data["ema_fast"] = data["close"].ewm(span=21).mean()
    data["ema_slow"] = data["close"].ewm(span=55).mean()
    data["rsi"] = calculate_rsi(data["close"], RSI_PERIOD)
    data["range"] = data["high"] - data["low"]

    atr_current = data["range"].rolling(14).mean().iloc[-1]
    atr_average = data["range"].rolling(50).mean().iloc[-1]
    adx = calculate_adx(data)

    if (
        pd.isna(atr_current)
        or pd.isna(atr_average)
        or atr_current <= 0
        or atr_average <= 0
        or adx is None
    ):
        return None, "invalid indicators"

    if adx < get_min_adx():
        return None, f"adx too low {round(adx, 1)}"

    if atr_current < atr_average * 0.45:
        return None, "low volatility"

    if atr_current > atr_average * 2.4:
        return None, "volatility spike"

    last = data.iloc[-1]
    prev = data.iloc[-2]
    rsi = last["rsi"]
    body_ratio = candle_body_ratio(last)

    if pd.isna(rsi):
        return None, "invalid rsi"

    local_buy = (
        confirm_side == "BUY"
        and last["ema_fast"] > last["ema_slow"]
        and last["close"] > last["ema_fast"]
        and prev["low"] <= prev["ema_fast"]
        and last["close"] > prev["high"]
        and last["close"] > last["open"]
        and body_ratio >= 0.35
        and 46 <= rsi <= 66
    )

    if local_buy:
        return "BUY", f"{confirm_reason} pullback breakout rsi={round(rsi, 1)} adx={round(adx, 1)}"

    local_sell = (
        confirm_side == "SELL"
        and last["ema_fast"] < last["ema_slow"]
        and last["close"] < last["ema_fast"]
        and prev["high"] >= prev["ema_fast"]
        and last["close"] < prev["low"]
        and last["close"] < last["open"]
        and body_ratio >= 0.35
        and 34 <= rsi <= 54
    )

    if local_sell:
        return "SELL", f"{confirm_reason} pullback breakout rsi={round(rsi, 1)} adx={round(adx, 1)}"

    return None, (
        f"{confirm_reason} no setup rsi={round(rsi, 1)} "
        f"adx={round(adx, 1)} body={round(body_ratio, 2)}"
    )


def strong_htf_trend(symbol):
    checks = []

    for interval in ("15", "60"):
        htf = get_data(symbol, interval=interval, limit=180)
        if htf is None or len(htf) < 90:
            return None, f"sniper no {interval}m data"

        data = htf.copy()
        data["ema_fast"] = data["close"].ewm(span=21).mean()
        data["ema_slow"] = data["close"].ewm(span=55).mean()
        data["rsi"] = calculate_rsi(data["close"], RSI_PERIOD)
        adx = calculate_adx(data)

        last = data.iloc[-1]
        prev = data.iloc[-8]
        slope = last["ema_fast"] - prev["ema_fast"]

        if adx is None or adx < max(get_min_adx(), 22):
            return None, f"sniper {interval}m adx too low"

        if last["ema_fast"] > last["ema_slow"] and slope > 0 and last["close"] > last["ema_fast"]:
            checks.append("BUY")
        elif last["ema_fast"] < last["ema_slow"] and slope < 0 and last["close"] < last["ema_fast"]:
            checks.append("SELL")
        else:
            return None, f"sniper {interval}m trend not clean"

    if checks[0] == checks[1]:
        return checks[0], "sniper 15m/60m aligned"

    return None, "sniper htf disagreement"


def close_near_extreme(candle, side):
    full = candle["high"] - candle["low"]
    if full <= 0:
        return False

    if side == "BUY":
        return ((candle["high"] - candle["close"]) / full) <= 0.25

    return ((candle["close"] - candle["low"]) / full) <= 0.25


def sniper_signal(df):
    if df is None or len(df) < 120:
        return None, "sniper not enough data"

    symbol = df["symbol"].iloc[-1]
    confirm_side, confirm_reason = strong_htf_trend(symbol)
    if confirm_side is None:
        return None, confirm_reason

    data = df.copy()
    data["ema_fast"] = data["close"].ewm(span=21).mean()
    data["ema_slow"] = data["close"].ewm(span=55).mean()
    data["rsi"] = calculate_rsi(data["close"], RSI_PERIOD)
    data["range"] = data["high"] - data["low"]

    atr_current = data["range"].rolling(14).mean().iloc[-1]
    atr_average = data["range"].rolling(80).mean().iloc[-1]
    volume_current = data["volume"].iloc[-1]
    volume_average = data["volume"].rolling(40).mean().iloc[-1]
    adx = calculate_adx(data)

    if (
        pd.isna(atr_current)
        or pd.isna(atr_average)
        or pd.isna(volume_average)
        or atr_current <= 0
        or atr_average <= 0
        or adx is None
    ):
        return None, "sniper invalid indicators"

    if adx < max(get_min_adx(), 24):
        return None, f"sniper adx too low {round(adx, 1)}"

    if atr_current < atr_average * 0.65:
        return None, "sniper low volatility"

    if atr_current > atr_average * 1.9:
        return None, "sniper volatility spike"

    if volume_current < volume_average * 0.85:
        return None, "sniper volume too low"

    last = data.iloc[-1]
    prev = data.iloc[-2]
    prior = data.iloc[-3]
    rsi = last["rsi"]
    body_ratio = candle_body_ratio(last)

    if pd.isna(rsi):
        return None, "sniper invalid rsi"

    recent_high = data["high"].iloc[-12:-2].max()
    recent_low = data["low"].iloc[-12:-2].min()

    buy_setup = (
        confirm_side == "BUY"
        and last["ema_fast"] > last["ema_slow"]
        and last["close"] > last["ema_fast"]
        and prior["low"] <= prior["ema_fast"]
        and prev["close"] > prev["open"]
        and last["close"] > last["open"]
        and last["close"] > recent_high
        and body_ratio >= 0.45
        and close_near_extreme(last, "BUY")
        and 50 <= rsi <= 64
    )

    if buy_setup:
        return "BUY", f"{confirm_reason} breakout forte rsi={round(rsi, 1)} adx={round(adx, 1)}"

    sell_setup = (
        confirm_side == "SELL"
        and last["ema_fast"] < last["ema_slow"]
        and last["close"] < last["ema_fast"]
        and prior["high"] >= prior["ema_fast"]
        and prev["close"] < prev["open"]
        and last["close"] < last["open"]
        and last["close"] < recent_low
        and body_ratio >= 0.45
        and close_near_extreme(last, "SELL")
        and 36 <= rsi <= 50
    )

    if sell_setup:
        return "SELL", f"{confirm_reason} breakout forte rsi={round(rsi, 1)} adx={round(adx, 1)}"

    return None, (
        f"sniper no setup side={confirm_side} rsi={round(rsi, 1)} "
        f"adx={round(adx, 1)} body={round(body_ratio, 2)}"
    )


def breakout_m1_signal(df):
    if df is None or len(df) < 80:
        return None, "m1 breakout not enough data"

    symbol = df["symbol"].iloc[-1]
    m15 = get_data(symbol, interval="15", limit=240)
    if m15 is None or len(m15) < 210:
        return None, "m1 breakout no m15 trend data"

    trend = m15.copy()
    trend["ema50"] = trend["close"].ewm(span=50).mean()
    trend["ema200"] = trend["close"].ewm(span=200).mean()
    trend_last = trend.iloc[-1]

    data = df.copy()
    data["rsi"] = calculate_rsi(data["close"], RSI_PERIOD)

    prev_close = data["close"].shift(1)
    data["tr"] = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr"] = data["tr"].rolling(14).mean()

    last = data.iloc[-1]
    rsi = last["rsi"]
    atr_current = last["atr"]
    atr_average = data["atr"].iloc[-21:-1].mean()
    volume_current = last["volume"]
    volume_average = data["volume"].iloc[-21:-1].mean()
    recent_high = data["high"].iloc[-11:-1].max()
    recent_low = data["low"].iloc[-11:-1].min()

    if (
        pd.isna(rsi)
        or pd.isna(atr_current)
        or pd.isna(atr_average)
        or pd.isna(volume_average)
        or atr_current <= 0
        or atr_average <= 0
        or volume_average <= 0
    ):
        return None, "m1 breakout invalid indicators"

    trend_buy = trend_last["ema50"] > trend_last["ema200"]
    trend_sell = trend_last["ema50"] < trend_last["ema200"]
    volume_ok = volume_current > volume_average
    atr_ok = atr_current > atr_average
    breaks_high = last["close"] > recent_high
    breaks_low = last["close"] < recent_low

    if trend_buy and rsi > 55 and breaks_high and volume_ok and atr_ok:
        return (
            "BUY",
            f"m1 breakout buy ema50>ema200 rsi={round(rsi, 1)} "
            f"vol={round(volume_current / volume_average, 2)} atr={round(atr_current / atr_average, 2)}"
        )

    if trend_sell and rsi < 45 and breaks_low and volume_ok and atr_ok:
        return (
            "SELL",
            f"m1 breakout sell ema50<ema200 rsi={round(rsi, 1)} "
            f"vol={round(volume_current / volume_average, 2)} atr={round(atr_current / atr_average, 2)}"
        )

    return None, (
        f"m1 breakout no setup trend_buy={trend_buy} trend_sell={trend_sell} "
        f"rsi={round(rsi, 1)} break_hi={breaks_high} break_lo={breaks_low} "
        f"vol_ok={volume_ok} atr_ok={atr_ok}"
    )


def get_signal(df):
    mode = get_strategy_mode()

    if mode == "relaxed":
        return relaxed_signal(df)

    if mode in ("m1_breakout", "breakout", "volume_breakout"):
        return breakout_m1_signal(df)

    if mode == "sniper":
        return sniper_signal(df)

    if mode == "quality":
        return quality_signal(df)

    signal = ai_signal(df, use_news_filter=True, verbose=False)
    return signal, "strict strategy"


def get_signal_interval():
    mode = get_strategy_mode()
    if mode in ("m1_breakout", "breakout", "volume_breakout"):
        return "1"
    return os.getenv("BYBIT_INTERVAL", BYBIT_INTERVAL)


def get_signal_limit():
    mode = get_strategy_mode()
    if mode in ("m1_breakout", "breakout", "volume_breakout"):
        return 240
    return 100


def refresh_account():
    global balance, profit

    response = get_session().get_wallet_balance(accountType="UNIFIED", coin=get_quote_coin())
    if api_ok(response):
        accounts = response.get("result", {}).get("list", [])
        if accounts:
            coins = accounts[0].get("coin", [])
            coin = next((item for item in coins if item.get("coin") == get_quote_coin()), None)
            if coin:
                balance = float(coin.get("walletBalance") or coin.get("equity") or 0)

    total_profit = 0.0
    for symbol in get_symbols():
        for position in get_positions(symbol):
            total_profit += float(position.get("unrealisedPnl") or 0)
    profit = total_profit


def get_positions(symbol=None):
    params = {"category": get_category()}
    if symbol:
        params["symbol"] = symbol

    response = get_session().get_positions(**params)
    if not api_ok(response):
        log(f"POSITIONS FAILED {symbol or 'ALL'}: {response}")
        return []

    positions = response.get("result", {}).get("list", [])
    return [item for item in positions if abs(float(item.get("size") or 0)) > 0]


def get_latest_closed_pnl(symbol):
    try:
        response = get_session().get_closed_pnl(
            category=get_category(),
            symbol=symbol,
            limit=1,
        )
    except Exception as exc:
        log(f"CLOSED PNL ERROR {symbol}: {type(exc).__name__}: {exc}")
        return None

    if not api_ok(response):
        log(f"CLOSED PNL FAILED {symbol}: {response}")
        return None

    rows = response.get("result", {}).get("list", [])
    return rows[0] if rows else None


def closed_pnl_key(symbol, closed_pnl):
    return "|".join(
        [
            symbol,
            str(closed_pnl.get("orderId") or ""),
            str(closed_pnl.get("updatedTime") or closed_pnl.get("createdTime") or ""),
            str(closed_pnl.get("closedPnl") or ""),
        ]
    )


def ensure_daily_stats():
    today = local_now().date().isoformat()

    if daily_stats["date"] == today:
        return

    daily_stats.update(
        {
            "date": today,
            "start_balance": balance,
            "realized_pnl": 0.0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "consecutive_losses": 0,
            "summary_sent": False,
        }
    )


def ensure_monthly_stats():
    month = local_now().strftime("%Y-%m")

    if monthly_stats["month"] == month:
        return

    monthly_stats.update(
        {
            "month": month,
            "start_balance": balance,
            "realized_pnl": 0.0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
        }
    )


def closed_pnl_datetime(closed_pnl):
    raw = closed_pnl.get("updatedTime") or closed_pnl.get("createdTime")
    try:
        return datetime.fromtimestamp(int(raw) / 1000, local_timezone())
    except Exception:
        return local_now()


def record_closed_trade(pnl, closed_at=None):
    ensure_daily_stats()
    ensure_monthly_stats()

    closed_at = closed_at or local_now()
    stats_start_at = get_stats_start_at()
    if stats_start_at is not None and closed_at < stats_start_at:
        return

    daily_matches = closed_at.date().isoformat() == daily_stats["date"]
    monthly_matches = closed_at.strftime("%Y-%m") == monthly_stats["month"]

    if daily_matches:
        daily_stats["realized_pnl"] += pnl
        daily_stats["closed_trades"] += 1

    if monthly_matches:
        monthly_stats["realized_pnl"] += pnl
        monthly_stats["closed_trades"] += 1

    if pnl > 0:
        if daily_matches:
            daily_stats["wins"] += 1
            daily_stats["consecutive_losses"] = 0
        if monthly_matches:
            monthly_stats["wins"] += 1
    else:
        if daily_matches:
            daily_stats["losses"] += 1
            daily_stats["consecutive_losses"] += 1
        if monthly_matches:
            monthly_stats["losses"] += 1


def record_closed_pnl(symbol, closed_pnl):
    key = closed_pnl_key(symbol, closed_pnl)
    if key in recorded_closed_keys:
        return False

    recorded_closed_keys.add(key)
    record_closed_trade(safe_float(closed_pnl.get("closedPnl")), closed_pnl_datetime(closed_pnl))
    return True


def sync_closed_pnl_stats():
    global last_closed_pnl_sync

    now = time.time()
    if now - last_closed_pnl_sync < 300:
        return

    last_closed_pnl_sync = now
    ensure_daily_stats()
    ensure_monthly_stats()

    for symbol in get_symbols():
        try:
            response = get_session().get_closed_pnl(
                category=get_category(),
                symbol=symbol,
                limit=100,
            )
        except Exception as exc:
            log(f"SYNC CLOSED PNL ERROR {symbol}: {type(exc).__name__}: {exc}")
            continue

        if not api_ok(response):
            log(f"SYNC CLOSED PNL FAILED {symbol}: {response}")
            continue

        rows = response.get("result", {}).get("list", [])
        for row in reversed(rows):
            record_closed_pnl(symbol, row)


def stats_snapshot(stats, period_key):
    realized = float(stats["realized_pnl"])
    open_pnl = float(profit)
    total = realized + open_pnl
    trades = int(stats["closed_trades"])
    wins = int(stats["wins"])
    losses = int(stats["losses"])
    start_balance = stats["start_balance"] or balance
    win_rate = (wins / trades * 100) if trades else 0.0
    result = "profittevole" if total > 0 else "in perdita" if total < 0 else "in pareggio"

    return {
        "period": stats[period_key],
        "result": result,
        "start_balance": float(start_balance or 0),
        "balance": float(balance or 0),
        "balance_change": float(balance - start_balance) if start_balance is not None else 0.0,
        "realized_pnl": realized,
        "open_pnl": open_pnl,
        "total_pnl": total,
        "closed_trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
    }


def get_dashboard_reports():
    ensure_daily_stats()
    ensure_monthly_stats()
    return {
        "daily": stats_snapshot(daily_stats, "date"),
        "monthly": stats_snapshot(monthly_stats, "month"),
    }


def trading_guard_reason():
    if not is_trade_guard_enabled():
        return None

    ensure_daily_stats()
    ensure_monthly_stats()

    monthly_start = monthly_stats["start_balance"] or balance
    monthly_total = float(monthly_stats["realized_pnl"]) + float(profit)

    if monthly_start and monthly_total <= -(monthly_start * get_monthly_max_loss_pct() / 100):
        return f"monthly loss limit reached {format_money(monthly_total)}"

    if daily_stats["consecutive_losses"] >= get_max_consecutive_losses():
        return f"consecutive losses reached {daily_stats['consecutive_losses']}"

    trades = monthly_stats["closed_trades"]
    if trades >= get_monthly_win_rate_check_trades():
        win_rate = (monthly_stats["wins"] / trades) * 100 if trades else 0
        if win_rate < get_min_monthly_win_rate():
            return f"monthly win rate too low {round(win_rate, 1)}%"

    return None


def get_guard_status():
    reason = trading_guard_reason()
    stats_start_at = get_stats_start_at()
    return {
        "active": reason is not None,
        "enabled": is_trade_guard_enabled(),
        "reason": reason or ("ok" if is_trade_guard_enabled() else "disattivato"),
        "max_risk_pct": get_max_runtime_risk(),
        "monthly_max_loss_pct": get_monthly_max_loss_pct(),
        "stats_start_at": stats_start_at.isoformat(timespec="minutes") if stats_start_at else None,
    }


def notify_position_closed(symbol, previous_position):
    closed_pnl = get_latest_closed_pnl(symbol)

    if closed_pnl is None:
        log(f"{symbol} closed, waiting for closed PnL data")
        return

    key = closed_pnl_key(symbol, closed_pnl)
    if key in notified_closed_keys:
        return

    notified_closed_keys.add(key)

    pnl = safe_float(closed_pnl.get("closedPnl"))
    side = closed_pnl.get("side") or previous_position.get("side", "UNKNOWN")
    qty = closed_pnl.get("qty") or previous_position.get("size", "?")
    entry = closed_pnl.get("avgEntryPrice") or previous_position.get("avgPrice", "?")
    exit_price = closed_pnl.get("avgExitPrice") or "?"
    result = "PROFITTO" if pnl > 0 else "PERDITA" if pnl < 0 else "PAREGGIO"

    record_closed_pnl(symbol, closed_pnl)
    notify(
        "BOT PRO - posizione chiusa\n"
        f"Exchange: Bybit {'Demo' if is_demo_mode() else 'Live'}\n"
        f"Simbolo: {symbol}\n"
        f"Direzione: {side}\n"
        f"Quantita: {qty}\n"
        f"Entrata: {entry}\n"
        f"Uscita: {exit_price}\n"
        f"Risultato: {result}\n"
        f"PnL: {format_money(pnl)}"
    )


def monitor_position_lifecycle():
    current_positions = {}

    for symbol in get_symbols():
        positions = get_positions(symbol)
        if positions:
            current_positions[symbol] = positions[0]

    for symbol, previous in list(tracked_positions.items()):
        if symbol not in current_positions:
            notify_position_closed(symbol, previous)

    tracked_positions.clear()
    tracked_positions.update(current_positions)


def maybe_send_daily_summary():
    ensure_daily_stats()

    if daily_stats["summary_sent"]:
        return

    now = local_now()
    hour, minute = get_daily_summary_time()
    if (now.hour, now.minute) < (hour, minute):
        return

    notify(build_report_message("BOT PRO - riepilogo giornaliero"))
    daily_stats["summary_sent"] = True


def get_open_positions_text():
    lines = []

    for symbol in get_symbols():
        for position in get_positions(symbol):
            side = position.get("side", "?")
            size = position.get("size", "?")
            avg_price = position.get("avgPrice", "?")
            pnl = safe_float(position.get("unrealisedPnl"))
            lines.append(f"{symbol} {side} qty={size} entry={avg_price} PnL={format_money(pnl)}")

    return "\n".join(lines) if lines else "Nessuna posizione aperta"


def build_report_message(title="BOT PRO - report richiesto"):
    ensure_daily_stats()
    ensure_monthly_stats()

    realized = daily_stats["realized_pnl"]
    open_pnl = profit
    total = realized + open_pnl
    start_balance = daily_stats["start_balance"] or balance
    balance_change = balance - start_balance
    result = "profittevole" if total > 0 else "in perdita" if total < 0 else "in pareggio"
    guard = get_guard_status()
    monthly = stats_snapshot(monthly_stats, "month")
    return (
        f"{title}\n"
        f"Data: {daily_stats['date']}\n"
        f"Esito: giornata {result}\n"
        f"Trade chiusi: {daily_stats['closed_trades']}\n"
        f"Vinti/Persi: {daily_stats['wins']}/{daily_stats['losses']}\n"
        f"Perdite consecutive: {daily_stats['consecutive_losses']}\n"
        f"PnL realizzato: {format_money(realized)}\n"
        f"PnL aperto: {format_money(open_pnl)}\n"
        f"PnL totale: {format_money(total)}\n"
        f"Variazione balance: {format_money(balance_change)}\n"
        f"\nMese: {monthly['period']}\n"
        f"Esito mese: {monthly['result']}\n"
        f"Trade mese: {monthly['closed_trades']}\n"
        f"Vinti/Persi mese: {monthly['wins']}/{monthly['losses']}\n"
        f"Win rate mese: {monthly['win_rate']}%\n"
        f"PnL mese: {format_money(monthly['total_pnl'])}\n"
        f"\n"
        f"Balance: {format_money(balance)}\n"
        f"Ultimo segnale: {last_signal}\n"
        f"Guardiano trade: {'ATTIVO' if guard['active'] else 'ok'} ({guard['reason']})\n"
        f"Posizioni:\n{get_open_positions_text()}"
    )


def get_open_position_symbols():
    symbols = set()
    for symbol in get_symbols():
        for position in get_positions(symbol):
            symbols.add(position.get("symbol"))
    return symbols


def get_telegram_updates():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return []

    try:
        import requests

        params = {"timeout": 0}
        if last_telegram_update_id is not None:
            params["offset"] = last_telegram_update_id + 1

        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params=params,
            timeout=8,
        )

        if not response.ok:
            log(f"TELEGRAM UPDATES FAILED: {response.status_code} {response.text}")
            return []

        return response.json().get("result", [])
    except Exception as exc:
        log(f"TELEGRAM UPDATES ERROR: {type(exc).__name__}: {exc}")
        return []


def process_telegram_commands():
    global last_telegram_update_id

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        return

    for update in get_telegram_updates():
        update_id = update.get("update_id")
        if update_id is not None:
            last_telegram_update_id = update_id

        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip().lower()
        message_chat_id = str((message.get("chat") or {}).get("id"))

        if message_chat_id != str(chat_id):
            continue

        if text in ("/report", "report", "resoconto", "/resoconto", "/status", "status"):
            notify(build_report_message("BOT PRO - report richiesto"))
        elif text in ("/help", "help", "aiuto"):
            notify(
                "Comandi BOT PRO\n"
                "/report - report della giornata\n"
                "/status - stato bot e posizioni\n"
                "/help - lista comandi"
            )


def calculate_qty(symbol, entry, stop_loss):
    fixed_qty = get_fixed_qty()
    if fixed_qty > 0:
        return normalize_qty(symbol, fixed_qty)

    risk_pct = max(0.1, min(float(settings["risk"]), get_max_runtime_risk()))
    risk_amount = get_effective_balance() * (risk_pct / 100)
    stop_distance = abs(entry - stop_loss)

    if stop_distance <= 0:
        return normalize_qty(symbol, DEFAULT_LOT)

    qty = risk_amount / stop_distance
    return normalize_qty(symbol, qty)


def open_trade(df, signal):
    global last_signal

    if signal not in ("BUY", "SELL"):
        return

    symbol = df["symbol"].iloc[-1]
    last_signal = f"{symbol} {signal}"
    bar_time = str(df["time"].iloc[-1])

    if last_trade_bar.get(symbol) == bar_time:
        log(f"{symbol} already traded this candle")
        return

    if in_order_cooldown(symbol):
        log(f"{symbol} order cooldown active")
        return

    open_symbols = get_open_position_symbols()
    if symbol in open_symbols:
        log(f"{symbol} already open")
        return

    if len(open_symbols) >= settings["max_trades"]:
        log("MAX TRADES GLOBAL")
        return

    guard_reason = trading_guard_reason()
    if guard_reason:
        log(f"TRADE BLOCKED: {guard_reason}")
        last_signal = f"{symbol} {signal} BLOCKED"
        return

    ticker = get_session().get_tickers(category=get_category(), symbol=symbol)
    if not api_ok(ticker):
        log(f"TICKER FAILED {symbol}: {ticker}")
        return

    last_price = float(ticker["result"]["list"][0]["lastPrice"])
    atr = compute_atr(df)

    if pd.isna(atr) or atr <= 0:
        return

    if signal == "BUY":
        side = "Buy"
        sl = last_price - (atr * get_bybit_sl_atr())
        tp = last_price + (atr * get_bybit_tp_atr())
    else:
        side = "Sell"
        sl = last_price + (atr * get_bybit_sl_atr())
        tp = last_price - (atr * get_bybit_tp_atr())

    qty = calculate_qty(symbol, last_price, sl)
    sl_text = normalize_price(symbol, sl)
    tp_text = normalize_price(symbol, tp)

    if is_dry_run():
        log(f"DRY RUN {symbol} {signal} qty={qty} sl={sl_text} tp={tp_text}")
        return

    try:
        response = get_session().place_order(
            category=get_category(),
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=qty,
            takeProfit=tp_text,
            stopLoss=sl_text,
            tpslMode="Full",
            positionIdx=0,
            orderLinkId=f"botpro-{int(time.time() * 1000)}",
        )
    except Exception as exc:
        log(f"ORDER ERROR {symbol} {signal} qty={qty}: {type(exc).__name__}: {exc}")
        start_order_cooldown(symbol)
        return

    if api_ok(response):
        last_trade_bar[symbol] = bar_time
        log(f"{symbol} {signal} OPENED qty={qty} sl={sl_text} tp={tp_text}")
        notify(
            "BOT PRO - posizione aperta\n"
            f"Exchange: Bybit {'Demo' if is_demo_mode() else 'Live'}\n"
            f"Simbolo: {symbol}\n"
            f"Direzione: {'ACQUISTO' if signal == 'BUY' else 'VENDITA'}\n"
            f"Quantita: {qty}\n"
            f"SL: {sl_text}\n"
            f"TP: {tp_text}"
        )
    else:
        log(f"ORDER FAILED {symbol} {signal}: {response}")
        start_order_cooldown(symbol)


def maybe_force_demo_order():
    global forced_test_done

    if forced_test_done or not should_force_demo_order():
        return

    if not is_demo_mode() or is_dry_run():
        log("FORCE ORDER SKIPPED: requires BYBIT_DEMO=true and BYBIT_DRY_RUN=false")
        forced_test_done = True
        return

    symbols = get_symbols()
    if not symbols:
        log("FORCE ORDER SKIPPED: no symbols configured")
        forced_test_done = True
        return

    symbol = symbols[0]
    df = get_data(symbol)
    if df is None:
        log(f"FORCE ORDER SKIPPED: no data for {symbol}")
        return

    side = get_force_side()
    log(f"FORCE DEMO ORDER {symbol} {side}")
    open_trade(df, side)
    forced_test_done = True


def manage_trailing():
    for symbol in get_symbols():
        df = get_data(symbol)
        if df is None:
            continue

        atr = compute_atr(df)
        if pd.isna(atr) or atr <= 0:
            continue

        ticker = get_session().get_tickers(category=get_category(), symbol=symbol)
        if not api_ok(ticker):
            continue

        price = float(ticker["result"]["list"][0]["lastPrice"])

        for position in get_positions(symbol):
            side = position.get("side")
            entry = float(position.get("avgPrice") or 0)
            current_sl = float(position.get("stopLoss") or 0)
            new_sl = None

            if side == "Buy":
                atr_gain = (price - entry) / atr
                if atr_gain >= 4:
                    new_sl = price - (atr * 0.5)
                elif atr_gain >= 3:
                    new_sl = price - (atr * 0.8)
                elif atr_gain >= 2:
                    new_sl = entry + (atr * 0.8)
                elif atr_gain >= 1:
                    new_sl = entry + (atr * get_be_lock_atr())
                elif atr_gain >= get_be_trigger_atr():
                    new_sl = entry + (atr * get_be_lock_atr())

                if new_sl is None or (current_sl > 0 and current_sl >= new_sl):
                    continue

            elif side == "Sell":
                atr_gain = (entry - price) / atr
                if atr_gain >= 4:
                    new_sl = price + (atr * 0.5)
                elif atr_gain >= 3:
                    new_sl = price + (atr * 0.8)
                elif atr_gain >= 2:
                    new_sl = entry - (atr * 0.8)
                elif atr_gain >= 1:
                    new_sl = entry - (atr * get_be_lock_atr())
                elif atr_gain >= get_be_trigger_atr():
                    new_sl = entry - (atr * get_be_lock_atr())

                if new_sl is None or (current_sl > 0 and current_sl <= new_sl):
                    continue

            if new_sl is None:
                continue

            sl_text = normalize_price(symbol, new_sl)

            if is_dry_run():
                log(f"DRY RUN TRAIL {symbol} SL -> {sl_text}")
                continue

            response = get_session().set_trading_stop(
                category=get_category(),
                symbol=symbol,
                stopLoss=sl_text,
                positionIdx=0,
            )

            if api_ok(response):
                log(f"TRAIL {symbol} SL -> {sl_text}")
            else:
                log(f"TRAIL FAILED {symbol}: {response}")


def run_bot():
    global equity_history, forced_test_done, last_signal, running

    try:
        get_session()
    except Exception as exc:
        log(f"BYBIT INIT FAILED: {exc}")
        return

    log(
        "BYBIT BOT STARTED "
        f"testnet={env_bool('BYBIT_TESTNET', True)} "
        f"demo={is_demo_mode()} "
        f"dry_run={is_dry_run()}"
    )
    running = True
    forced_test_done = False

    try:
        while running:
            try:
                refresh_account()
                ensure_daily_stats()
                sync_closed_pnl_stats()
                equity_history.append(balance + profit)
                equity_history = equity_history[-100:]

                manage_trailing()
                monitor_position_lifecycle()
                maybe_send_daily_summary()
                process_telegram_commands()
                maybe_force_demo_order()

                for symbol in get_symbols():
                    df = get_data(symbol, interval=get_signal_interval(), limit=get_signal_limit())
                    if df is None:
                        continue

                    if not settings["ai"]:
                        last_signal = "AI OFF"
                        continue

                    signal, reason = get_signal(df)
                    last_signal = f"{symbol} {signal or 'NONE'}"
                    log(f"{symbol} -> {signal or 'NONE'} ({get_strategy_mode()}: {reason})")

                    if signal:
                        open_trade(df, signal)
            except Exception as exc:
                log(f"BYBIT LOOP ERROR: {type(exc).__name__}: {exc}")

            time.sleep(SLEEP)
    finally:
        running = False
        log("BYBIT BOT STOPPED")


def stop_bot():
    global running
    running = False
