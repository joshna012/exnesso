import os
import sys
import time
import logging
import MetaTrader5 as mt5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mt5test")

SERVER   = os.environ.get("MT5_SERVER")
LOGIN    = os.environ.get("MT5_LOGIN")
PASSWORD = os.environ.get("MT5_PASSWORD")
PORTABLE = os.environ.get("GITHUB_ACTIONS") == "true"

if not SERVER or not LOGIN or not PASSWORD:
    log.error("MT5 credentials not found in environment variables!")
    sys.exit(1)

LOGIN = int(LOGIN)

log.info(f"Initializing MT5 (portable={PORTABLE})...")
if not mt5.initialize(login=LOGIN, server=SERVER, password=PASSWORD, portable=PORTABLE):
    fallback_paths = [
        "C:\\MT5\\terminal64.exe",
        "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe",
        "C:\\Program Files\\Exness MetaTrader 5\\terminal64.exe",
        "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
    ]
    initialized = False
    for path in fallback_paths:
        log.info(f"Default init failed. Trying path: {path}")
        if mt5.initialize(path=path, login=LOGIN, server=SERVER, password=PASSWORD, portable=PORTABLE):
            initialized = True
            break
    if not initialized:
        log.error(f"MT5 initialization failed: {mt5.last_error()}")
        sys.exit(1)

# Verify connection
acc_info = mt5.account_info()
if acc_info is None:
    log.error(f"Failed to get account info. Login details might be incorrect. Error: {mt5.last_error()}")
    mt5.shutdown()
    sys.exit(1)

log.info(f"=== MT5 CONNECTION SUCCESS ===")
log.info(f"Account Login: {acc_info.login}")
log.info(f"Account Balance: {acc_info.balance:.2f} {acc_info.currency}")
log.info(f"Account Broker/Server: {acc_info.server}")

# Verify symbol check
symbol = "XAUUSDm"
if not mt5.symbol_select(symbol, True):
    log.warning(f"Symbol {symbol} not available, trying XAUUSD...")
    symbol = "XAUUSD"
    if not mt5.symbol_select(symbol, True):
        log.error("Gold symbol not available in this account!")
        mt5.shutdown()
        sys.exit(1)

# Check terminal settings
terminal_info = mt5.terminal_info()
trade_allowed_terminal = terminal_info.trade_allowed
trade_allowed_account = acc_info.trade_allowed

log.info("=== AUTOMATION CHECKS ===")
if trade_allowed_terminal:
    log.info("✅ SUCCESS: Algorithmic Trading (AutoTrading) is ENABLED in terminal!")
else:
    log.error("❌ FAILURE: Algorithmic Trading (AutoTrading) is DISABLED in terminal!")
    mt5.shutdown()
    sys.exit(1)

if trade_allowed_account:
    log.info("✅ SUCCESS: Trading is allowed on this Account!")
else:
    log.error("❌ FAILURE: Trading is DISABLED on this Account!")
    mt5.shutdown()
    sys.exit(1)

# Perform a real test trade (open and close) to satisfy the verification
log.info("=== REAL TRADE EXECUTION TEST ===")
tick = mt5.symbol_info_tick(symbol)
if tick is None:
    log.error("Failed to get tick info for test trade!")
    mt5.shutdown()
    sys.exit(1)

price = tick.ask
sl = price - 5.0
tp = price + 5.0

request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_BUY,
    "price": price,
    "sl": sl,
    "tp": tp,
    "deviation": 20,
    "magic": 999999,
    "comment": "test_trade",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC
}

log.info(f"Sending test BUY order at {price:.5f}...")
result = mt5.order_send(request)
if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
    ret_code = getattr(result, 'retcode', 'None')
    comment = getattr(result, 'comment', 'None')
    log.error(f"❌ Test trade failed! Retcode: {ret_code}, Comment: {comment}")
    mt5.shutdown()
    sys.exit(1)

ticket = result.order
log.info(f"✅ SUCCESS: Test trade opened successfully! Ticket ID: {ticket}")

# Wait 5 seconds
log.info("Waiting 5 seconds before closing the position...")
time.sleep(5)

# Close the position
tick = mt5.symbol_info_tick(symbol)
close_price = tick.bid if tick is not None else result.price
close_request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "position": ticket,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_SELL,
    "price": close_price,
    "deviation": 20,
    "magic": 999999,
    "comment": "test_close",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC
}

log.info(f"Sending test CLOSE order at {close_price:.5f}...")
close_result = mt5.order_send(close_request)
if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
    ret_code_c = getattr(close_result, 'retcode', 'None')
    log.error(f"❌ Test trade close failed! Retcode: {ret_code_c}")
    mt5.shutdown()
    sys.exit(1)

log.info("✅ SUCCESS: Test trade closed successfully!")
mt5.shutdown()

log.info("🎉 ALL CHECKS PASSED: Your GitHub Actions runner is 100% ready for auto-trading!")
sys.exit(0)
