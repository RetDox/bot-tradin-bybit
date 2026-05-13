import argparse
from dataclasses import dataclass

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from ai_model import ai_signal
from config import *


SYMBOL = "EURUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
BARS = 5000
INITIAL_BALANCE = 1000.0
DEFAULT_SPREAD_POINTS = {
    "EURUSD": 15,
    "XAUUSD": 35,
}


@dataclass
class Position:
    side: str
    entry: float
    sl: float
    tp: float
    lot: float
    risk_amount: float
    entry_index: int


def get_data(symbol, bars):
    if not mt5.initialize():
        raise RuntimeError("MT5 initialize failed")

    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Symbol select failed: {symbol}")

    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data received for {symbol}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_symbol_meta(symbol, df):
    info = mt5.symbol_info(symbol)
    if info is not None:
        return {
            "point": info.point,
            "digits": info.digits,
            "volume_min": info.volume_min,
            "volume_max": min(info.volume_max, MAX_LOT),
            "volume_step": info.volume_step,
            "contract_size": info.trade_contract_size or 100000,
        }

    median_price = float(df["close"].median())
    digits = 3 if median_price > 20 else 5
    return {
        "point": 10 ** -digits,
        "digits": digits,
        "volume_min": DEFAULT_LOT,
        "volume_max": MAX_LOT,
        "volume_step": 0.01,
        "contract_size": 100000,
    }


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
    return true_range.rolling(period).mean()


def compute_adx(df, period=14):
    up = df["high"].diff()
    down = -df["low"].diff()

    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

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
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(period).sum() / tr_sum)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(period).sum() / tr_sum)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    return dx.rolling(period).mean()


def prepare_data(df):
    data = df.copy()
    data["atr"] = compute_atr(data)
    data["adx"] = compute_adx(data)
    return data


def round_price(price, digits):
    return round(float(price), digits)


def normalize_lot(lot, meta):
    lot = max(meta["volume_min"], min(lot, meta["volume_max"]))
    step = meta["volume_step"]

    if step > 0:
        lot = int(lot / step) * step

    return round(lot, 2)


def calc_profit(symbol, side, lot, entry, exit_price, meta):
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    profit = mt5.order_calc_profit(order_type, symbol, lot, entry, exit_price)

    if profit is not None:
        return float(profit)

    multiplier = 1 if side == "BUY" else -1
    return (exit_price - entry) * multiplier * lot * meta["contract_size"]


def calc_lot(symbol, side, balance, risk_pct, entry, sl, meta):
    risk_amount = balance * (risk_pct / 100)
    loss_per_lot = abs(calc_profit(symbol, side, 1.0, entry, sl, meta))

    if loss_per_lot <= 0:
        return DEFAULT_LOT, risk_amount

    return normalize_lot(risk_amount / loss_per_lot, meta), risk_amount


def ask_price(bid_price, spread):
    return bid_price + spread


def apply_trailing(position, row, atr, spread, digits):
    if pd.isna(atr) or atr <= 0:
        return position

    if position.side == "BUY":
        current_price = row["open"]
        atr_gain = (current_price - position.entry) / atr

        if atr_gain >= 4:
            new_sl = current_price - (atr * 0.5)
        elif atr_gain >= 3:
            new_sl = current_price - (atr * 0.8)
        elif atr_gain >= 2:
            new_sl = position.entry + (atr * 0.8)
        elif atr_gain >= 1:
            new_sl = position.entry
        else:
            return position

        position.sl = max(position.sl, round_price(new_sl, digits))
        return position

    current_price = ask_price(row["open"], spread)
    atr_gain = (position.entry - current_price) / atr

    if atr_gain >= 4:
        new_sl = current_price + (atr * 0.5)
    elif atr_gain >= 3:
        new_sl = current_price + (atr * 0.8)
    elif atr_gain >= 2:
        new_sl = position.entry - (atr * 0.8)
    elif atr_gain >= 1:
        new_sl = position.entry
    else:
        return position

    position.sl = min(position.sl, round_price(new_sl, digits))
    return position


def check_exit(position, row, spread, slippage):
    if position.side == "BUY":
        hit_sl = row["low"] <= position.sl
        hit_tp = row["high"] >= position.tp

        if hit_sl:
            return position.sl - slippage, "SL"
        if hit_tp:
            return position.tp - slippage, "TP"
        return None, None

    ask_high = ask_price(row["high"], spread)
    ask_low = ask_price(row["low"], spread)
    hit_sl = ask_high >= position.sl
    hit_tp = ask_low <= position.tp

    if hit_sl:
        return position.sl + slippage, "SL"
    if hit_tp:
        return position.tp + slippage, "TP"
    return None, None


def open_position(symbol, row, signal, atr, balance, risk_pct, atr_sl, atr_tp, spread, slippage, meta, index):
    if signal == "BUY":
        entry = ask_price(row["open"], spread) + slippage
        sl = entry - (atr * atr_sl)
        tp = entry + (atr * atr_tp)
    else:
        entry = row["open"] - slippage
        sl = entry + (atr * atr_sl)
        tp = entry - (atr * atr_tp)

    entry = round_price(entry, meta["digits"])
    sl = round_price(sl, meta["digits"])
    tp = round_price(tp, meta["digits"])
    lot, risk_amount = calc_lot(symbol, signal, balance, risk_pct, entry, sl, meta)

    return Position(
        side=signal,
        entry=entry,
        sl=sl,
        tp=tp,
        lot=lot,
        risk_amount=risk_amount,
        entry_index=index,
    )


def run_backtest(
    df,
    symbol=SYMBOL,
    initial_balance=INITIAL_BALANCE,
    risk_pct=RISK,
    atr_sl=ATR_SL_MULTIPLIER,
    atr_tp=ATR_TP_MULTIPLIER,
    min_adx=0,
    spread_points=None,
    slippage_points=0,
    use_trailing=True,
):
    data = prepare_data(df)
    meta = get_symbol_meta(symbol, data)
    spread_points = DEFAULT_SPREAD_POINTS.get(symbol, 20) if spread_points is None else spread_points
    spread = spread_points * meta["point"]
    slippage = slippage_points * meta["point"]

    balance = float(initial_balance)
    equity_curve = [balance]
    trades = []
    position = None

    for i in range(100, len(data)):
        row = data.iloc[i]
        prev = data.iloc[i - 1]

        if position is not None:
            if use_trailing:
                position = apply_trailing(position, row, prev["atr"], spread, meta["digits"])

            exit_price, exit_reason = check_exit(position, row, spread, slippage)
            if exit_reason:
                pnl = calc_profit(symbol, position.side, position.lot, position.entry, exit_price, meta)
                balance += pnl
                trades.append(
                    {
                        "entry_time": data.iloc[position.entry_index]["time"],
                        "exit_time": row["time"],
                        "side": position.side,
                        "entry": position.entry,
                        "exit": round_price(exit_price, meta["digits"]),
                        "sl": position.sl,
                        "tp": position.tp,
                        "lot": position.lot,
                        "pnl": pnl,
                        "exit_reason": exit_reason,
                    }
                )
                position = None

        if position is None and not pd.isna(prev["atr"]) and prev["atr"] > 0:
            if not pd.isna(prev["adx"]) and prev["adx"] >= min_adx:
                signal = ai_signal(data.iloc[:i], use_news_filter=False, verbose=False)

                if signal:
                    position = open_position(
                        symbol,
                        row,
                        signal,
                        prev["atr"],
                        balance,
                        risk_pct,
                        atr_sl,
                        atr_tp,
                        spread,
                        slippage,
                        meta,
                        i,
                    )
                    exit_price, exit_reason = check_exit(position, row, spread, slippage)
                    if exit_reason:
                        pnl = calc_profit(symbol, position.side, position.lot, position.entry, exit_price, meta)
                        balance += pnl
                        trades.append(
                            {
                                "entry_time": row["time"],
                                "exit_time": row["time"],
                                "side": position.side,
                                "entry": position.entry,
                                "exit": round_price(exit_price, meta["digits"]),
                                "sl": position.sl,
                                "tp": position.tp,
                                "lot": position.lot,
                                "pnl": pnl,
                                "exit_reason": exit_reason,
                            }
                        )
                        position = None

        equity_curve.append(balance)

    return summarize(trades, equity_curve, initial_balance, atr_sl, atr_tp, min_adx)


def summarize(trades, equity_curve, initial_balance, atr_sl, atr_tp, min_adx):
    pnl = [trade["pnl"] for trade in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = pd.Series(equity_curve)
    drawdown = equity - equity.cummax()

    return {
        "atr_sl": atr_sl,
        "atr_tp": atr_tp,
        "min_adx": min_adx,
        "initial_balance": initial_balance,
        "final_balance": equity_curve[-1],
        "net_profit": equity_curve[-1] - initial_balance,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": (len(wins) / len(trades) * 100) if trades else 0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0,
        "trades_detail": trades,
        "equity_curve": equity_curve,
    }


def print_result(result):
    print(
        "SL:{atr_sl} TP:{atr_tp} ADX:{min_adx} | "
        "Final:{final_balance:.2f} Net:{net_profit:.2f} "
        "Trades:{trades} WR:{winrate:.1f}% PF:{profit_factor:.2f} DD:{max_drawdown:.2f}".format(
            **result
        )
    )


def optimize(df, symbol, args):
    results = []

    for atr_sl in [0.8, 1.0, 1.2, 1.5]:
        for atr_tp in [1.5, 2.0, 2.5, 3.0]:
            for adx in [0, 15, 20, 25]:
                result = run_backtest(
                    df,
                    symbol=symbol,
                    initial_balance=args.balance,
                    risk_pct=args.risk,
                    atr_sl=atr_sl,
                    atr_tp=atr_tp,
                    min_adx=adx,
                    spread_points=args.spread_points,
                    slippage_points=args.slippage_points,
                    use_trailing=not args.no_trailing,
                )
                results.append(result)
                print_result(result)

    best = sorted(
        results,
        key=lambda item: (item["final_balance"], item["profit_factor"], -abs(item["max_drawdown"])),
        reverse=True,
    )[0]

    print("\nBEST CONFIG")
    print_result(best)
    return best


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest BOT PRO strategy")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--bars", type=int, default=BARS)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE)
    parser.add_argument("--risk", type=float, default=RISK)
    parser.add_argument("--atr-sl", type=float, default=ATR_SL_MULTIPLIER)
    parser.add_argument("--atr-tp", type=float, default=ATR_TP_MULTIPLIER)
    parser.add_argument("--min-adx", type=float, default=0)
    parser.add_argument("--spread-points", type=float, default=None)
    parser.add_argument("--slippage-points", type=float, default=0)
    parser.add_argument("--no-trailing", action="store_true")
    parser.add_argument("--optimize", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        df = get_data(args.symbol, args.bars)

        if args.optimize:
            optimize(df, args.symbol, args)
            return

        result = run_backtest(
            df,
            symbol=args.symbol,
            initial_balance=args.balance,
            risk_pct=args.risk,
            atr_sl=args.atr_sl,
            atr_tp=args.atr_tp,
            min_adx=args.min_adx,
            spread_points=args.spread_points,
            slippage_points=args.slippage_points,
            use_trailing=not args.no_trailing,
        )
        print_result(result)

        if result["trades_detail"]:
            print("\nLAST TRADES")
            for trade in result["trades_detail"][-10:]:
                print(
                    f"{trade['exit_time']} {trade['side']} "
                    f"entry={trade['entry']} exit={trade['exit']} "
                    f"lot={trade['lot']} pnl={trade['pnl']:.2f} {trade['exit_reason']}"
                )
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
