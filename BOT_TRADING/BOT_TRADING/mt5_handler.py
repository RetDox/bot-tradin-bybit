import MetaTrader5 as mt5
from utils import log

def connect():
    if not mt5.initialize():
        log("❌ MT5 INIT FAIL")
        return False

    acc = mt5.account_info()
    if acc is None:
        log("❌ NO ACCOUNT")
        return False

    log(f"✅ CONNECTED: {acc.login}")
    return True