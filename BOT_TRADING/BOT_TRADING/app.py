import threading
import os

from flask import Flask, render_template, jsonify, request

if os.getenv("EXCHANGE", "mt5").lower() == "bybit":
    import bot_bybit as bot
else:
    import bot

from utils import logs

app = Flask(__name__)
bot_thread = None


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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/start")
def start():
    global bot_thread
    if not bot.running and (bot_thread is None or not bot_thread.is_alive()):
        bot_thread = threading.Thread(target=bot.run_bot)
        bot_thread.daemon = True
        bot_thread.start()
    return "STARTED"


@app.route("/stop")
def stop():
    bot.stop_bot()
    return "STOPPED"


@app.route("/settings", methods=["POST"])
def settings():
    data = request.get_json(silent=True) or {}

    bot.settings["risk"] = clamp_float(data.get("risk"), 1.0, 0.1, 10.0)
    bot.settings["max_trades"] = clamp_int(data.get("max_trades"), 2, 1, 20)
    bot.settings["ai"] = data.get("ai", True) is True

    return "OK"


@app.route("/data")
def data():
    return jsonify({
        "exchange": os.getenv("EXCHANGE", "mt5").lower(),
        "balance": bot.balance,
        "profit": bot.profit,
        "status": "ON" if bot.running else "OFF",
        "signal": bot.last_signal,
        "logs": logs,
        "settings": bot.settings,
        "equity": bot.equity_history,
        "reports": get_dashboard_reports(),
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

    app.run(debug=debug, use_reloader=False, host=host, port=port)
