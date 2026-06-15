import threading
import os
import time

from flask import Flask, render_template, jsonify, request

if os.getenv("EXCHANGE", "mt5").lower() == "bybit":
    import bot_bybit as bot
else:
    import bot

from utils import logs

app = Flask(__name__)
bot_thread = None
watchdog_thread = None
bot_thread_lock = threading.Lock()
manual_stop = False


def env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def clamp_float(value, default, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def clamp_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def get_default_symbol():
    symbols = os.getenv("BYBIT_SYMBOLS", "XAUUSDT")
    return symbols.split(",")[0].strip().upper() or "XAUUSDT"


def candle_time(value):
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_dashboard_reports():
    if hasattr(bot, "get_dashboard_reports"):
        return bot.get_dashboard_reports()
    return {
        "daily": {},
        "monthly": {},
    }


def get_guard_status():
    if hasattr(bot, "get_guard_status"):
        return bot.get_guard_status()
    return {"active": False, "reason": "ok"}


@app.route("/")
def home():
    return render_template("index.html")


def start_bot_thread(reason="manual"):
    global bot_thread

    with bot_thread_lock:
        if bot.running or (bot_thread is not None and bot_thread.is_alive()):
            return False

        bot_thread = threading.Thread(target=bot.run_bot, name=f"bot-runner-{reason}")
        bot_thread.daemon = True
        bot_thread.start()
        return True


def watchdog_loop():
    while True:
        time.sleep(int(os.getenv("BOT_WATCHDOG_SECONDS", "30")))

        if manual_stop:
            continue

        if not env_bool("BOT_AUTO_RESTART", True):
            continue

        if not bot.running and (bot_thread is None or not bot_thread.is_alive()):
            started = start_bot_thread("watchdog")
            if started:
                try:
                    from utils import log
                    log("BOT WATCHDOG RESTARTED THREAD")
                except Exception:
                    pass


def start_watchdog():
    global watchdog_thread
    if watchdog_thread is not None and watchdog_thread.is_alive():
        return

    watchdog_thread = threading.Thread(target=watchdog_loop, name="bot-watchdog")
    watchdog_thread.daemon = True
    watchdog_thread.start()


@app.route("/start")
def start():
    global manual_stop
    manual_stop = False
    start_bot_thread("manual")
    return "STARTED"


@app.route("/stop")
def stop():
    global manual_stop
    manual_stop = True
    bot.stop_bot()
    return "STOPPED"


@app.route("/settings", methods=["POST"])
def settings():
    data = request.get_json(silent=True) or {}
    max_risk = bot.get_max_runtime_risk() if hasattr(bot, "get_max_runtime_risk") else 1.0

    bot.settings["risk"] = clamp_float(data.get("risk"), 1.0, 0.1, max_risk)
    bot.settings["max_trades"] = clamp_int(data.get("max_trades"), 1, 1, 3)
    bot.settings["ai"] = data.get("ai", True) is True

    return "OK"


@app.route("/data")
def data():
    return jsonify({
        "exchange": os.getenv("EXCHANGE", "mt5").lower(),
        "strategy": bot.get_strategy_mode() if hasattr(bot, "get_strategy_mode") else None,
        "balance": bot.balance,
        "wallet_balance": bot.balance,
        "trading_capital": bot.get_effective_balance() if hasattr(bot, "get_effective_balance") else bot.balance,
        "account_size": bot.get_account_size() if hasattr(bot, "get_account_size") else None,
        "fixed_qty": bot.get_fixed_qty() if hasattr(bot, "get_fixed_qty") else None,
        "profit": bot.profit,
        "status": "ON" if bot.running else "OFF",
        "signal": bot.last_signal,
        "logs": logs,
        "settings": bot.settings,
        "equity": bot.equity_history,
        "reports": get_dashboard_reports(),
        "guard": get_guard_status(),
    })


@app.route("/candles")
def candles():
    symbol = request.args.get("symbol", get_default_symbol()).strip().upper()
    interval = request.args.get("interval", os.getenv("BYBIT_INTERVAL", "5")).strip()
    limit = clamp_int(request.args.get("limit"), 160, 30, 500)

    try:
        try:
            df = bot.get_data(symbol, interval=interval, limit=limit)
        except TypeError:
            df = bot.get_data(symbol)

        candles_data = []
        for _, row in df.tail(limit).iterrows():
            candles_data.append({
                "time": candle_time(row.get("time")),
                "open": float(row.get("open")),
                "high": float(row.get("high")),
                "low": float(row.get("low")),
                "close": float(row.get("close")),
            })

        return jsonify({
            "symbol": symbol,
            "interval": interval,
            "candles": candles_data
        })
    except Exception as exc:
        return jsonify({
            "symbol": symbol,
            "interval": interval,
            "candles": [],
            "error": f"{type(exc).__name__}: {exc}"
        }), 500


if __name__ == "__main__":
    host = os.getenv("BOT_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("BOT_PORT", "5000")))
    debug = os.getenv("BOT_DEBUG", "false").lower() == "true"

    start_watchdog()
    if env_bool("BOT_AUTO_START", True):
        start_bot_thread("autostart")

    app.run(debug=debug, use_reloader=False, host=host, port=port)
