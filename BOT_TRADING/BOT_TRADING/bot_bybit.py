import os
import time
from decimal import Decimal, ROUND_DOWN

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
        "max_qty": Decimal(str(lot_filter.get("maxOrderQty", "1000000"))),
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
    value = max(meta["min_qty"], min(value, meta["max_qty"]))
    return fmt_decimal(value)


def get_data(symbol):
    response = get_session().get_kline(
        category=get_category(),
        symbol=symbol,
        interval=os.getenv("BYBIT_INTERVAL", BYBIT_INTERVAL),
        limit=100,
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


def get_signal(df):
    mode = get_strategy_mode()

    if mode == "relaxed":
        return relaxed_signal(df)

    signal = ai_signal(df, use_news_filter=True, verbose=False)
    return signal, "strict strategy"


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


def get_open_position_symbols():
    symbols = set()
    for symbol in get_symbols():
        for position in get_positions(symbol):
            symbols.add(position.get("symbol"))
    return symbols


def calculate_qty(symbol, entry, stop_loss):
    risk_pct = max(0.1, min(float(settings["risk"]), 10.0))
    risk_amount = balance * (risk_pct / 100)
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

    open_symbols = get_open_position_symbols()
    if symbol in open_symbols:
        log(f"{symbol} already open")
        return

    if len(open_symbols) >= settings["max_trades"]:
        log("MAX TRADES GLOBAL")
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
        sl = last_price - (atr * ATR_SL_MULTIPLIER)
        tp = last_price + (atr * ATR_TP_MULTIPLIER)
    else:
        side = "Sell"
        sl = last_price + (atr * ATR_SL_MULTIPLIER)
        tp = last_price - (atr * ATR_TP_MULTIPLIER)

    qty = calculate_qty(symbol, last_price, sl)
    sl_text = normalize_price(symbol, sl)
    tp_text = normalize_price(symbol, tp)

    if is_dry_run():
        log(f"DRY RUN {symbol} {signal} qty={qty} sl={sl_text} tp={tp_text}")
        return

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

    if api_ok(response):
        log(f"{symbol} {signal} OPENED qty={qty} sl={sl_text} tp={tp_text}")
        notify(
            "BOT PRO position opened\n"
            f"Exchange: Bybit {'Demo' if is_demo_mode() else 'Live'}\n"
            f"Symbol: {symbol}\n"
            f"Side: {signal}\n"
            f"Qty: {qty}\n"
            f"SL: {sl_text}\n"
            f"TP: {tp_text}"
        )
    else:
        log(f"ORDER FAILED {symbol} {signal}: {response}")


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
                    new_sl = entry

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
                    new_sl = entry

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
                equity_history.append(balance + profit)
                equity_history = equity_history[-100:]

                manage_trailing()
                maybe_force_demo_order()

                for symbol in get_symbols():
                    df = get_data(symbol)
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
