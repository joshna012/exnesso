"""
MT5 Pro Dual-Strategy Bot (Exness) - Multi-Symbol Edition v6.0
- Supports both Trendline/EMA Pullback Bounce and Donchian Range Breakout strategies
- Strategy selection via CONFIG flag: STRATEGY_MODE = "BOUNCE" or "BREAKOUT"
- Mode A (BOUNCE): enters dynamic EMA 50 touches in trend direction with tight swing SL
- Mode B (BREAKOUT): enters 10-period consolidative Donchian range breakouts
- Trend-aligned entry filter: Multi-Timeframe Confirmation (M1 EMA 50 + M5 EMA 200)
- Trend Strength Guard: M1 ADX(14) must be >= 20 to trade, avoiding choppy consolidations
- Setup candle filters: body ratio >= 50%, range >= 30% of ATR
- Relative Volume (RVOL) filter: requires setup candle volume >= 1.5x of 20-period M1 average
- Dynamic Sizing: Sized using adaptive balance risk based on consecutive loss streaks
- Robotic Drawdown Protection: Halves trade risk percentage on consecutive losses (1% -> 0.5% -> 0.25%)
- Adaptive Cooldown: Triples cooldown (180s instead of 60s) for a symbol if the last trade was a loss
- Partial Profit Scale-Out: closes 50% of trade volume at 1.5x ATR (or 2.0x ATR for Breakouts) and moves remaining SL to BE
- Dynamic Breakeven lock-in and Trailing Stop-Loss based on M1 ATR
- 3-loss circuit breaker: pauses 15 min in choppy markets
- Session filter: trades only 07:00-17:00 UTC (= 13:00-23:00 UTC+6)
- Daily loss limit, daily profit goal, CSV journal, Telegram alerts
Requires: pip install MetaTrader5 numpy
DISCLAIMER: High-frequency scalping is high risk. Demo account only.
"""

import os
import csv
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
import numpy as np
import MetaTrader5 as mt5

# ---------------- CONFIG ----------------
SERVER     = os.environ.get("MT5_SERVER")
LOGIN      = os.environ.get("MT5_LOGIN")
PASSWORD   = os.environ.get("MT5_PASSWORD")
PORTABLE   = os.environ.get("GITHUB_ACTIONS") == "true"
FB_API_KEY = os.environ.get("FB_API_KEY")

# Fallback to local_config.py for local development
if not SERVER or not LOGIN or not PASSWORD or not FB_API_KEY:
    try:
        import local_config
        SERVER     = SERVER or getattr(local_config, "MT5_SERVER", None)
        LOGIN      = LOGIN or getattr(local_config, "MT5_LOGIN", None)
        PASSWORD   = PASSWORD or getattr(local_config, "MT5_PASSWORD", None)
        FB_API_KEY = FB_API_KEY or getattr(local_config, "FB_API_KEY", None)
    except ImportError:
        pass


# Verify credentials are set
if not SERVER or not LOGIN or not PASSWORD:
    raise RuntimeError(
        "Error: MT5 credentials not found. "
        "Please set MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER as environment variables, "
        "or create a local_config.py file with your private credentials. "
        "Ensure local_config.py is added to .gitignore to keep it private."
    )

LOGIN = int(LOGIN)

SYMBOLS           = ["XAUUSDm"]  # Hunted in parallel (Gold only)
SCAN_SECONDS      = 0.1            # scan 10 times per second (lightning-fast execution)

# Strategy Mode Setting
STRATEGY_MODE      = "AUTO"        # Strategy Selection: "BOUNCE", "BREAKOUT", "SWEEP", "SMC", "ORB", "OB", or "AUTO"
BOT_THOUGHTS       = True          # Print conversational commentary explaining bot logic, scans, and triggers

# Volatility & Trend Settings
ATR_PERIOD         = 14           # period for ATR calculation
EMA_PERIOD         = 50           # period for trend filter M1 EMA
EMA_M5_PERIOD      = 200          # period for trend filter M5 EMA (upgraded to institutional 200)
EMA_M15_PERIOD     = 50           # period for intermediate trend filter M15 EMA
EMA_H1_PERIOD      = 50           # period for macro trend filter H1 EMA
ADX_PERIOD         = 14           # period for ADX calculation
ADX_MIN_LEVEL      = 20           # minimum trend strength (ADX >= 20 to trade)
RSI_PERIOD         = 14           # Relative Strength Index period for sweep confirmation
SPREAD_ATR_LIMIT   = 0.35         # max ratio of spread / ATR to allow trading
SPREAD_MA_PERIOD   = 20           # lookback for average spread to detect widening
SL_ATR_MULT        = 1.0          # stop-loss = 1.0x ATR (tighter for pullback bounces)
TP_ATR_MULT        = 3.0          # initial take-profit = 3.0x ATR (R:R ratio of 1:3)
QTP_THRESHOLD      = 75           # minimum Quantitative Trade Probability Score (0-100) to trade
DXY_VELOCITY_LIMIT = 0.05         # block trades if DXY shifts by more than this over 3 candles
SWING_LOOKBACK     = 10           # lookback for swing high/low trailing stop
OB_LOOKBACK        = 15           # candles to scan back for active Order Blocks
 
# Economic News Calendar Settings
NEWS_URL           = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_PAUSE_MINUTES = 15           # pause trading 15 minutes before and after high-impact news
 
# Candle Pullback & Range Settings
DONCHIAN_PERIOD     = 10           # range breakout of last 10 completed M1 candles (used in BREAKOUT mode)
SWEEP_PERIOD        = 15           # range lookback of last 15 completed M1 candles (used in SWEEP mode)
SWEEP_WICK_RATIO    = 0.40         # setup candle rejection wick must be >= 40% of full candle range
ORB_PERIOD          = 15           # opening range duration in minutes (used in ORB mode)
SMC_LOOKBACK        = 10           # candles to scan back for active Fair Value Gaps (used in SMC mode)
MIN_CANDLE_RANGE_ATR = 0.30       # setup M1 candle must be at least 30% of M1 ATR
MIN_BODY_RATIO       = 0.50       # candle body must be >= 50% of the full candle range
RVOL_PERIOD         = 20           # period for M1 average tick volume calculation
RVOL_LIMIT          = 1.5          # setup candle volume must be >= 1.5x average volume

# Scale-Out / Partial TP Settings
PARTIAL_TP_ATR      = 1.5          # target to take partial profit
PARTIAL_CLOSE_RATIO = 0.5          # close 50% of position volume at target 1

# Trailing Stop & Breakeven Settings
TRAIL_TRIGGER_ATR  = 1.0          # trigger trail when profit > 1.0x ATR
BREAKEVEN_BUFFER_ATR = 0.1        # move SL to entry + 0.1x ATR
TRAIL_DISTANCE_ATR = 1.2          # trail SL at 1.2x ATR behind price

MAX_HOLD_SECONDS  = 90           # force-close stalled trades
RISK_PER_TRADE    = 0.01         # base risk: 1% balance per trade
DAILY_LOSS_LIMIT  = 0.03         # stop day at -3%
DAILY_PROFIT_GOAL = 0.02         # stop day at +2% (lock the win)
DAILY_PROFIT_TRAIL_PERCENT = 0.20   # trail floor at peak - 20% of peak profit
DAILY_PROFIT_MIN_SLACK     = 0.005  # minimum trailing slack of 0.5% of account balance to prevent noise trigger
MAX_TRADES_DAY    = 100
COOLDOWN_SEC      = 60           # normal cooldown per symbol in seconds
LOSS_STREAK_MAX   = 3            # losses in a row -> pause
LOSS_PAUSE_SEC    = 900          # 15-minute chop pause
SESSION_START_UTC = 0            # Tokyo open   (06:00 your local time)
SESSION_END_UTC   = 17           # NY afternoon (23:00 your local time)
MAX_OPEN_TOTAL    = 2            # max simultaneous positions
MAGIC             = 234568
DEVIATION         = 20
JOURNAL_FILE      = "trade_journal.csv"
RUN_DURATION_HOURS = 5.75         # clean shutdown after 5 hours 45 minutes to prevent Github Action timeout cut

# Telegram (optional): create a bot with @BotFather, put token + your chat id.
TELEGRAM_TOKEN   = ""
TELEGRAM_CHAT_ID = ""

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tickbot")

# Global State
open_times = {}                              # ticket -> entry time
known_deals = set()                          # deals already journaled
state = {"day": None, "start_balance": 0.0, "trades_today": 0,
         "last_entry": {s: 0.0 for s in SYMBOLS}, "halted": False,
         "halt_reason": "", "loss_streak": 0, "pause_until": 0.0,
         "partial_closed_tickets": {},
         "last_trade_loss": {s: False for s in SYMBOLS},
         "dxy_bullish": True,
         "orb_ranges": {},
         "last_commentary_time": {s: 0 for s in SYMBOLS},
         "profit_locked": False,
         "peak_equity_profit": 0.0} # tracks loss per symbol, DXY strength, session ORB ranges, commentary timing, and daily trailing profit states
BOT_START = time.time()                      # session start marker

# Technical Indicators Cache
indicators = {s: {"atr": 0.0, "ema50": 0.0, "ema50_m15": 0.0, "ema200_m5": 0.0, "ema50_h1": 0.0, "adx": 0.0, "rsi_m15": 50.0, "avg_spread": 0.0, "last_update": 0.0} for s in SYMBOLS}

# ---------------- INDICATORS ----------------
def compute_atr(rates, period=14):
    high = rates['high']
    low = rates['low']
    close = rates['close']
    tr = np.zeros(len(rates))
    for i in range(1, len(rates)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
    tr[0] = high[0] - low[0]
    
    atr = np.zeros(len(rates))
    atr[period-1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, len(rates)):
        atr[i] = tr[i] * alpha + atr[i-1] * (1.0 - alpha)
    return atr[-1]

def compute_ema(rates, period):
    close = rates['close']
    alpha = 2.0 / (period + 1)
    ema = np.zeros(len(rates))
    ema[0] = close[0]
    for i in range(1, len(rates)):
        ema[i] = close[i] * alpha + ema[i-1] * (1 - alpha)
    return ema

def compute_adx(rates, period=14):
    high = rates['high']
    low = rates['low']
    close = rates['close']
    n = len(rates)
    
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        
        if up > down and up > 0:
            plus_dm[i] = up
        else:
            plus_dm[i] = 0
            
        if down > up and down > 0:
            minus_dm[i] = down
        else:
            minus_dm[i] = 0
            
    tr[0] = high[0] - low[0]
    
    # Smooth TR, +DM, -DM (Wilder's smoothing)
    str_val = np.zeros(n)
    splus_dm = np.zeros(n)
    sminus_dm = np.zeros(n)
    
    str_val[period] = np.sum(tr[1:period+1])
    splus_dm[period] = np.sum(plus_dm[1:period+1])
    sminus_dm[period] = np.sum(minus_dm[1:period+1])
    
    for i in range(period+1, n):
        str_val[i] = str_val[i-1] - (str_val[i-1] / period) + tr[i]
        splus_dm[i] = splus_dm[i-1] - (splus_dm[i-1] / period) + plus_dm[i]
        sminus_dm[i] = sminus_dm[i-1] - (sminus_dm[i-1] / period) + minus_dm[i]
        
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    dx = np.zeros(n)
    
    for i in range(period, n):
        if str_val[i] > 0:
            plus_di[i] = 100 * splus_dm[i] / str_val[i]
            minus_di[i] = 100 * sminus_dm[i] / str_val[i]
        else:
            plus_di[i] = 0
            minus_di[i] = 0
            
        di_diff = abs(plus_di[i] - minus_di[i])
        di_sum = plus_di[i] + minus_di[i]
        dx[i] = 100 * di_diff / di_sum if di_sum > 0 else 0
        
    # ADX is SMA of DX
    adx = np.zeros(n)
    adx[2*period-1] = np.mean(dx[period:2*period])
    for i in range(2*period, n):
        adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
        
    return adx[-1]

def compute_rsi(rates, period=14):
    close = rates['close']
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    
    avg_gain = np.zeros(len(close))
    avg_loss = np.zeros(len(close))
    
    if len(close) <= period:
        return np.zeros(len(close))
        
    avg_gain[period] = np.mean(gain[:period])
    avg_loss[period] = np.mean(loss[:period])
    
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i-1]) / period
        
    rsi = np.zeros(len(close))
    for i in range(period, len(close)):
        if avg_loss[i] == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def update_indicators():
    global last_news_fetch, news_events
    now = time.time()
    
    # 1. Update DXYm cache
    dxy_rates = mt5.copy_rates_from_pos("DXYm", mt5.TIMEFRAME_M1, 0, 100)
    if dxy_rates is not None and len(dxy_rates) >= 50:
        dxy_tick = mt5.symbol_info_tick("DXYm")
        if dxy_tick is not None:
            dxy_mid = (dxy_tick.ask + dxy_tick.bid) / 2
            dxy_ema = compute_ema(dxy_rates, 50)[-1]
            state["dxy_bullish"] = (dxy_mid > dxy_ema)
            
    # 2. Update Symbol Indicators
    for s in SYMBOLS:
        cache = indicators.get(s)
        if cache is None or now - cache["last_update"] >= 10:
            rates = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 0, 100)
            rates_m5 = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M5, 0, 300)
            rates_m15 = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M15, 0, 100)
            rates_h1 = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_H1, 0, 100)
            
            if (rates is not None and len(rates) >= 50 and 
                rates_m5 is not None and len(rates_m5) >= 250 and
                rates_m15 is not None and len(rates_m15) >= 50 and
                rates_h1 is not None and len(rates_h1) >= 50):
                
                atr = compute_atr(rates, ATR_PERIOD)
                ema50 = compute_ema(rates, EMA_PERIOD)[-1]
                adx = compute_adx(rates, ADX_PERIOD)
                ema200_m5 = compute_ema(rates_m5, EMA_M5_PERIOD)[-1]
                ema50_m15 = compute_ema(rates_m15, EMA_M15_PERIOD)[-1]
                ema50_h1 = compute_ema(rates_h1, EMA_H1_PERIOD)[-1]
                rsi_m15 = compute_rsi(rates_m15, RSI_PERIOD)[-1]
                
                sym_info = mt5.symbol_info(s)
                avg_spread = 0.0
                if sym_info is not None:
                    avg_spread = np.mean(rates['spread'][-21:-1]) * sym_info.point
                
                indicators[s] = {
                    "atr": atr,
                    "ema50": ema50,
                    "ema50_m15": ema50_m15,
                    "ema200_m5": ema200_m5,
                    "ema50_h1": ema50_h1,
                    "adx": adx,
                    "rsi_m15": rsi_m15,
                    "avg_spread": avg_spread,
                    "last_update": now
                }
            elif cache is None:
                indicators[s] = {
                    "atr": 0.001,
                    "ema50": 0.0,
                    "ema50_m15": 0.0,
                    "ema200_m5": 0.0,
                    "ema50_h1": 0.0,
                    "adx": 0.0,
                    "rsi_m15": 50.0,
                    "avg_spread": 0.0,
                    "last_update": 0.0
                }

# Economic News State
import json
last_news_fetch = 0.0
news_events = []

def update_news():
    global last_news_fetch, news_events
    now = time.time()
    if now - last_news_fetch < 3600:  # check once per hour
        return
    try:
        req = urllib.request.Request(NEWS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            events = []
            for item in data:
                if item.get('impact') == 'High':
                    try:
                        dt = datetime.fromisoformat(item['date'])
                        dt_utc = dt.astimezone(timezone.utc)
                        events.append({
                            'title': item.get('title'),
                            'country': item.get('country'),
                            'time': dt_utc
                        })
                    except Exception as e:
                        continue
            news_events = events
            last_news_fetch = now
            log.info("News calendar updated. Loaded %d high-impact events.", len(news_events))
    except Exception as e:
        log.warning("Failed to fetch news calendar: %s", e)
        last_news_fetch = now - 3300  # retry in 5 minutes

def is_news_paused(symbol):
    """Check if there is a high-impact news event within NEWS_PAUSE_MINUTES of current time."""
    now_utc = datetime.now(timezone.utc)
    currencies = ["All"]
    if "USD" in symbol:
        currencies.append("USD")
    if "JPY" in symbol:
        currencies.append("JPY")
    if "EUR" in symbol:
        currencies.append("EUR")
    if "GBP" in symbol:
        currencies.append("GBP")
        
    for ev in news_events:
        if ev['country'] in currencies:
            diff = abs((now_utc - ev['time']).total_seconds())
            if diff <= NEWS_PAUSE_MINUTES * 60:
                return True, ev['title']
    return False, ""

def get_qtp_score(symbol, direction, mid, ema200_m5, ema50_m15, ema50_h1, dxy_aligned, adx, rvol, rsi_m15):
    score = 0
    
    # 1. Trend Alignment check M5 + M15 (20 points)
    trend_aligned = False
    if direction == "BUY" and mid > ema200_m5 and mid > ema50_m15:
        trend_aligned = True
    elif direction == "SELL" and mid < ema200_m5 and mid < ema50_m15:
        trend_aligned = True
    if trend_aligned:
        score += 20
        
    # 2. H1 Institutional Trend Alignment check (15 points)
    h1_aligned = False
    if direction == "BUY" and mid > ema50_h1:
        h1_aligned = True
    elif direction == "SELL" and mid < ema50_h1:
        h1_aligned = True
    if h1_aligned:
        score += 15
        
    # 3. DXY Alignment check (20 points)
    if dxy_aligned:
        score += 20
        
    # 4. Trend Strength ADX (15 points)
    if adx >= 25:
        score += 15
    elif adx >= 20:
        score += 10
        
    # 5. Volume RVOL confirmation (15 points)
    if rvol >= 2.0:
        score += 15
    elif rvol >= 1.5:
        score += 10
        
    # 6. Synthetic Retail Sentiment via M15 RSI (15 points)
    if direction == "BUY":
        if rsi_m15 < 30:
            score += 15  # retail panic selling -> high prob bounce/reversal
        elif rsi_m15 < 70:
            score += 10  # neutral/standard
    else: # SELL
        if rsi_m15 > 70:
            score += 15  # retail panic buying -> high prob sell/reversal
        elif rsi_m15 > 30:
            score += 10  # neutral/standard
            
    return score

def find_active_fvgs(rates):
    """Scan back the last SMC_LOOKBACK candles to find active (unmitigated) M1 Fair Value Gaps.
    Returns: (bullish_fvgs, bearish_fvgs)"""
    bullish_fvgs = []
    bearish_fvgs = []
    n = len(rates)
    
    start_idx = max(0, n - 2 - SMC_LOOKBACK)
    end_idx = n - 3
    
    for i in range(start_idx, end_idx):
        # 1. Bullish FVG
        if rates[i]['high'] < rates[i+2]['low']:
            floor = rates[i]['high']
            ceiling = rates[i+2]['low']
            mitigated = False
            for j in range(i+3, n):
                if rates[j]['close'] < floor:
                    mitigated = True
                    break
            if not mitigated:
                bullish_fvgs.append({'floor': floor, 'ceiling': ceiling, 'index': i+1})
                
        # 2. Bearish FVG
        elif rates[i]['low'] > rates[i+2]['high']:
            ceiling = rates[i]['low']
            floor = rates[i+2]['high']
            mitigated = False
            for j in range(i+3, n):
                if rates[j]['close'] > ceiling:
                    mitigated = True
                    break
            if not mitigated:
                bearish_fvgs.append({'floor': floor, 'ceiling': ceiling, 'index': i+1})
                
    return bullish_fvgs, bearish_fvgs

def find_active_order_blocks(rates, atr):
    """Scan back the last OB_LOOKBACK completed M1 candles to find active (unmitigated) Order Blocks.
    Returns: (bullish_obs, bearish_obs)"""
    bullish_obs = []
    bearish_obs = []
    n = len(rates)
    
    start_idx = max(0, n - 2 - OB_LOOKBACK)
    end_idx = n - 3
    
    for i in range(start_idx, end_idx):
        # 1. Bullish Order Block: bearish candle followed by strong upward impulse
        if rates[i]['close'] < rates[i]['open']:
            # Impulsive upward breakout: next candle closes above rates[i] high with body size > 0.5x ATR
            if rates[i+1]['close'] > rates[i]['high'] and (rates[i+1]['close'] - rates[i+1]['open']) > 0.5 * atr:
                floor = rates[i]['low']
                ceiling = rates[i]['high']
                mitigated = False
                # Verify OB remains unmitigated (no subsequent candle closed below the OB floor/low)
                for j in range(i+2, n):
                    if rates[j]['close'] < floor:
                        mitigated = True
                        break
                if not mitigated:
                    bullish_obs.append({'floor': floor, 'ceiling': ceiling, 'index': i})
                    
        # 2. Bearish Order Block: bullish candle followed by strong downward impulse
        elif rates[i]['close'] > rates[i]['open']:
            if rates[i+1]['close'] < rates[i]['low'] and (rates[i+1]['open'] - rates[i+1]['close']) > 0.5 * atr:
                floor = rates[i]['low']
                ceiling = rates[i]['high']
                mitigated = False
                # Verify OB remains unmitigated (no subsequent candle closed above the OB ceiling/high)
                for j in range(i+2, n):
                    if rates[j]['close'] > ceiling:
                        mitigated = True
                        break
                if not mitigated:
                    bearish_obs.append({'floor': floor, 'ceiling': ceiling, 'index': i})
                    
    return bullish_obs, bearish_obs

def get_orb_ranges(symbol):
    """Fetch and calculate Tokyo (00:00 UTC), London (07:00 UTC) and NY (13:00 UTC) ORB range high/low for today."""
    today = datetime.now(timezone.utc).date()
    if symbol not in state["orb_ranges"]:
        state["orb_ranges"][symbol] = {}
        
    symbol_ranges = state["orb_ranges"][symbol]
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    
    # Tokyo ORB (00:00 - 00:15 UTC)
    if "tokyo_high" not in symbol_ranges:
        start_dt = datetime(today.year, today.month, today.day, 0, 0)
        if now_naive >= start_dt + timedelta(minutes=ORB_PERIOD):
            tokyo_rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1, start_dt, ORB_PERIOD)
            if tokyo_rates is not None and len(tokyo_rates) >= ORB_PERIOD:
                symbol_ranges["tokyo_high"] = np.max(tokyo_rates['high'])
                symbol_ranges["tokyo_low"] = np.min(tokyo_rates['low'])
                log.info("Symbol %s Tokyo ORB Range established: %.5f - %.5f", symbol, symbol_ranges["tokyo_low"], symbol_ranges["tokyo_high"])
    
    # London ORB (07:00 - 07:15 UTC)
    if "london_high" not in symbol_ranges:
        start_dt = datetime(today.year, today.month, today.day, 7, 0)
        if now_naive >= start_dt + timedelta(minutes=ORB_PERIOD):
            london_rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1, start_dt, ORB_PERIOD)
            if london_rates is not None and len(london_rates) >= ORB_PERIOD:
                symbol_ranges["london_high"] = np.max(london_rates['high'])
                symbol_ranges["london_low"] = np.min(london_rates['low'])
                log.info("Symbol %s London ORB Range established: %.5f - %.5f", symbol, symbol_ranges["london_low"], symbol_ranges["london_high"])
            
    # NY ORB (13:00 - 13:15 UTC)
    if "ny_high" not in symbol_ranges:
        start_dt = datetime(today.year, today.month, today.day, 13, 0)
        if now_naive >= start_dt + timedelta(minutes=ORB_PERIOD):
            ny_rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1, start_dt, ORB_PERIOD)
            if ny_rates is not None and len(ny_rates) >= ORB_PERIOD:
                symbol_ranges["ny_high"] = np.max(ny_rates['high'])
                symbol_ranges["ny_low"] = np.min(ny_rates['low'])
                log.info("Symbol %s NY ORB Range established: %.5f - %.5f", symbol, symbol_ranges["ny_low"], symbol_ranges["ny_high"])
            
    return symbol_ranges

# ---------------- NOTIFICATIONS (TELEGRAM & FACEBOOK) ----------------
def beautify_message_for_messenger(msg):
    try:
        # Trade closed
        if ("closed:" in msg) and ("Balance:" in msg):
            parts = msg.split("closed:")
            prefix = parts[0].strip().split()
            emoji = prefix[0]
            symbol = prefix[1]
            
            subparts = parts[1].split("|")
            profit_str = subparts[0].replace("USD", "").strip()
            balance_str = subparts[1].replace("Balance:", "").replace("USD", "").strip()
            
            return (
                "📊 [TRADE CLOSED REPORT] 📊\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"Asset: {symbol}\n"
                f"Outcome: {emoji} {profit_str} USD\n"
                f"New Balance: {balance_str} USD\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
        # Trade opened
        elif msg.startswith("📈"):
            parts = msg.replace("📈", "").strip().split()
            direction = parts[0]
            symbol = parts[1]
            volume = parts[2]
            price = parts[5]
            
            bracket_idx = msg.find("[")
            risk_val = "N/A"
            qtp_val = "N/A"
            if bracket_idx != -1:
                bracket_content = msg[bracket_idx+1:-1]
                sub_parts = bracket_content.split(",")
                for sp in sub_parts:
                    if "Risk:" in sp:
                        risk_val = sp.split("Risk:")[1].strip()
                    if "QTP:" in sp:
                        qtp_val = sp.split("QTP:")[1].strip()
            
            return (
                "🚀 [TRADE EXECUTED] 🚀\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"Action: {direction} {symbol}\n"
                f"Volume: {volume} Lots\n"
                f"Price: {price}\n"
                f"Risk: {risk_val}\n"
                f"Setup Quality: {qtp_val}/100\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
        # Bot startup
        elif "Bot online" in msg:
            balance = msg.split("Balance")[1].split("USD")[0].strip()
            symbols = msg.split("Hunting:")[1].strip()
            return (
                "🤖 [BOT SYSTEM ONLINE] 🤖\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Status: Operational\n"
                f"Initial Balance: {balance} USD\n"
                f"Assets: {symbols}\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
        # Daily target achieved
        elif "Trailing Daily Profit hit" in msg:
            pnl = msg.split("Locked in")[1].split("profit")[0].strip()
            balance = msg.split("Balance:")[1].strip()
            return (
                "🎯 [DAILY GOAL COMPLETED] 🎯\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"Secured Profit: {pnl}\n"
                f"Final Balance: {balance} USD\n"
                "Status: Finished for the day.\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
        # Cooldown circuit breaker
        elif msg.startswith("⚠️") and "losses in a row" in msg:
            parts = msg.replace("⚠️", "").strip().split()
            streak = parts[0]
            minutes = "N/A"
            if "Pausing" in msg:
                minutes = msg.split("Pausing")[1].split("min")[0].strip()
            return (
                "⚠️ [CIRCUIT BREAKER TRIGGERED] ⚠️\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"Streak: {streak} Consecutive Losses\n"
                f"Action: Cooldown Initiated ({minutes} min)\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
        # Daily loss limit hit
        elif "Daily loss limit hit" in msg:
            return (
                "🛑 [DRAWDOWN BREACH ALERT] 🛑\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Reason: Daily Loss Limit Reached\n"
                "Action: Closed All Open Positions\n"
                "Status: Paused until tomorrow.\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
        # Bot shutdown
        elif "Bot stopped" in msg:
            return (
                "🛑 [BOT SYSTEM OFFLINE] 🛑\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Status: Stopped safely.\n"
                "━━━━━━━━━━━━━━━━━━━━━"
            )
            
    except Exception:
        pass
    return msg

def notify(msg):
    # Send to Telegram if credentials are set
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?"
                   + urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}))
            urllib.request.urlopen(url, timeout=5)
        except Exception as e:
            log.warning("Telegram failed: %s", e)

    # Send to Facebook Messenger via CallMeBot if key is set
    if FB_API_KEY:
        try:
            fb_msg = beautify_message_for_messenger(msg)
            url = (f"https://api.callmebot.com/facebook/send.php?"
                   + urllib.parse.urlencode({"apikey": FB_API_KEY, "text": fb_msg}))
            urllib.request.urlopen(url, timeout=5)
        except Exception as e:
            log.warning("Facebook notification failed: %s", e)

# ---------------- JOURNAL ----------------
def init_journal():
    if not os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, "w", newline="") as f:
            csv.writer(f).writerow(
                ["time_utc", "symbol", "direction", "volume",
                 "entry", "exit", "profit", "reason", "balance_after"])

def journal_closed_deals():
    """Journal newly closed deals; ignore deals from before bot start."""
    day_start = datetime.combine(state["day"], datetime.min.time(),
                                 tzinfo=timezone.utc)
    deals = mt5.history_deals_get(day_start, datetime.now(timezone.utc)
                                  + timedelta(minutes=5)) or []
    acc = mt5.account_info()
    for d in deals:
        if d.magic != MAGIC or d.entry != mt5.DEAL_ENTRY_OUT \
           or d.ticket in known_deals:
            continue
        known_deals.add(d.ticket)
        if d.time < BOT_START:          # old deal from earlier session
            continue                    # absorb silently, no journal/streak

        # Loss-streak circuit breaker & outcome tracking
        if d.profit < 0:
            state["loss_streak"] += 1
            state["last_trade_loss"][d.symbol] = True
            if state["loss_streak"] >= LOSS_STREAK_MAX:
                state["pause_until"] = time.time() + LOSS_PAUSE_SEC
                state["loss_streak"] = 0
                log.warning("⚠️ %d losses in a row - pausing %d min (choppy market)",
                            LOSS_STREAK_MAX, LOSS_PAUSE_SEC // 60)
                notify(f"⚠️ {LOSS_STREAK_MAX} losses in a row. "
                       f"Pausing {LOSS_PAUSE_SEC // 60} min.")
        else:
            state["loss_streak"] = 0
            state["last_trade_loss"][d.symbol] = False

        direction = "SELL" if d.type == mt5.DEAL_TYPE_SELL else "BUY"
        with open(JOURNAL_FILE, "a", newline="") as f:
            csv.writer(f).writerow(
                [datetime.fromtimestamp(d.time, timezone.utc).isoformat(),
                 d.symbol, direction, d.volume, "", d.price,
                 round(d.profit, 2), d.comment, acc.balance])
        emoji = "✅" if d.profit >= 0 else "❌"
        log.info("%s CLOSED %s %.2f lots profit=%.2f (%s) | balance=%.2f",
                 emoji, d.symbol, d.volume, d.profit, d.comment, acc.balance)
        notify(f"{emoji} {d.symbol} closed: {d.profit:+.2f} USD "
               f"| Balance: {acc.balance:.2f}")

# ---------------- CONNECTION ----------------
def connect():
    if not mt5.initialize(login=LOGIN, server=SERVER, password=PASSWORD, portable=PORTABLE):
        fallback_paths = [
            "C:\\MT5\\terminal64.exe",
            "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe",
            "C:\\Program Files\\Exness MetaTrader 5\\terminal64.exe",
            "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
        ]
        initialized = False
        for path in fallback_paths:
            log.info("Default MT5 init failed. Retrying with path: %s", path)
            if mt5.initialize(path=path, login=LOGIN, server=SERVER, password=PASSWORD, portable=PORTABLE):
                initialized = True
                break
        if not initialized:
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    info = mt5.account_info()
    log.info("Connected: %s | Balance: %.2f %s", info.login, info.balance,
             info.currency)
    for s in SYMBOLS[:]:
        if not mt5.symbol_select(s, True):
            log.warning("Symbol %s not available, removing", s)
            SYMBOLS.remove(s)
    # Subscribe to DXYm for correlation checking
    if not mt5.symbol_select("DXYm", True):
        log.warning("USD Index symbol DXYm not available for correlation check!")
    if not mt5.terminal_info().trade_allowed:
        log.warning("Algo Trading DISABLED in terminal!")
    notify(f"🤖 Bot online. Balance {info.balance:.2f} USD. "
           f"Hunting: {', '.join(SYMBOLS)}")

# ---------------- GUARDS ----------------
def close_all_positions(reason=""):
    """Emergency close all open positions managed by this bot's magic number."""
    positions = mt5.positions_get() or []
    closed_any = False
    for pos in positions:
        if pos.magic == MAGIC:
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is not None:
                is_buy = pos.type == mt5.POSITION_TYPE_BUY
                price = tick.bid if is_buy else tick.ask
                res = mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "position": pos.ticket,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    "price": price,
                    "deviation": DEVIATION,
                    "magic": MAGIC,
                    "comment": reason[:31],
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC
                })
                if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                    log.info("🚨 EMERGENCY CLOSE %s #%d due to: %s", pos.symbol, pos.ticket, reason)
                    closed_any = True
                else:
                    log.warning("⚠️ Emergency close failed for %s #%d: %s", 
                                pos.symbol, pos.ticket, res.comment if res is not None else "None")
    return closed_any

def daily_guard():
    today = datetime.now(timezone.utc).date()
    acc = mt5.account_info()
    if acc is None:
        return False

    if state["day"] != today:
        state.update(day=today, start_balance=acc.balance, trades_today=0,
                     halted=False, halt_reason="", loss_streak=0,
                     pause_until=0.0, profit_locked=False, peak_equity_profit=0.0)
        state["last_trade_loss"] = {s: False for s in SYMBOLS}
        known_deals.clear()
        log.info("New trading day. Start balance: %.2f", acc.balance)
    if state["halted"]:
        return False

    # Drawdown limit checks floating equity
    floating_pnl = (acc.equity - state["start_balance"]) / state["start_balance"]
    realized_pnl = (acc.balance - state["start_balance"]) / state["start_balance"]

    # Drawdown limit checks floating equity (DISABLED - Solution 3)
    # if floating_pnl <= -DAILY_LOSS_LIMIT:
    #     close_all_positions("loss_limit")
    #     state.update(halted=True, halt_reason="loss limit")
    #     log.warning("DAILY LOSS LIMIT (-%.0f%%). Halted.", DAILY_LOSS_LIMIT * 100)
    #     notify(f"🛑 Daily loss limit hit ({floating_pnl*100:.1f}%). All trades closed. Trading halted.")
    #     return False

    # Dynamic Trailing Daily Profit Floor Activation
    max_pnl = max(realized_pnl, floating_pnl)
    if max_pnl >= DAILY_PROFIT_GOAL:
        if not state.get("profit_locked", False):
            state["profit_locked"] = True
            state["peak_equity_profit"] = max_pnl
            log.info("🚀 DAILY PROFIT GOAL reached! Dynamic Trailing Profit mode activated to ride trends.")
            notify("🚀 Daily profit goal reached! Trailing Profit Mode active: riding trend while protecting gains.")

    if state.get("profit_locked", False):
        state["peak_equity_profit"] = max(state.get("peak_equity_profit", 0.0), max_pnl)
        
        # Calculate dynamic trailing floor with noise protection slack
        slack = max(DAILY_PROFIT_MIN_SLACK, state["peak_equity_profit"] * DAILY_PROFIT_TRAIL_PERCENT)
        trailing_floor = state["peak_equity_profit"] - slack
        
        if floating_pnl < trailing_floor:
            close_all_positions("trail_lock")
            state.update(halted=True, halt_reason="trailing profit locked")
            log.info("🎯 Trailing Daily Profit hit! Locked in %.2f%% profit. Done for today.", realized_pnl * 100)
            notify(f"🎯 Trailing Daily Profit hit! Locked in {realized_pnl*100:+.2f}% profit. Balance: {acc.equity:.2f}")
            return False

    hour = datetime.now(timezone.utc).hour
    if not (SESSION_START_UTC <= hour < SESSION_END_UTC):
        return False
    if time.time() < state["pause_until"]:
        return False
    return state["trades_today"] < MAX_TRADES_DAY

# ---------------- SIZING ----------------
def lot_size(symbol, sl_dist, risk_pct):
    acc, sym = mt5.account_info(), mt5.symbol_info(symbol)
    loss_per_lot = (sl_dist / sym.trade_tick_size) * sym.trade_tick_value
    if loss_per_lot <= 0:
        return sym.volume_min
    lots = max(sym.volume_min,
               min(acc.balance * risk_pct / loss_per_lot, sym.volume_max))
    return round(lots // sym.volume_step * sym.volume_step, 2)

# ---------------- ORDERS ----------------
def open_trade(symbol, direction, sl, tp, sl_dist, qtp_score=0):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return
    sym = mt5.symbol_info(symbol)
    if sym is None:
        return

    price = tick.ask if direction == "BUY" else tick.bid
    sl = round(sl, sym.digits)
    tp = round(tp, sym.digits)

    # Robotic Drawdown Protection Sizing: halving risk based on loss streak
    streak = state.get("loss_streak", 0)
    risk_multiplier = 1.0 / (2 ** streak)
    current_risk = RISK_PER_TRADE * risk_multiplier
    
    volume = lot_size(symbol, sl_dist, current_risk)

    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
        "volume": volume, "type": mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price, "sl": sl, "tp": tp, "deviation": DEVIATION, "magic": MAGIC,
        "comment": STRATEGY_MODE.lower() + "_bot", "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC})

    if res is None:
        log.error("Order failed %s: order_send returned None", symbol)
        return

    if res.retcode == mt5.TRADE_RETCODE_DONE:
        state["trades_today"] += 1
        state["last_entry"][symbol] = time.time()
        open_times[res.order] = time.time()
        log.info(">>> %s %s %.2f lots @ %.5f TP=%.5f SL=%.5f [Risk Size: %.2f%%, QTP Score: %d]",
                 direction, symbol, res.volume, price, tp, sl, current_risk * 100, qtp_score)
        if BOT_THOUGHTS:
            log.info(f"🎯 [TRADE EXECUTED] Successfully placed a {direction} trade on {symbol} with size {res.volume:.2f} lots! "
                     f"Our QTP Setup Probability was high at {qtp_score}/100. "
                     f"Initial Stop-Loss is set at {sl:.5f} and Take-Profit at {tp:.5f} (Risking {current_risk * 100:.2f}% of our balance).")
        notify(f"📈 {direction} {symbol} {res.volume} lots @ {price:.5f} [Risk: {current_risk * 100:.2f}%, QTP: {qtp_score}]")
    else:
        log.error("Order failed %s: %s %s", symbol, res.retcode, getattr(res, 'comment', ''))

def partial_close_position(pos, sym_info, close_ratio=0.5):
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return False
        
    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    vol_step = sym_info.volume_step
    
    # Calculate volume to close
    raw_close_vol = pos.volume * close_ratio
    close_vol = round(raw_close_vol // vol_step * vol_step, 2)
    
    # Check bounds
    if close_vol < sym_info.volume_min:
        close_vol = sym_info.volume_min
        
    if pos.volume - close_vol < sym_info.volume_min:
        # Close the entire position if remaining volume would be too small
        close_vol = pos.volume
        
    price = tick.bid if is_buy else tick.ask
    otype = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
    
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "position": pos.ticket,
        "volume": close_vol,
        "type": otype,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "partial_tp",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    })
    
    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
        log.info("✂️ PARTIAL CLOSE %s #%d closed %.2f lots at %.5f",
                 pos.symbol, pos.ticket, close_vol, price)
        return True
    else:
        log.warning("⚠️ Partial close failed for %s #%d: %s",
                    pos.symbol, pos.ticket, res.comment if res is not None else "None")
        return False

def manage_open():
    """Time-stop stalled trades and apply 3-step take profit scale-outs and trailing stop."""
    count = 0
    positions = mt5.positions_get() or []
    current_tickets = set()
    
    for pos in positions:
        if pos.magic != MAGIC:
            continue
        count += 1
        current_tickets.add(pos.ticket)
        
        # 1. Time-stop check
        opened = open_times.get(pos.ticket, pos.time)
        if time.time() - opened > MAX_HOLD_SECONDS:
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is not None:
                is_buy = pos.type == mt5.POSITION_TYPE_BUY
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
                    "position": pos.ticket, "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    "price": tick.bid if is_buy else tick.ask,
                    "deviation": DEVIATION, "magic": MAGIC,
                    "comment": "time_exit", "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC})
                log.info("⏱ Time-stop %s #%s profit=%.2f",
                         pos.symbol, pos.ticket, pos.profit)
                if BOT_THOUGHTS:
                    log.info(f"⏱ [TIME LIMIT REACHED] Trade #{pos.ticket} on {pos.symbol} has been open for {int(time.time() - opened)} seconds "
                             f"(maximum hold time: {MAX_HOLD_SECONDS}s). Force-closing remaining position to protect capital.")
            open_times.pop(pos.ticket, None)
            state["partial_closed_tickets"].pop(pos.ticket, None)
            continue

        symbol = pos.symbol
        cache = indicators.get(symbol)
        if cache is None or cache["atr"] <= 0:
            continue
            
        atr = cache["atr"]
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            continue
            
        sym_info = mt5.symbol_info(symbol)
        if sym_info is None:
            continue
            
        stops_level = sym_info.trade_stops_level * sym_info.point
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        entry_price = pos.price_open
        current_sl = pos.sl
        current_tp = pos.tp
        
        new_sl = current_sl
        current_price = tick.bid if is_buy else tick.ask
        step = state["partial_closed_tickets"].get(pos.ticket, 0)

        # 2. 3-Step Mathematical Scale-Out Check
        if step == 0:
            # Step 1: Target = 1.0 * ATR. Close 30%. Move SL to BE + 0.1 * ATR
            profit_reached = (current_price >= entry_price + 1.0 * atr) if is_buy else (current_price <= entry_price - 1.0 * atr)
            if profit_reached:
                if partial_close_position(pos, sym_info, 0.30):
                    state["partial_closed_tickets"][pos.ticket] = 1
                    target_sl = entry_price + (BREAKEVEN_BUFFER_ATR * atr if is_buy else -BREAKEVEN_BUFFER_ATR * atr)
                    target_sl = round(target_sl, sym_info.digits)
                    
                    if is_buy:
                        if current_price - target_sl < stops_level:
                            target_sl = current_price - stops_level - sym_info.point
                    else:
                        if target_sl - current_price < stops_level:
                            target_sl = current_price + stops_level + sym_info.point
                            
                    target_sl = round(target_sl, sym_info.digits)
                    res_be = mt5.order_send({
                        "action": mt5.TRADE_ACTION_SLTP, "symbol": symbol,
                        "position": pos.ticket, "sl": target_sl, "tp": current_tp
                    })
                    if res_be is not None and res_be.retcode == mt5.TRADE_RETCODE_DONE:
                        log.info("🛡️ Secured Scale-out Step 1 (SL to BE) for %s #%d at %.5f", symbol, pos.ticket, target_sl)
                        if BOT_THOUGHTS:
                            log.info(f"✂️ [SCALE-OUT STEP 1] Trade #{pos.ticket} hit +1.0x ATR target! Closed 30% of size "
                                     f"and moved SL to Breakeven (+0.1x ATR) at {target_sl:.5f}. This trade is now 100% risk-free!")
                    current_sl = target_sl

        elif step == 1:
            # Step 2: Target = 2.0 * ATR. Close 43% of remaining (~30% of initial). Move SL to Entry + 1.0 * ATR
            profit_reached = (current_price >= entry_price + 2.0 * atr) if is_buy else (current_price <= entry_price - 2.0 * atr)
            if profit_reached:
                if partial_close_position(pos, sym_info, 0.43):
                    state["partial_closed_tickets"][pos.ticket] = 2
                    target_sl = entry_price + (1.0 * atr if is_buy else -1.0 * atr)
                    target_sl = round(target_sl, sym_info.digits)
                    
                    if is_buy:
                        if current_price - target_sl < stops_level:
                            target_sl = current_price - stops_level - sym_info.point
                    else:
                        if target_sl - current_price < stops_level:
                            target_sl = current_price + stops_level + sym_info.point
                            
                    target_sl = round(target_sl, sym_info.digits)
                    res_be = mt5.order_send({
                        "action": mt5.TRADE_ACTION_SLTP, "symbol": symbol,
                        "position": pos.ticket, "sl": target_sl, "tp": current_tp
                    })
                    if res_be is not None and res_be.retcode == mt5.TRADE_RETCODE_DONE:
                        log.info("🛡️ Secured Scale-out Step 2 (SL to Lock 1x ATR) for %s #%d at %.5f", symbol, pos.ticket, target_sl)
                        if BOT_THOUGHTS:
                            log.info(f"✂️ [SCALE-OUT STEP 2] Trade #{pos.ticket} hit +2.0x ATR target! Closed another 30% of size "
                                     f"and moved SL to lock in +1.0x ATR profit at {target_sl:.5f}.")
                    current_sl = target_sl

        # 3. Dynamic Trailing Stop using Market Structure Swing Points (Only after Step 2)
        if step >= 2:
            # Fetch M1 rates to find swing high/low pivots
            rates_m1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, SWING_LOOKBACK + 2)
            if rates_m1 is not None and len(rates_m1) >= SWING_LOOKBACK:
                swing_rates = rates_m1[-1 - SWING_LOOKBACK : -1]
                local_low = np.min(swing_rates['low'])
                local_high = np.max(swing_rates['high'])
            else:
                local_low = current_price - TRAIL_DISTANCE_ATR * atr
                local_high = current_price + TRAIL_DISTANCE_ATR * atr

            if is_buy:
                if current_price >= entry_price + TRAIL_TRIGGER_ATR * atr:
                    target_be = entry_price + BREAKEVEN_BUFFER_ATR * atr
                    target_trail = local_low - 0.2 * atr
                    target_sl = max(target_be, target_trail)
                    
                    if current_price - target_sl < stops_level:
                        target_sl = current_price - stops_level - sym_info.point
                    
                    min_step = 0.05 * atr
                    if current_sl == 0 or (target_sl > current_sl + min_step):
                        new_sl = target_sl
            else:
                if current_price <= entry_price - TRAIL_TRIGGER_ATR * atr:
                    target_be = entry_price - BREAKEVEN_BUFFER_ATR * atr
                    target_trail = local_high + 0.2 * atr
                    target_sl = min(target_be, target_trail)
                    
                    if target_sl - current_price < stops_level:
                        target_sl = current_price + stops_level + sym_info.point
                    
                    min_step = 0.05 * atr
                    if current_sl == 0 or (target_sl < current_sl - min_step):
                        new_sl = target_sl
                        
        if new_sl != current_sl:
            new_sl = round(new_sl, sym_info.digits)
            if is_buy:
                if tick.bid - new_sl < stops_level:
                    continue
            else:
                if new_sl - tick.ask < stops_level:
                    continue
                    
            res = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "position": pos.ticket,
                "sl": new_sl,
                "tp": current_tp
            })
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                log.info("🛡️ Updated SL for %s #%d to %.5f (ATR trail)",
                         symbol, pos.ticket, new_sl)
                if BOT_THOUGHTS:
                    log.info(f"🛡️ [TRAILING STOP UPDATED] Moved Stop-Loss for #{pos.ticket} behind recent market structure "
                             f"swing point to {new_sl:.5f} to secure accumulated run gains.")
            else:
                log.warning("⚠️ Failed to update SL for %s #%d: %s",
                            symbol, pos.ticket, res.comment if res is not None else "None")
                            
    # Clean up stale tickets from partial_closed_tickets dict
    dead_tickets = set(state["partial_closed_tickets"].keys()) - current_tickets
    for ticket in dead_tickets:
        state["partial_closed_tickets"].pop(ticket, None)
        
    return count

# ---------------- MAIN LOOP ----------------
def run():
    connect()
    init_journal()
    
    # Pre-populate technical indicators cache before starting
    update_news()
    update_indicators()
    
    log.info("%s Bot live | %s | session %02d:00-%02d:00 UTC | "
             "stop day at -%.0f%% or +%.0f%%",
             STRATEGY_MODE.capitalize(), ", ".join(SYMBOLS), SESSION_START_UTC, SESSION_END_UTC,
             DAILY_LOSS_LIMIT * 100, DAILY_PROFIT_GOAL * 100)
    n = 0
    while True:
        try:
            update_news()
            update_indicators()
            open_count = manage_open()
            can_trade = daily_guard()
            journal_closed_deals()

            for symbol in SYMBOLS:
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    continue
                sym_info = mt5.symbol_info(symbol)
                if sym_info is None:
                    continue
                spread = tick.ask - tick.bid
                mid = (tick.ask + tick.bid) / 2

                # Get indicators cache
                cache = indicators.get(symbol)
                if cache is None or cache["atr"] <= 0:
                    continue

                atr = cache["atr"]
                ema200_m5 = cache["ema200_m5"]
                ema50_m15 = cache["ema50_m15"]
                ema50_h1 = cache["ema50_h1"]
                adx = cache["adx"]
                rsi_m15 = cache["rsi_m15"]
                avg_spread = cache["avg_spread"]

                # Get M1 rates to inspect setups
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 100)
                if rates is None or len(rates) < 50:
                    continue

                # Determine strategy mode dynamically
                active_mode = STRATEGY_MODE
                if STRATEGY_MODE == "AUTO":
                    if adx >= 25:
                        hour = datetime.now(timezone.utc).hour
                        if (0 <= hour < 2) or (7 <= hour < 9) or (13 <= hour < 15):
                            active_mode = "ORB"
                        else:
                            active_mode = "BREAKOUT"
                    else:
                        bull_obs, bear_obs = find_active_order_blocks(rates, atr)
                        bull_fvgs, bear_fvgs = find_active_fvgs(rates)
                        if len(bull_obs) > 0 or len(bear_obs) > 0:
                            active_mode = "OB"
                        elif len(bull_fvgs) > 0 or len(bear_fvgs) > 0:
                            active_mode = "SMC"
                        else:
                            active_mode = "SWEEP"

                # Check if a new M1 candle opened to print analysis
                last_t = state["last_commentary_time"].get(symbol, 0)
                current_candle_t = int(rates[-2]['time'])
                if BOT_THOUGHTS and current_candle_t != last_t:
                    state["last_commentary_time"][symbol] = current_candle_t

                    # 1. Trend Analysis
                    trend_dir = "NEUTRAL"
                    if mid > ema200_m5 and mid > ema50_m15 and mid > ema50_h1:
                        trend_dir = "BULLISH"
                    elif mid < ema200_m5 and mid < ema50_m15 and mid < ema50_h1:
                        trend_dir = "BEARISH"

                    # 2. Market Regime & Strategy Decision
                    regime_desc = f"ADX is {adx:.1f} (Trend is {'strong' if adx >= 25 else 'weak'}). Chose '{active_mode}' strategy."

                    # 3. Strategy specific details
                    strat_thoughts = ""
                    if active_mode == "OB":
                        bull_obs, bear_obs = find_active_order_blocks(rates, atr)
                        strat_thoughts = f"Scanning for Order Blocks: Found {len(bull_obs)} bullish / {len(bear_obs)} bearish active zones."
                        if len(bull_obs) > 0 and trend_dir == "BULLISH":
                            ob = bull_obs[-1]
                            strat_thoughts += f" Waiting for price to pull back and touch Bullish OB ({ob['floor']:.2f} - {ob['ceiling']:.2f}). Current: {mid:.2f} (diff: {(mid - ob['ceiling']):.2f})."
                        elif len(bear_obs) > 0 and trend_dir == "BEARISH":
                            ob = bear_obs[-1]
                            strat_thoughts += f" Waiting for price to pull back and touch Bearish OB ({ob['floor']:.2f} - {ob['ceiling']:.2f}). Current: {mid:.2f} (diff: {(ob['floor'] - mid):.2f})."
                    elif active_mode == "SMC":
                        bull_fvgs, bear_fvgs = find_active_fvgs(rates)
                        strat_thoughts = f"Scanning for Fair Value Gaps: Found {len(bull_fvgs)} bullish / {len(bear_fvgs)} bearish active FVGs."
                        if len(bull_fvgs) > 0 and trend_dir == "BULLISH":
                            fvg = bull_fvgs[-1]
                            strat_thoughts += f" Waiting for price to mitigate Bullish FVG ({fvg['floor']:.2f} - {fvg['ceiling']:.2f}). Current: {mid:.2f}."
                        elif len(bear_fvgs) > 0 and trend_dir == "BEARISH":
                            fvg = bear_fvgs[-1]
                            strat_thoughts += f" Waiting for price to mitigate Bearish FVG ({fvg['floor']:.2f} - {fvg['ceiling']:.2f}). Current: {mid:.2f}."
                    elif active_mode == "SWEEP":
                        sweep_rates = rates[-2 - SWEEP_PERIOD : -2]
                        highest_high = np.max(sweep_rates['high'])
                        lowest_low = np.min(sweep_rates['low'])
                        strat_thoughts = f"Liquidity Sweep Search: Recent M1 swing high is {highest_high:.2f}, low is {lowest_low:.2f}. "
                        if trend_dir == "BULLISH":
                            strat_thoughts += f"Waiting for price to sweep below {lowest_low:.2f} and reject it."
                        else:
                            strat_thoughts += f"Waiting for price to sweep above {highest_high:.2f} and reject it."
                    elif active_mode == "BREAKOUT":
                        donchian_rates = rates[-1 - DONCHIAN_PERIOD : -1]
                        highest_high = np.max(donchian_rates['high'])
                        lowest_low = np.min(donchian_rates['low'])
                        strat_thoughts = f"Donchian Channel: Upper bound {highest_high:.2f}, lower bound {lowest_low:.2f}. Waiting for breakout. Current: {mid:.2f}."
                    elif active_mode == "ORB":
                        orb_range = get_orb_ranges(symbol)
                        hour = datetime.now(timezone.utc).hour
                        if hour >= 13:
                            active_session = "ny"
                        elif hour >= 7:
                            active_session = "london"
                        else:
                            active_session = "tokyo"
                        high_key = f"{active_session}_high"
                        low_key = f"{active_session}_low"
                        if high_key in orb_range:
                            strat_thoughts = f"ORB boundaries: {orb_range[low_key]:.2f} - {orb_range[high_key]:.2f}. Waiting for session breakout."
                        else:
                            strat_thoughts = f"ORB: Waiting to establish session {active_session.upper()} range (07:00 or 13:00 UTC)."
                    elif active_mode == "BOUNCE":
                        strat_thoughts = f"EMA Pullback Search: Waiting for price to touch M1 EMA 50 ({cache['ema50']:.2f}) and bounce in trend direction ({trend_dir}). Current: {mid:.2f}."

                    # 4. Warnings / Block reasons
                    warnings = []
                    acc_info = mt5.account_info()
                    pnl_pct = (acc_info.equity - state["start_balance"]) / state["start_balance"] if state["start_balance"] > 0 else 0
                    
                    if state["halted"]:
                        warnings.append(f"🛑 Trading is halted today: {state['halt_reason']}.")
                    elif pnl_pct <= -DAILY_LOSS_LIMIT:
                        warnings.append("🛑 Daily loss limit reached.")
                    elif state.get("profit_locked", False):
                        peak_p = state.get("peak_equity_profit", 0.0)
                        slack = max(DAILY_PROFIT_MIN_SLACK, peak_p * DAILY_PROFIT_TRAIL_PERCENT)
                        t_floor = peak_p - slack
                        warnings.append(f"🚀 Trailing Profit Mode active: Peak: {peak_p*100:.2f}%, Trail Floor: {t_floor*100:.2f}%, Current PNL: {pnl_pct*100:+.2f}%.")
                    elif pnl_pct >= DAILY_PROFIT_GOAL:
                        warnings.append("🎯 Daily profit goal reached.")
                    
                    hour = datetime.now(timezone.utc).hour
                    if not (SESSION_START_UTC <= hour < SESSION_END_UTC):
                        warnings.append(f"💤 Outside session hours (trades 07:00-17:00 UTC). Current hour: {hour:02d}:00 UTC.")
                    
                    if time.time() < state["pause_until"]:
                        warnings.append(f"⏳ Cooldown pause for another {int(state['pause_until'] - time.time())}s (choppy market).")
                    
                    symbol_cooldown = COOLDOWN_SEC * 3 if state["last_trade_loss"].get(symbol, False) else COOLDOWN_SEC
                    time_since_last_trade = time.time() - state["last_entry"][symbol]
                    if time_since_last_trade < symbol_cooldown:
                        warnings.append(f"⏳ Cooldown active for {symbol}: wait {int(symbol_cooldown - time_since_last_trade)}s.")
                    
                    if open_count >= MAX_OPEN_TOTAL:
                        warnings.append(f"🚫 Max open positions limit reached ({open_count}/{MAX_OPEN_TOTAL}).")
                    
                    if any(p.symbol == symbol and p.magic == MAGIC for p in mt5.positions_get(symbol=symbol) or []):
                        warnings.append(f"🚫 Already have an active trade open for {symbol}.")
                    
                    paused, news_title = is_news_paused(symbol)
                    if paused:
                        warnings.append(f"⚠️ Paused due to news: '{news_title}'.")
                    
                    if avg_spread > 0 and spread > 1.5 * avg_spread:
                        warnings.append(f"⚠️ Spread widened to {spread/sym_info.point:.1f} points (limit: {1.5*avg_spread/sym_info.point:.1f} points).")
                    elif spread / atr > SPREAD_ATR_LIMIT:
                        warnings.append(f"⚠️ Spread/ATR ratio too high: {spread/atr:.2f} (limit: {SPREAD_ATR_LIMIT:.2f}).")
                    
                    if rsi_m15 >= 75:
                        warnings.append(f"⚠️ Retail buying exhaustion (RSI M15: {rsi_m15:.1f} >= 75). Blocking BUY entries.")
                    elif rsi_m15 <= 25:
                        warnings.append(f"⚠️ Retail selling exhaustion (RSI M15: {rsi_m15:.1f} <= 25). Blocking SELL entries.")

                    dxy_rates_copy = mt5.copy_rates_from_pos("DXYm", mt5.TIMEFRAME_M1, 0, 5)
                    if dxy_rates_copy is not None and len(dxy_rates_copy) >= 3:
                        dxy_c = dxy_rates_copy[-1]['close'] - dxy_rates_copy[-3]['close']
                        if dxy_c > DXY_VELOCITY_LIMIT:
                            warnings.append(f"⚡ DXY Index pumping rapidly (+{dxy_c:.4f}). Blocking BUY setups.")
                        elif dxy_c < -DXY_VELOCITY_LIMIT:
                            warnings.append(f"⚡ DXY Index dumping rapidly ({dxy_c:.4f}). Blocking SELL setups.")

                    # Print compiled thoughts
                    warn_str = "\n   -> STATUS GUARDS: " + " | ".join(warnings) if warnings else ""
                    log.info(f"\n🧠 [BOT BRAIN - {symbol} ANALYSIS]\n"
                             f"   -> Trend Alignment: {trend_dir} (M5/M15/H1)\n"
                             f"   -> Market Regime: {regime_desc}\n"
                             f"   -> Scanning Thoughts: {strat_thoughts}{warn_str}\n"
                             f"   -> Current Spread: {spread/sym_info.point:.1f} points (Average: {avg_spread/sym_info.point:.1f}) | M1 ATR: {atr:.4f}\n")

                # Dynamic cooldown check: triple delay after a loss on this symbol
                symbol_cooldown = COOLDOWN_SEC
                if state["last_trade_loss"].get(symbol, False):
                    symbol_cooldown = COOLDOWN_SEC * 3

                if (not can_trade or open_count >= MAX_OPEN_TOTAL
                        or time.time() - state["last_entry"][symbol] < symbol_cooldown):
                    continue
                if any(p.symbol == symbol and p.magic == MAGIC
                       for p in mt5.positions_get(symbol=symbol) or []):
                    continue

                # News Guard
                paused, news_title = is_news_paused(symbol)
                if paused:
                    if n % 300 == 0:
                        log.info("Symbol %s paused due to high-impact news: %s", symbol, news_title)
                    continue

                # Dynamic Spread Widening Guard
                if avg_spread > 0 and spread > 1.5 * avg_spread:
                    continue
                if spread / atr > SPREAD_ATR_LIMIT:
                    continue

                # Synthetic Retail Sentiment (SRS) extreme exhaustion guards
                if rsi_m15 >= 75:  # Retail buying climax -> block buy
                    continue
                if rsi_m15 <= 25:  # Retail selling panic climax -> block sell
                    continue

                # Compute RSI for divergence check
                rsi = compute_rsi(rates, RSI_PERIOD)

                # Common setup candle values (rates[-2] is the last completed candle)
                setup_candle = rates[-2]
                c_open  = setup_candle['open']
                c_high  = setup_candle['high']
                c_low   = setup_candle['low']
                c_close = setup_candle['close']
                c_vol   = setup_candle['tick_volume']

                c_range = c_high - c_low
                c_body  = abs(c_close - c_open)

                # Setup candle filters (size and body ratio)
                if c_range < MIN_CANDLE_RANGE_ATR * atr:
                    continue
                if c_range > 0 and (c_body / c_range) < MIN_BODY_RATIO:
                    continue

                # Relative Volume filter
                past_volumes = rates['tick_volume'][-22:-2]
                avg_volume = np.mean(past_volumes)
                rvol = c_vol / avg_volume if avg_volume > 0 else 1.0
                if rvol < RVOL_LIMIT:
                    continue

                # DXY Alignment checks
                dxy_bullish = state.get("dxy_bullish", True)
                is_inverse = not symbol.startswith("USD")
                dxy_buy_aligned = (not dxy_bullish) if is_inverse else dxy_bullish
                dxy_sell_aligned = dxy_bullish if is_inverse else (not dxy_bullish)

                # DXY Velocity Check
                dxy_velocity_blocked_buy = False
                dxy_velocity_blocked_sell = False
                dxy_rates = mt5.copy_rates_from_pos("DXYm", mt5.TIMEFRAME_M1, 0, 5)
                if dxy_rates is not None and len(dxy_rates) >= 3:
                    dxy_change = dxy_rates[-1]['close'] - dxy_rates[-3]['close']
                    if dxy_change > DXY_VELOCITY_LIMIT:
                        dxy_velocity_blocked_buy = True
                        if n % 300 == 0:
                            log.info("DXY is pumping rapidly (change: %.4f) | Blocking Buy setups", dxy_change)
                    elif dxy_change < -DXY_VELOCITY_LIMIT:
                        dxy_velocity_blocked_sell = True
                        if n % 300 == 0:
                            log.info("DXY is dumping rapidly (change: %.4f) | Blocking Sell setups", dxy_change)

                # Strategy Mode Selector (Regime Switcher)
                active_mode = STRATEGY_MODE
                if STRATEGY_MODE == "AUTO":
                    if adx >= 25:
                        # Trending regime
                        hour = datetime.now(timezone.utc).hour
                        if (0 <= hour < 2) or (7 <= hour < 9) or (13 <= hour < 15):
                            active_mode = "ORB"
                        else:
                            active_mode = "BREAKOUT"
                    else:
                        # Ranging / Pullback regime (Prioritize OB, then SMC, then SWEEP)
                        bull_obs, bear_obs = find_active_order_blocks(rates, atr)
                        bull_fvgs, bear_fvgs = find_active_fvgs(rates)
                        if len(bull_obs) > 0 or len(bear_obs) > 0:
                            active_mode = "OB"
                        elif len(bull_fvgs) > 0 or len(bear_fvgs) > 0:
                            active_mode = "SMC"
                        else:
                            active_mode = "SWEEP"

                # Strategy Mode execution
                if active_mode == "BREAKOUT":
                    # Donchian channel boundaries over the last completed DONCHIAN_PERIOD candles
                    donchian_rates = rates[-1 - DONCHIAN_PERIOD : -1]
                    highest_high = np.max(donchian_rates['high'])
                    lowest_low = np.min(donchian_rates['low'])

                    ema50_arr = compute_ema(rates, EMA_PERIOD)

                    buy_score = get_qtp_score(symbol, "BUY", mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned, adx, rvol, rsi_m15)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15)

                    # BUY condition
                    if (buy_score >= QTP_THRESHOLD and not dxy_velocity_blocked_buy and mid > ema50_arr[-1] and 
                        c_close > c_open and tick.ask > highest_high):
                        entry_price = tick.ask
                        sl = entry_price - SL_ATR_MULT * atr
                        tp = entry_price + TP_ATR_MULT * atr
                        sl_dist = entry_price - sl
                        open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                        open_count += 1

                    # SELL condition
                    elif (sell_score >= QTP_THRESHOLD and not dxy_velocity_blocked_sell and mid < ema50_arr[-1] and 
                          c_close < c_open and tick.bid < lowest_low):
                        entry_price = tick.bid
                        sl = entry_price + SL_ATR_MULT * atr
                        tp = entry_price - TP_ATR_MULT * atr
                        sl_dist = sl - entry_price
                        open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                        open_count += 1

                elif active_mode == "SWEEP":
                    # Liquidity Sweep Reversal strategy
                    sweep_rates = rates[-2 - SWEEP_PERIOD : -2]
                    highest_high = np.max(sweep_rates['high'])
                    lowest_low = np.min(sweep_rates['low'])

                    buy_score = get_qtp_score(symbol, "BUY", mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned, adx, rvol, rsi_m15)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15)

                    # BUY condition
                    if (buy_score >= QTP_THRESHOLD and not dxy_velocity_blocked_buy):
                        lower_wick = min(c_open, c_close) - c_low
                        wick_ratio = lower_wick / c_range if c_range > 0 else 0
                        
                        if (c_low < lowest_low and c_close > lowest_low and c_close > c_open and wick_ratio >= SWEEP_WICK_RATIO):
                            min_idx_in_range = np.argmin(sweep_rates['low'])
                            prev_low_idx = len(rates) - 2 - SWEEP_PERIOD + min_idx_in_range
                            rsi_divergence = rsi[-2] > rsi[prev_low_idx]
                            
                            if rsi_divergence:
                                if tick.ask > c_high:
                                    entry_price = tick.ask
                                    sl = min(c_low - 0.2 * atr, entry_price - SL_ATR_MULT * atr)
                                    tp = entry_price + TP_ATR_MULT * atr
                                    sl_dist = entry_price - sl
                                    open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                    open_count += 1

                    # SELL condition
                    elif (sell_score >= QTP_THRESHOLD and not dxy_velocity_blocked_sell):
                        upper_wick = c_high - max(c_open, c_close)
                        wick_ratio = upper_wick / c_range if c_range > 0 else 0
                        
                        if (c_high > highest_high and c_close < highest_high and c_close < c_open and wick_ratio >= SWEEP_WICK_RATIO):
                            max_idx_in_range = np.argmax(sweep_rates['high'])
                            prev_high_idx = len(rates) - 2 - SWEEP_PERIOD + max_idx_in_range
                            rsi_divergence = rsi[-2] < rsi[prev_high_idx]
                            
                            if rsi_divergence:
                                if tick.bid < c_low:
                                    entry_price = tick.bid
                                    sl = max(c_high + 0.2 * atr, entry_price + SL_ATR_MULT * atr)
                                    tp = entry_price - TP_ATR_MULT * atr
                                    sl_dist = sl - entry_price
                                    open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                    open_count += 1

                elif active_mode == "SMC":
                    # Smart Money Concepts: Fair Value Gap Mitigation
                    bull_fvgs, bear_fvgs = find_active_fvgs(rates)
                    
                    buy_score = get_qtp_score(symbol, "BUY", mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned, adx, rvol, rsi_m15)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15)
                    
                    # BUY condition: FVG mitigation & rejection
                    if buy_score >= QTP_THRESHOLD and not dxy_velocity_blocked_buy and len(bull_fvgs) > 0:
                        fvg = bull_fvgs[-1]
                        if c_low <= fvg['ceiling'] and c_close > fvg['floor'] and c_close > c_open:
                            if tick.ask > c_high:
                                entry_price = tick.ask
                                sl = min(c_low - 0.2 * atr, entry_price - SL_ATR_MULT * atr)
                                tp = entry_price + TP_ATR_MULT * atr
                                  # Fix formatting
                                sl_dist = entry_price - sl
                                open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                open_count += 1
                                
                    # SELL condition: FVG mitigation & rejection
                    elif sell_score >= QTP_THRESHOLD and not dxy_velocity_blocked_sell and len(bear_fvgs) > 0:
                        fvg = bear_fvgs[-1]
                        if c_high >= fvg['floor'] and c_close < fvg['ceiling'] and c_close < c_open:
                            if tick.bid < c_low:
                                entry_price = tick.bid
                                sl = max(c_high + 0.2 * atr, entry_price + SL_ATR_MULT * atr)
                                tp = entry_price - TP_ATR_MULT * atr
                                sl_dist = sl - entry_price
                                open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                open_count += 1

                elif active_mode == "ORB":
                    # Opening Range Breakout
                    orb_range = get_orb_ranges(symbol)
                    
                    buy_score = get_qtp_score(symbol, "BUY", mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned, adx, rvol, rsi_m15)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15)
                    
                    hour = datetime.now(timezone.utc).hour
                    if hour >= 13:
                        active_session = "ny"
                    elif hour >= 7:
                        active_session = "london"
                    else:
                        active_session = "tokyo"
                    
                    high_key = f"{active_session}_high"
                    low_key = f"{active_session}_low"
                    
                    if high_key in orb_range:
                        range_high = orb_range[high_key]
                        range_low = orb_range[low_key]
                        
                        # BUY condition: breakout of range high
                        if buy_score >= QTP_THRESHOLD and not dxy_velocity_blocked_buy and tick.ask > range_high and c_close > c_open:
                            entry_price = tick.ask
                            sl = entry_price - SL_ATR_MULT * atr
                            tp = entry_price + TP_ATR_MULT * atr
                            sl_dist = entry_price - sl
                            open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                            open_count += 1
                            
                        # SELL condition: breakout of range low
                        elif sell_score >= QTP_THRESHOLD and not dxy_velocity_blocked_sell and tick.bid < range_low and c_close < c_open:
                            entry_price = tick.bid
                            sl = entry_price + SL_ATR_MULT * atr
                            tp = entry_price - TP_ATR_MULT * atr
                            sl_dist = sl - entry_price
                            open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                            open_count += 1

                elif active_mode == "OB":
                    # Smart Money Concepts: Order Block Mitigation
                    bull_obs, bear_obs = find_active_order_blocks(rates, atr)
                    
                    buy_score = get_qtp_score(symbol, "BUY", mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned, adx, rvol, rsi_m15)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15)
                    
                    # BUY condition: Price tests Bullish OB ceiling and rejects it
                    if buy_score >= QTP_THRESHOLD and not dxy_velocity_blocked_buy and len(bull_obs) > 0:
                        ob = bull_obs[-1]
                        if c_low <= ob['ceiling'] and c_close > ob['floor'] and c_close > c_open:
                            if tick.ask > c_high:
                                entry_price = tick.ask
                                sl = min(c_low - 0.2 * atr, entry_price - SL_ATR_MULT * atr)
                                tp = entry_price + TP_ATR_MULT * atr
                                sl_dist = entry_price - sl
                                open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                open_count += 1
                                
                    # SELL condition: Price tests Bearish OB floor and rejects it
                    elif sell_score >= QTP_THRESHOLD and not dxy_velocity_blocked_sell and len(bear_obs) > 0:
                        ob = bear_obs[-1]
                        if c_high >= ob['floor'] and c_close < ob['ceiling'] and c_close < c_open:
                            if tick.bid < c_low:
                                entry_price = tick.bid
                                sl = max(c_high + 0.2 * atr, entry_price + SL_ATR_MULT * atr)
                                tp = entry_price - TP_ATR_MULT * atr
                                sl_dist = sl - entry_price
                                open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                open_count += 1

                else: # active_mode == "BOUNCE"
                    # Pullback Bounce strategy
                    ema50_arr = compute_ema(rates, EMA_PERIOD)
                    ema50_val = ema50_arr[-2]

                    buy_score = get_qtp_score(symbol, "BUY", mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned, adx, rvol, rsi_m15)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15)

                    # BUY condition
                    if (buy_score >= QTP_THRESHOLD and not dxy_velocity_blocked_buy and mid > ema50_arr[-1]):
                        # Touch and bounce check
                        if (c_low <= ema50_val and c_close > ema50_val and c_close > c_open):
                            # Trigger: breaks setup high
                            if tick.ask > c_high:
                                entry_price = tick.ask
                                sl = min(c_low - 0.2 * atr, entry_price - SL_ATR_MULT * atr)
                                tp = entry_price + TP_ATR_MULT * atr
                                sl_dist = entry_price - sl
                                open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                open_count += 1

                    # SELL condition
                    elif (sell_score >= QTP_THRESHOLD and not dxy_velocity_blocked_sell and mid < ema50_arr[-1]):
                        # Touch and bounce check
                        if (c_high >= ema50_val and c_close < ema50_val and c_close < c_open):
                            # Trigger: breaks setup low
                            if tick.bid < c_low:
                                entry_price = tick.bid
                                sl = max(c_high + 0.2 * atr, entry_price + SL_ATR_MULT * atr)
                                tp = entry_price - TP_ATR_MULT * atr
                                sl_dist = sl - entry_price
                                open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                open_count += 1

            n += 1
            if n % 30 == 0:  # status every ~30s
                acc = mt5.account_info()
                hour = datetime.now(timezone.utc).hour
                equity = acc.equity if acc is not None else 0.0
                if state["halted"]:
                    status = f"halted ({state['halt_reason']})"
                elif not (SESSION_START_UTC <= hour < SESSION_END_UTC):
                    status = "outside session (trades 13:00-23:00 your time)"
                elif time.time() < state["pause_until"]:
                    status = "chop pause"
                else:
                    status = "hunting"
                log.info("Status: %s | equity=%.2f | trades today=%d | open=%d",
                         status, equity, state["trades_today"], open_count)
            # Run duration limit check for Github Actions
            if RUN_DURATION_HOURS > 0 and (time.time() - BOT_START) > RUN_DURATION_HOURS * 3600:
                log.info("⏳ Run duration limit reached (%s hours). Initiating clean shutdown...", RUN_DURATION_HOURS)
                close_all_positions("duration_limit")
                break

            time.sleep(SCAN_SECONDS)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            notify("🤖 Bot stopped.")
            break
        except Exception as e:
            log.exception("Loop error: %s", e)
            time.sleep(10)
    mt5.shutdown()

if __name__ == "__main__":
    run()
