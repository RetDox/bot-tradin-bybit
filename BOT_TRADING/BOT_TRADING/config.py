SYMBOLS = ["EURUSD", "XAUUSD"]
BYBIT_SYMBOLS = ["XAUUSDT"]
BYBIT_CATEGORY = "linear"
BYBIT_INTERVAL = "5"
BYBIT_QUOTE_COIN = "USDT"

SLEEP = 5
MAGIC_NUMBER = 123456
ORDER_COMMENT = "BOT PRO"

# =========================
# EMA
# =========================
EMA_FAST = 20
EMA_SLOW = 50

# =========================
# RSI
# =========================
RSI_PERIOD = 14
RSI_BUY_MAX = 65
RSI_SELL_MIN = 35

# =========================
# RISK
# =========================
RISK = 1.0
MAX_TRADES = 2
DEFAULT_LOT = 0.02
MAX_LOT = 1.0
DEVIATION = 20
ATR_SL_MULTIPLIER = 1.0
ATR_TP_MULTIPLIER = 2.5

# =========================
# VOLATILITY FILTER
# =========================
MIN_VOLATILITY_MULTIPLIER = 0.5
MAX_VOLATILITY_MULTIPLIER = 2.5

# =========================
# NEWS FILTER
# =========================
NEWS_BLOCK_HOURS = [12, 13, 14, 15]

# =========================
# STRUCTURE FILTER
# =========================
STRUCTURE_LOOKBACK = 10

# =========================
# MOMENTUM FILTER
# =========================
MIN_BODY_RATIO = 0.25
