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
        "equity": bot.equity_history
    })


if __name__ == "__main__":
    host = os.getenv("BOT_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("BOT_PORT", "5000")))
    debug = os.getenv("BOT_DEBUG", "false").lower() == "true"

    app.run(debug=debug, use_reloader=False, host=host, port=port)
