import time

import MetaTrader5 as mt5
import pandas as pd

from ai_model import ai_signal
from config import *
from utils import log


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

TIMEFRAME = mt5.TIMEFRAME_M5
SUCCESS_RETCODES = {
    mt5.TRADE_RETCODE_DONE,
    mt5.TRADE_RETCODE_PLACED,
}


def get_bot_positions():
    positions = mt5.positions_get() or []
    return [p for p in positions if getattr(p, "magic", None) == MAGIC_NUMBER]


def compute_atr(df, period=14):
    data = df.copy()
    prev_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean().iloc[-1]


def round_price(symbol, price):
    info = mt5.symbol_info(symbol)
    if info is None:
        return price
    return round(price, info.digits)


def normalize_volume(symbol, volume):
    info = mt5.symbol_info(symbol)
    if info is None:
        return round(max(DEFAULT_LOT, min(volume, MAX_LOT)), 2)

    min_volume = info.volume_min
    max_volume = min(info.volume_max, MAX_LOT)
    step = info.volume_step

    volume = max(min_volume, min(volume, max_volume))

    if step > 0:
        volume = int(volume / step) * step

    return round(volume, 2)


def calculate_lot(symbol, signal, entry, stop_loss):
    acc = mt5.account_info()
    if acc is None:
        log(f"NO ACCOUNT INFO: fallback lot {DEFAULT_LOT}")
        return normalize_volume(symbol, DEFAULT_LOT)

    risk_pct = max(0.1, min(float(settings["risk"]), 10.0))
    risk_amount = acc.balance * (risk_pct / 100)
    order_type = mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL
    loss_per_lot = mt5.order_calc_profit(order_type, symbol, 1.0, entry, stop_loss)

    if loss_per_lot is None or loss_per_lot == 0:
        log(f"LOT CALC FAILED: fallback lot {DEFAULT_LOT}")
        return normalize_volume(symbol, DEFAULT_LOT)

    lot = risk_amount / abs(loss_per_lot)
    return normalize_volume(symbol, lot)


def send_sl_update(position, new_sl, reason):
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "sl": round_price(position.symbol, new_sl),
        "tp": position.tp,
    }
    result = mt5.order_send(request)

    if result and result.retcode in SUCCESS_RETCODES:
        log(f"{reason} {position.symbol} SL -> {request['sl']}")
        return True

    log(f"SL UPDATE FAILED {position.symbol}: {result}")
    return False


def should_update_sl(position, new_sl):
    current_sl = position.sl or 0

    if current_sl <= 0:
        return True

    if position.type == mt5.ORDER_TYPE_BUY:
        return current_sl < new_sl

    return current_sl > new_sl


def get_real_symbol(symbol):
    symbols = mt5.symbols_get()

    if symbols is None:
        log("symbols_get FAILED")
        return None

    for item in symbols:
        if symbol.upper() == item.name.upper():
            return item.name

    for item in symbols:
        if symbol.lower() in item.name.lower():
            return item.name

    log(f"Symbol not found: {symbol}")
    return None


def get_data(symbol):
    real_symbol = get_real_symbol(symbol)

    if real_symbol is None:
        return None

    if not mt5.symbol_select(real_symbol, True):
        log(f"Symbol select failed: {real_symbol}")
        return None

    rates = mt5.copy_rates_from_pos(real_symbol, TIMEFRAME, 0, 100)

    if rates is None:
        log(f"No data: {real_symbol}")
        return None

    df = pd.DataFrame(rates)

    if df.empty:
        log(f"Empty data: {real_symbol}")
        return None

    df["symbol"] = real_symbol
    return df


def manage_trailing():
    positions = get_bot_positions()
    if not positions:
        return

    for position in positions:
        df = get_data(position.symbol)
        if df is None:
            continue

        atr = compute_atr(df)
        if pd.isna(atr) or atr <= 0:
            continue

        tick = mt5.symbol_info_tick(position.symbol)
        if not tick:
            continue

        price = tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask
        entry = position.price_open
        new_sl = None
        reason = None

        if position.type == mt5.ORDER_TYPE_BUY:
            atr_gain = (price - entry) / atr

            if atr_gain >= 4:
                new_sl = price - (atr * 0.5)
                reason = "AGGRESSIVE BUY"
            elif atr_gain >= 3:
                new_sl = price - (atr * 0.8)
                reason = "TRAIL BUY"
            elif atr_gain >= 2:
                new_sl = entry + (atr * 0.8)
                reason = "LOCK BUY"
            elif atr_gain >= 1:
                new_sl = entry
                reason = "BE BUY"

        elif position.type == mt5.ORDER_TYPE_SELL:
            atr_gain = (entry - price) / atr

            if atr_gain >= 4:
                new_sl = price + (atr * 0.5)
                reason = "AGGRESSIVE SELL"
            elif atr_gain >= 3:
                new_sl = price + (atr * 0.8)
                reason = "TRAIL SELL"
            elif atr_gain >= 2:
                new_sl = entry - (atr * 0.8)
                reason = "LOCK SELL"
            elif atr_gain >= 1:
                new_sl = entry
                reason = "BE SELL"

        if new_sl is not None and should_update_sl(position, new_sl):
            send_sl_update(position, new_sl, reason)


def open_trade(df, signal):
    global last_signal

    if signal not in ("BUY", "SELL"):
        return

    positions = get_bot_positions()
    symbol = df["symbol"].iloc[-1]
    last_signal = f"{symbol} {signal}"

    symbols_open = [position.symbol for position in positions]
    if symbol in symbols_open:
        log(f"{symbol} already open")
        return

    if len(positions) >= settings["max_trades"]:
        log("MAX TRADES GLOBAL")
        return

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        log(f"NO TICK: {symbol}")
        return

    price = tick.ask if signal == "BUY" else tick.bid
    atr = compute_atr(df)

    if pd.isna(atr) or atr <= 0:
        return

    if signal == "BUY":
        sl = price - (atr * ATR_SL_MULTIPLIER)
        tp = price + (atr * ATR_TP_MULTIPLIER)
    else:
        sl = price + (atr * ATR_SL_MULTIPLIER)
        tp = price - (atr * ATR_TP_MULTIPLIER)

    sl = round_price(symbol, sl)
    tp = round_price(symbol, tp)
    lot = calculate_lot(symbol, signal, price, sl)

    fillings = [
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_RETURN,
    ]

    for fill in fillings:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": DEVIATION,
            "magic": MAGIC_NUMBER,
            "comment": ORDER_COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": fill,
        }
        result = mt5.order_send(request)

        if result and result.retcode in SUCCESS_RETCODES:
            log(f"{symbol} {signal} OPENED lot={lot} sl={sl} tp={tp}")
            return

        log(f"{symbol} {signal} rejected fill={fill}: {result}")

    log(f"FAILED {symbol} {signal}")


def run_bot():
    global balance, equity_history, last_signal, profit, running

    if not mt5.initialize():
        log("MT5 INIT FAILED")
        return

    log("BOT STARTED")
    running = True

    try:
        while running:
            acc = mt5.account_info()

            if acc:
                balance = acc.balance

            positions = get_bot_positions()
            profit = sum(position.profit for position in positions)
            equity_history.append(balance + profit)
            equity_history = equity_history[-100:]

            manage_trailing()

            for symbol in SYMBOLS:
                df = get_data(symbol)

                if df is None:
                    continue

                if not settings["ai"]:
                    last_signal = "AI OFF"
                    continue

                signal = ai_signal(df)
                last_signal = f"{symbol} {signal or 'NONE'}"
                log(f"{symbol} -> {signal or 'NONE'}")

                if signal:
                    open_trade(df, signal)

            time.sleep(SLEEP)
    finally:
        running = False
        mt5.shutdown()
        log("BOT STOPPED")


def stop_bot():
    global running
    running = False
