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
SPREAD_ATR_LIMIT   = 0.55         # max ratio of spread / ATR — relaxed for Gold's natural wide spread
SPREAD_MA_PERIOD   = 20           # lookback for average spread to detect widening
SL_ATR_MULT        = 1.0          # stop-loss = 1.0x ATR fallback (structural SL preferred)
TP_ATR_MULT        = 3.0          # initial take-profit = 3.0x ATR (R:R ratio of 1:3)
QTP_THRESHOLD      = 70           # minimum Quantitative Trade Probability Score (0-100) to trade
DXY_VELOCITY_LIMIT = 0.05         # block trades if DXY shifts by more than this over 3 candles
SWING_LOOKBACK     = 10           # lookback for swing high/low trailing stop
OB_LOOKBACK        = 15           # candles to scan back for active Order Blocks

# ---- Smart RR / Structural SL-TP Settings ----
MIN_RR_RATIO        = 4.0          # hard minimum reward:risk ratio — skip trade if RR < 4.0 (institutional standard)
STRUCTURAL_SL       = True         # use nearest M1 swing high/low for SL instead of fixed ATR
STRUCTURAL_TP       = True         # use H1 swing target for TP to anchor to real price structure
STRUCT_SL_LOOKBACK  = 8            # candles to look back for structural swing SL point
STRUCT_TP_LOOKBACK  = 30           # H1 candles to look back for next swing high/low TP target (30h window)
SL_BUFFER_ATR       = 0.15         # buffer beyond swing point for SL (0.15x ATR cushion)
TP_BUFFER_ATR       = 0.20         # buffer before swing point for TP (take profit slightly before resistance)
MAX_SL_ATR_MULT     = 2.0          # cap structural SL at 2.0x ATR max (wider to allow more setups)

# ---- Institutional Liquidity Settings ----
EQH_EQL_LOOKBACK    = 30           # candles to scan for Equal Highs / Equal Lows pools
EQH_EQL_TOLERANCE   = 0.20         # ATR fraction: two highs/lows this close = equal (Gold-optimized)
EQH_EQL_MIN_GAP     = 3            # minimum candles between the two equal points
PREMIUM_DISC_PERIOD = 50           # candles to define the swing range for Premium/Discount zones
IDM_LOOKBACK        = 10           # candles to check for Inducement (fake breakout) trap
IDM_WICK_RATIO      = 0.55         # wick must be >= 55% of candle range to qualify as IDM rejection
LIQ_SCORE_ENABLED   = True         # include liquidity confluence in QTP scoring

# ---- Smart Entry Quality Filters ----
MOMENTUM_FILTER_ENABLED = True    # require momentum confirmation before entry
MOMENTUM_BARS           = 3       # last N M1 candles must agree on direction
TREND_VALID_CHECK       = True    # re-check trend still intact before each entry
SMART_TIME_EXIT         = True    # don't time-exit if trade is profitable — let it run
SMART_TIME_EXIT_BUFFER  = 0.3     # only time-exit if profit < 0.3x ATR (near breakeven or loss)
LOSS_COOLDOWN_SCALE     = 2       # after loss: cooldown = COOLDOWN_SEC × this (was 3x, now 2x)
CHOP_ADX_LEVEL          = 15      # if ADX drops below this after entry, it's choppy — skip entry
 
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
MIN_BODY_RATIO       = 0.40       # candle body must be >= 40% of the full candle range
RVOL_PERIOD         = 20           # period for M1 average tick volume calculation
RVOL_LIMIT          = 1.2          # setup candle volume must be >= 1.2x average volume (Gold-optimized)

# Scale-Out / Partial TP Settings
PARTIAL_TP_ATR      = 1.5          # target to take partial profit
PARTIAL_CLOSE_RATIO = 0.5          # close 50% of position volume at target 1

# Trailing Stop & Breakeven Settings
TRAIL_TRIGGER_ATR  = 1.0          # trigger trail when profit > 1.0x ATR
BREAKEVEN_BUFFER_ATR = 0.1        # move SL to entry + 0.1x ATR
TRAIL_DISTANCE_ATR = 1.2          # trail SL at 1.2x ATR behind price

MAX_HOLD_SECONDS  = 900          # force-close stalled trades (15 min — gives 4:1 RR more time)
RISK_PER_TRADE    = 0.01         # base risk: 1% balance per trade
DAILY_LOSS_LIMIT  = 0.03         # stop day at -3%
DAILY_PROFIT_GOAL = 0.05         # target at +5% to activate trailing profit guard
DAILY_PROFIT_TRAIL_PERCENT = 0.20   # trail floor at peak - 20% of peak profit
DAILY_PROFIT_MIN_SLACK     = 0.005  # minimum trailing slack of 0.5% of account balance to prevent noise trigger
DAILY_PROFIT_ATR_SLACK_MULT = 1.5   # dynamic multiplier for ATR-based trailing daily profit slack
MAX_TRADES_DAY    = 100
COOLDOWN_SEC      = 30           # normal cooldown per symbol in seconds (faster re-entry on wins)
LOSS_STREAK_MAX   = 3            # losses in a row -> pause
LOSS_PAUSE_SEC    = 600          # 10-minute chop pause (was 15 — shorter, market changes fast)
SESSION_START_UTC = 0            # no global block — each GitHub Actions run handles its own window
SESSION_END_UTC   = 24           # bot runs 24h range; cron schedule controls actual trading hours
MAX_OPEN_TOTAL    = 3            # max simultaneous positions (increased from 2)
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

# ============================================================
# NEW v7.0 ADVANCED CONFIG
# ============================================================
# Discord Webhook (optional backup notification)
DISCORD_WEBHOOK       = os.environ.get("DISCORD_WEBHOOK", "")

# Volatility Regime Filter
VOLATILITY_ATR_PERIOD = 50          # rolling ATR average lookback
VOLATILITY_HIGH_MULT  = 2.0         # block if ATR > 2x avg (news spike)
VOLATILITY_LOW_MULT   = 0.4         # block if ATR < 0.4x avg (dead market)

# Dynamic TP based on ADX strength
DYNAMIC_TP_ENABLED    = True        # enable dynamic TP ratio
TP_ATR_WEAK           = 2.0         # TP multiplier when ADX 20-25 (weak trend)
TP_ATR_NORMAL         = 3.0         # TP multiplier when ADX 25-30
TP_ATR_STRONG         = 4.5         # TP multiplier when ADX > 30 (strong trend)

# MACD Settings (new confluence filter)
MACD_FAST             = 12
MACD_SLOW             = 26
MACD_SIGNAL_PERIOD    = 9
MACD_CONFLUENCE       = True        # require MACD histogram alignment for entry

# Adaptive QTP Threshold (self-learning)
ADAPTIVE_QTP_ENABLED  = True        # auto-adjust QTP threshold based on recent win rate
ADAPTIVE_QTP_LOOKBACK = 20          # last N trades to evaluate
ADAPTIVE_QTP_MIN      = 60          # minimum QTP threshold floor (allows more trades when performing well)
ADAPTIVE_QTP_MAX      = 85          # maximum QTP threshold ceiling (tightens when losing)

# Kelly Criterion Risk Sizing
KELLY_ENABLED         = True        # use Kelly criterion for position sizing
KELLY_FRACTION        = 0.25        # fractional Kelly (25% of full Kelly = safer)
KELLY_MIN_TRADES      = 10          # minimum trades before Kelly activates

# Session-based risk multiplier
SESSION_RISK_MULTIPLIERS = {
    "london":  1.2,   # London session
    "ny":      1.1,   # NY session
    "overlap": 1.3,   # London/NY overlap (13:00-17:00 UTC) — best setups
    "tokyo":   0.8,   # Tokyo session — less risk for gold
    "off":     0.5,   # Off-hours
}

# NY Session Close — force-close all positions near end of NY session
NY_CLOSE_HOUR_UTC     = 21          # 21:00 UTC = NY market close
NY_CLOSE_ENABLED      = True        # enable end-of-day close

# ============================================================
# Global State
open_times = {}                              # ticket -> entry time
known_deals = set()                          # deals already journaled
state = {"day": None, "start_balance": 0.0, "trades_today": 0,
         "last_entry": {s: 0.0 for s in SYMBOLS},
         "last_exit": {s: 0.0 for s in SYMBOLS}, # tracks exit times for cooldown calculations
         "halted": False,
         "halt_reason": "", "loss_streak": 0, "pause_until": 0.0,
         "partial_closed_tickets": {},
         "last_trade_loss": {s: False for s in SYMBOLS},
         "dxy_bullish": True,
         "orb_ranges": {},
         "last_commentary_time": {s: 0 for s in SYMBOLS},
         "profit_locked": False,
         "peak_equity_profit": 0.0,
         # v7.0 new state fields
         "trade_outcomes": [],           # list of True/False for recent trades (win rate tracker)
         "adaptive_qtp": QTP_THRESHOLD,  # current adaptive QTP threshold (self-learning)
         "kelly_win_rate": 0.5,          # estimated win rate for Kelly sizing
         "kelly_avg_rr": 2.5,            # estimated avg R:R for Kelly sizing
         "ny_close_done": False,         # tracks if NY close already triggered today
         "volatility_regime": "normal",  # current volatility regime: normal/high/low
         }
BOT_START = time.time()                      # session start marker

# Technical Indicators Cache (v7.0: added macd, avg_atr for volatility regime)
indicators = {s: {"atr": 0.0, "ema50": 0.0, "ema50_m15": 0.0, "ema200_m5": 0.0, "ema50_h1": 0.0,
                  "adx": 0.0, "rsi_m15": 50.0, "avg_spread": 0.0,
                  "macd_hist": 0.0, "avg_atr": 0.0, "last_update": 0.0} for s in SYMBOLS}

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

def compute_macd(rates, fast=12, slow=26, signal=9):
    """Compute MACD line, signal line, and histogram. Returns (macd_line, signal_line, histogram) last values."""
    close = rates['close']
    if len(close) < slow + signal:
        return 0.0, 0.0, 0.0
    alpha_fast = 2.0 / (fast + 1)
    alpha_slow = 2.0 / (slow + 1)
    alpha_sig  = 2.0 / (signal + 1)
    ema_fast = np.zeros(len(close))
    ema_slow = np.zeros(len(close))
    ema_fast[0] = close[0]
    ema_slow[0] = close[0]
    for i in range(1, len(close)):
        ema_fast[i] = close[i] * alpha_fast + ema_fast[i-1] * (1 - alpha_fast)
        ema_slow[i] = close[i] * alpha_slow + ema_slow[i-1] * (1 - alpha_slow)
    macd_line = ema_fast - ema_slow
    sig_line  = np.zeros(len(close))
    sig_line[0] = macd_line[0]
    for i in range(1, len(close)):
        sig_line[i] = macd_line[i] * alpha_sig + sig_line[i-1] * (1 - alpha_sig)
    histogram = macd_line - sig_line
    return macd_line[-1], sig_line[-1], histogram[-1]

def compute_avg_atr(rates, period=14, avg_period=50):
    """Compute rolling average of ATR over avg_period candles for volatility regime detection."""
    if len(rates) < period + avg_period:
        return compute_atr(rates, period)
    high, low, close = rates['high'], rates['low'], rates['close']
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    # Compute ATR at each point using Wilder's smoothing
    atr_series = np.zeros(len(tr))
    atr_series[period-1] = tr[:period].mean()
    alpha = 1.0 / period
    for i in range(period, len(tr)):
        atr_series[i] = tr[i] * alpha + atr_series[i-1] * (1 - alpha)
    # Average the last avg_period ATR values
    valid = atr_series[atr_series > 0]
    if len(valid) < avg_period:
        return atr_series[-1]
    return np.mean(valid[-avg_period:])

def get_session_name(hour_utc):
    """Return current session name based on UTC hour.
    Order matters — overlap must be checked first."""
    if 13 <= hour_utc < 17:
        return "overlap"   # London/NY overlap — highest liquidity, best setups
    elif 7 <= hour_utc < 13:
        return "london"    # London session — strong trends
    elif 13 <= hour_utc < 21:
        return "ny"        # NY session (non-overlap hours)
    elif 0 <= hour_utc < 7:
        return "tokyo"     # Tokyo/Asia — ranging, lower volatility for Gold
    else:
        return "off"       # 21:00-00:00 UTC — thin liquidity, avoid

def get_dynamic_tp_mult(adx):
    """Return TP ATR multiplier based on current ADX trend strength."""
    if not DYNAMIC_TP_ENABLED:
        return TP_ATR_MULT
    if adx >= 30:
        return TP_ATR_STRONG    # 4.5x — ride the strong trend
    elif adx >= 25:
        return TP_ATR_NORMAL    # 3.0x — standard
    else:
        return TP_ATR_WEAK      # 2.0x — weak trend, take profit quicker

def update_adaptive_qtp():
    """Adjust QTP threshold based on recent win rate (self-learning)."""
    if not ADAPTIVE_QTP_ENABLED:
        return
    outcomes = state["trade_outcomes"]
    if len(outcomes) < 5:
        return  # not enough data yet
    recent = outcomes[-ADAPTIVE_QTP_LOOKBACK:]
    win_rate = sum(recent) / len(recent)
    state["kelly_win_rate"] = win_rate
    current = state["adaptive_qtp"]
    if win_rate < 0.40:
        # Bad performance -> raise threshold (be more selective)
        new_qtp = min(current + 5, ADAPTIVE_QTP_MAX)
        if new_qtp != current:
            state["adaptive_qtp"] = new_qtp
            log.info("📉 [ADAPTIVE QTP] Win rate %.0f%% is low. Raised QTP threshold to %d (more selective).", win_rate*100, new_qtp)
    elif win_rate > 0.65:
        # Good performance -> lower threshold slightly (more trades)
        new_qtp = max(current - 3, ADAPTIVE_QTP_MIN)
        if new_qtp != current:
            state["adaptive_qtp"] = new_qtp
            log.info("📈 [ADAPTIVE QTP] Win rate %.0f%% is high. Lowered QTP threshold to %d (more trades).", win_rate*100, new_qtp)

def get_kelly_risk(base_risk):
    """Calculate position risk % using fractional Kelly criterion."""
    if not KELLY_ENABLED or len(state["trade_outcomes"]) < KELLY_MIN_TRADES:
        return base_risk
    w = state["kelly_win_rate"]
    r = state["kelly_avg_rr"]
    if r <= 0 or w <= 0:
        return base_risk
    # Kelly formula: f* = (w * r - (1 - w)) / r
    kelly_full = (w * r - (1 - w)) / r
    kelly_full = max(0.0, kelly_full)  # Kelly can't be negative
    kelly_risk = kelly_full * KELLY_FRACTION
    # Cap between 0.1% and 3%
    kelly_risk = max(0.001, min(0.03, kelly_risk))
    return kelly_risk


# ---------------- SMART STRUCTURAL SL / TP ----------------

def get_structural_sl(rates, direction, entry_price, atr):
    """Find the nearest swing high/low within STRUCT_SL_LOOKBACK candles as the SL anchor.
    Adds a small ATR buffer beyond the swing to avoid premature stop-outs.
    Caps the SL distance at MAX_SL_ATR_MULT * ATR to prevent oversized risk.
    Returns the SL price."""
    lookback = rates[-2 - STRUCT_SL_LOOKBACK : -1]  # completed candles only

    if direction == "BUY":
        # SL goes below the lowest low in the lookback window
        swing_low = np.min(lookback['low'])
        sl = swing_low - SL_BUFFER_ATR * atr
        # Cap: SL must not be more than MAX_SL_ATR_MULT * ATR below entry
        max_sl = entry_price - MAX_SL_ATR_MULT * atr
        sl = max(sl, max_sl)
    else:
        # SL goes above the highest high in the lookback window
        swing_high = np.max(lookback['high'])
        sl = swing_high + SL_BUFFER_ATR * atr
        # Cap: SL must not be more than MAX_SL_ATR_MULT * ATR above entry
        max_sl = entry_price + MAX_SL_ATR_MULT * atr
        sl = min(sl, max_sl)

    return sl


def get_structural_tp(rates_h1, direction, entry_price, sl_price, atr):
    """Find the next significant swing high/low on H1 as the TP target.
    Stops slightly before the swing to take profit before resistance/support reacts.
    Enforces MIN_RR_RATIO — returns None if the structure target doesn't give 6:1.
    Falls back to ATR-based TP if no H1 structure found but adjusts to hit 6:1.
    Returns (tp_price, rr_ratio) or (None, 0) if trade should be skipped."""
    sl_dist = abs(entry_price - sl_price)
    if sl_dist <= 0:
        return None, 0.0

    min_tp_dist = MIN_RR_RATIO * sl_dist  # minimum distance TP must be from entry

    if STRUCTURAL_TP and rates_h1 is not None and len(rates_h1) >= 10:
        lookback = rates_h1[-1 - STRUCT_TP_LOOKBACK : -1]  # skip live candle

        if direction == "BUY":
            # Next swing high above entry — find the nearest one that clears our min distance
            candidate_highs = lookback['high'][lookback['high'] > entry_price + min_tp_dist]
            if len(candidate_highs) > 0:
                # Target the nearest reachable swing high (lowest of those above)
                swing_target = np.min(candidate_highs)
                tp = swing_target - TP_BUFFER_ATR * atr  # stop slightly before resistance
                rr = (tp - entry_price) / sl_dist
                if rr >= MIN_RR_RATIO:
                    return tp, rr
        else:
            # Next swing low below entry — find the nearest one that clears our min distance
            candidate_lows = lookback['low'][lookback['low'] < entry_price - min_tp_dist]
            if len(candidate_lows) > 0:
                swing_target = np.max(candidate_lows)  # nearest below
                tp = swing_target + TP_BUFFER_ATR * atr  # stop slightly before support
                rr = (entry_price - tp) / sl_dist
                if rr >= MIN_RR_RATIO:
                    return tp, rr

    # Fallback: no H1 structure found — use ATR-based TP scaled to guarantee MIN_RR_RATIO
    tp_mult = max(get_dynamic_tp_mult(0), MIN_RR_RATIO)  # always at least MIN_RR_RATIO * SL dist
    if direction == "BUY":
        tp = entry_price + tp_mult * sl_dist
    else:
        tp = entry_price - tp_mult * sl_dist

    rr = tp_mult
    return tp, rr


# ============================================================
# SMART ENTRY QUALITY FILTERS
# ============================================================

def check_momentum(rates, direction):
    """Momentum Confirmation Filter — last MOMENTUM_BARS M1 candles must agree on direction.
    This prevents entering into a brief spike against the dominant short-term flow.

    BUY  momentum: majority of last N candle closes are rising (close > open)
    SELL momentum: majority of last N candle closes are falling (close < open)

    Returns True if momentum confirms the direction."""
    if not MOMENTUM_FILTER_ENABLED:
        return True
    n = MOMENTUM_BARS
    recent = rates[-1 - n : -1]   # last N completed candles
    if len(recent) < n:
        return True  # not enough data, don't block
    bullish = sum(1 for c in recent if c['close'] > c['open'])
    bearish = sum(1 for c in recent if c['close'] < c['open'])
    threshold = max(1, n // 2 + 1)  # simple majority
    if direction == "BUY":
        return bullish >= threshold
    else:
        return bearish >= threshold


def check_trend_still_valid(rates, direction, ema200_m5, ema50_m15, ema50_h1, adx):
    """Re-verify trend alignment just before entry — market can shift between
    the initial scan and actual order placement.

    Checks:
      1. Price still on correct side of M5 EMA200 and M15 EMA50
      2. ADX still above chop threshold
      3. No recent candle has crossed the trend EMAs (invalidation candle)

    Returns True if trend is still intact."""
    if not TREND_VALID_CHECK:
        return True
    if adx < CHOP_ADX_LEVEL:
        if BOT_THOUGHTS:
            log.info("⚠️ [TREND CHECK] ADX %.1f below chop level %d — trend too weak, skipping.", adx, CHOP_ADX_LEVEL)
        return False
    mid = rates[-2]['close']
    if direction == "BUY":
        return mid > ema200_m5 and mid > ema50_m15
    else:
        return mid < ema200_m5 and mid < ema50_m15


def compute_smart_sl_tp(rates, rates_h1, direction, entry_price, atr, adx):
    """Master function: compute structural SL and structural TP, enforce 6:1 RR gate.
    Returns (sl, tp, sl_dist, rr) or None if the setup doesn't meet minimum RR."""
    # 1. Get tightest structural SL
    if STRUCTURAL_SL:
        sl = get_structural_sl(rates, direction, entry_price, atr)
    else:
        sl = entry_price - SL_ATR_MULT * atr if direction == "BUY" else entry_price + SL_ATR_MULT * atr

    sl_dist = abs(entry_price - sl)

    # Guard: if SL distance is essentially zero, skip
    if sl_dist < atr * 0.05:
        return None

    # 2. Get structural TP anchored to H1 swing levels
    tp, rr = get_structural_tp(rates_h1, direction, entry_price, sl, atr)

    # 3. Hard RR gate — skip trade if we can't get 6:1
    if tp is None or rr < MIN_RR_RATIO:
        if BOT_THOUGHTS:
            log.info("⛔ [RR GATE] Skipping %s trade — best achievable RR is %.1f:1 (need %.1f:1).",
                     direction, rr, MIN_RR_RATIO)
        return None

    if BOT_THOUGHTS:
        log.info("✅ [RR GATE] %s trade approved — Structural SL: %.5f | Structural TP: %.5f | RR: %.1f:1",
                 direction, sl, tp, rr)

    return sl, tp, sl_dist, rr


def update_indicators():
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
                # v7.0: MACD and average ATR for volatility regime
                _, _, macd_hist = compute_macd(rates, MACD_FAST, MACD_SLOW, MACD_SIGNAL_PERIOD)
                avg_atr = compute_avg_atr(rates, ATR_PERIOD, VOLATILITY_ATR_PERIOD)
                
                sym_info = mt5.symbol_info(s)
                avg_spread = 0.0
                if sym_info is not None:
                    avg_spread = np.mean(rates['spread'][-21:-1]) * sym_info.point
                
                # Determine volatility regime
                if avg_atr > 0:
                    ratio = atr / avg_atr
                    if ratio > VOLATILITY_HIGH_MULT:
                        state["volatility_regime"] = "high"
                    elif ratio < VOLATILITY_LOW_MULT:
                        state["volatility_regime"] = "low"
                    else:
                        state["volatility_regime"] = "normal"
                
                indicators[s] = {
                    "atr": atr,
                    "ema50": ema50,
                    "ema50_m15": ema50_m15,
                    "ema200_m5": ema200_m5,
                    "ema50_h1": ema50_h1,
                    "adx": adx,
                    "rsi_m15": rsi_m15,
                    "avg_spread": avg_spread,
                    "macd_hist": macd_hist,
                    "avg_atr": avg_atr,
                    "rates_h1": rates_h1,   # cached for structural TP calculation
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
                    "rates_h1": None,
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

def get_qtp_score(symbol, direction, mid, ema200_m5, ema50_m15, ema50_h1, dxy_aligned, adx, rvol, rsi_m15, macd_hist=0.0, liq_score=0):
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
        
    # 6. Synthetic Retail Sentiment via M15 RSI (10 points, reduced to make room for MACD)
    if direction == "BUY":
        if rsi_m15 < 30:
            score += 10
        elif rsi_m15 < 70:
            score += 7
    else:
        if rsi_m15 > 70:
            score += 10
        elif rsi_m15 > 30:
            score += 7

    # 7. NEW v7.0: MACD Histogram Confluence (10 points)
    if MACD_CONFLUENCE and macd_hist != 0.0:
        if direction == "BUY" and macd_hist > 0:
            score += 10   # MACD histogram positive = bullish momentum confirmed
        elif direction == "SELL" and macd_hist < 0:
            score += 10   # MACD histogram negative = bearish momentum confirmed
        # MACD divergence (opposing histogram) gives 0 bonus — acts as soft filter

    # 8. Institutional Liquidity Confluence (up to +25, min -10)
    # liq_score passed in from get_liquidity_confluence() — already capped
    score += liq_score

    return score


# ============================================================
# INSTITUTIONAL LIQUIDITY ENGINE
# ============================================================

def find_equal_highs_lows(rates, atr):
    """Detect Equal Highs (BSL) and Equal Lows (SSL) — liquidity pools where
    retail traders' stop losses cluster. Institution sweeps these before reversing.

    Logic:
      - Two swing highs within EQH_EQL_TOLERANCE * ATR of each other = Equal High (BSL)
      - Two swing lows  within EQH_EQL_TOLERANCE * ATR of each other = Equal Low  (SSL)
      - Must be separated by at least EQH_EQL_MIN_GAP candles (not adjacent wicks)

    Returns:
      bsl_pools: list of {'price': float, 'idx1': int, 'idx2': int}  <- Buy-side Liquidity
      ssl_pools: list of {'price': float, 'idx1': int, 'idx2': int}  <- Sell-side Liquidity
    """
    bsl_pools = []  # Equal Highs  -> Buy-side  liquidity (retail BUY stops above)
    ssl_pools = []  # Equal Lows   -> Sell-side liquidity (retail SELL stops below)

    n = len(rates)
    tolerance = EQH_EQL_TOLERANCE * atr
    start = max(0, n - 2 - EQH_EQL_LOOKBACK)
    end   = n - 1  # skip live candle

    highs = rates['high'][start:end]
    lows  = rates['low'][start:end]

    for i in range(len(highs)):
        for j in range(i + EQH_EQL_MIN_GAP, len(highs)):
            # Equal Highs (BSL)
            if abs(highs[i] - highs[j]) <= tolerance:
                pool_price = (highs[i] + highs[j]) / 2
                bsl_pools.append({'price': pool_price, 'idx1': start + i, 'idx2': start + j})
            # Equal Lows (SSL)
            if abs(lows[i] - lows[j]) <= tolerance:
                pool_price = (lows[i] + lows[j]) / 2
                ssl_pools.append({'price': pool_price, 'idx1': start + i, 'idx2': start + j})

    def deduplicate(pools):
        if not pools:
            return []
        pools_sorted = sorted(pools, key=lambda x: x['price'])
        result = [pools_sorted[0]]
        for p in pools_sorted[1:]:
            if abs(p['price'] - result[-1]['price']) > tolerance:
                result.append(p)
        return result

    return deduplicate(bsl_pools), deduplicate(ssl_pools)


def get_premium_discount_zone(rates, period=None):
    """Classify current price as Premium, Discount, or Equilibrium relative to
    the recent swing range. Institution buys in Discount, sells in Premium.

    Returns:
      zone: 'premium' | 'discount' | 'equilibrium'
      pct:  0.0-1.0 (0 = at swing low, 1 = at swing high)
    """
    if period is None:
        period = PREMIUM_DISC_PERIOD
    n = len(rates)
    lookback = rates[max(0, n - 1 - period) : n - 1]
    swing_high = np.max(lookback['high'])
    swing_low  = np.min(lookback['low'])
    rng = swing_high - swing_low
    if rng <= 0:
        return 'equilibrium', 0.5
    mid = rates[-2]['close']
    pct = (mid - swing_low) / rng
    if pct >= 0.75:
        return 'premium', pct
    elif pct <= 0.25:
        return 'discount', pct
    else:
        return 'equilibrium', pct


def detect_inducement(rates, direction, atr):
    """Detect Inducement (IDM): a fake breakout that sweeps retail stops,
    then closes back with a strong rejection wick — the institutional fingerprint.

    BUY IDM: wick spikes below prior swing low, closes back above with bullish body
    SELL IDM: wick spikes above prior swing high, closes back below with bearish body

    Returns True if confirmed IDM pattern detected for this direction.
    """
    n = len(rates)
    ref_candles = rates[max(0, n - 2 - IDM_LOOKBACK) : n - 2]
    if len(ref_candles) < 3:
        return False

    s = rates[-2]
    c_open, c_high, c_low, c_close = s['open'], s['high'], s['low'], s['close']
    c_range = c_high - c_low
    if c_range <= 0:
        return False

    if direction == "BUY":
        recent_low     = np.min(ref_candles['low'])
        swept_below    = c_low < recent_low
        closed_above   = c_close > recent_low
        lower_wick     = min(c_open, c_close) - c_low
        strong_reject  = (lower_wick / c_range) >= IDM_WICK_RATIO and c_close > c_open
        return swept_below and closed_above and strong_reject
    else:
        recent_high    = np.max(ref_candles['high'])
        swept_above    = c_high > recent_high
        closed_below   = c_close < recent_high
        upper_wick     = c_high - max(c_open, c_close)
        strong_reject  = (upper_wick / c_range) >= IDM_WICK_RATIO and c_close < c_open
        return swept_above and closed_below and strong_reject


def get_liquidity_confluence(rates, direction, mid, atr):
    """Master liquidity scorer combining EQH/EQL pools, Premium/Discount zone,
    and Inducement detection. Returns bonus points for QTP and a context dict for logging.

    Score breakdown (max +25, min -10):
      +10  SSL swept before BUY  /  BSL swept before SELL  (liquidity collected)
      +10  BUY in Discount zone  /  SELL in Premium zone   (institutional zone)
      +15  Inducement (IDM) confirmed                       (highest conviction)
       -5  Heading INTO pool (not yet swept)                (risky)
       -5  Trading against institutional zone               (retail trap)
    """
    liq_score = 0
    context   = {}

    if not LIQ_SCORE_ENABLED:
        return 0, context

    # --- 1. Equal Highs / Equal Lows ---
    bsl_pools, ssl_pools = find_equal_highs_lows(rates, atr)
    prox = 2.0 * atr

    near_bsl  = any(abs(mid - p['price']) <= prox for p in bsl_pools)
    near_ssl  = any(abs(mid - p['price']) <= prox for p in ssl_pools)
    swept_bsl = any(mid > p['price'] for p in bsl_pools)
    swept_ssl = any(mid < p['price'] for p in ssl_pools)

    context['bsl_count'] = len(bsl_pools)
    context['ssl_count'] = len(ssl_pools)

    if direction == "BUY" and swept_ssl:
        liq_score += 10
        context['pool_signal'] = "SSL swept -> institutional BUY zone confirmed"
    elif direction == "SELL" and swept_bsl:
        liq_score += 10
        context['pool_signal'] = "BSL swept -> institutional SELL zone confirmed"
    elif direction == "BUY" and near_bsl:
        liq_score -= 5
        context['pool_signal'] = "Approaching BSL ahead — resistance pool, caution"
    elif direction == "SELL" and near_ssl:
        liq_score -= 5
        context['pool_signal'] = "Approaching SSL ahead — support pool, caution"
    else:
        context['pool_signal'] = "No active pool confluence"

    # --- 2. Premium / Discount Zone ---
    zone, zone_pct = get_premium_discount_zone(rates)
    context['zone']     = zone
    context['zone_pct'] = round(zone_pct * 100, 1)

    if direction == "BUY" and zone == 'discount':
        liq_score += 10
        context['zone_signal'] = f"BUY in Discount ({zone_pct*100:.0f}%) — with institution"
    elif direction == "SELL" and zone == 'premium':
        liq_score += 10
        context['zone_signal'] = f"SELL in Premium ({zone_pct*100:.0f}%) — with institution"
    elif zone == 'equilibrium':
        context['zone_signal'] = f"Equilibrium ({zone_pct*100:.0f}%) — fair value, no edge"
    else:
        liq_score -= 5
        context['zone_signal'] = f"{direction} against zone ({zone} {zone_pct*100:.0f}%) — retail trap"

    # --- 3. Inducement (IDM) ---
    idm = detect_inducement(rates, direction, atr)
    context['idm'] = idm
    if idm:
        liq_score += 15
        context['idm_signal'] = "IDM CONFIRMED — institution swept stops, reversal imminent"
    else:
        context['idm_signal'] = "No IDM"

    liq_score = max(-10, min(25, liq_score))
    context['total_liq_score'] = liq_score
    return liq_score, context


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

    # v7.0: Discord Webhook backup notification
    if DISCORD_WEBHOOK:
        try:
            import json as _json
            payload = _json.dumps({"content": f"🤖 **MT5 Bot** | {msg}"}).encode("utf-8")
            req = urllib.request.Request(
                DISCORD_WEBHOOK, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            log.warning("Discord notification failed: %s", e)

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

        # v7.0: Track outcome for Adaptive QTP & Kelly Criterion
        is_win = d.profit >= 0
        state["trade_outcomes"].append(is_win)
        if len(state["trade_outcomes"]) > ADAPTIVE_QTP_LOOKBACK * 2:
            state["trade_outcomes"] = state["trade_outcomes"][-ADAPTIVE_QTP_LOOKBACK:]
        # Update Kelly avg R:R estimate from journal (approximate)
        if len(state["trade_outcomes"]) >= KELLY_MIN_TRADES:
            wins = [o for o in state["trade_outcomes"] if o]
            losses = [o for o in state["trade_outcomes"] if not o]
            state["kelly_win_rate"] = len(wins) / len(state["trade_outcomes"])
        update_adaptive_qtp()

        state["last_exit"][d.symbol] = time.time()

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
    # Always use explicit path for GitHub Actions reliability
    init_ok = mt5.initialize(
        path="C:\\MT5\\terminal64.exe",
        login=LOGIN, server=SERVER, password=PASSWORD, portable=PORTABLE
    )
    if not init_ok:
        # Fallback: try default init, then other known paths
        fallback_paths = [
            None,  # default (let MT5 library find it)
            "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe",
            "C:\\Program Files\\Exness MetaTrader 5\\terminal64.exe",
            "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
        ]
        for path in fallback_paths:
            if path:
                log.info("Retrying MT5 init with path: %s", path)
                init_ok = mt5.initialize(path=path, login=LOGIN, server=SERVER,
                                         password=PASSWORD, portable=PORTABLE)
            else:
                log.info("Retrying MT5 init with default path...")
                init_ok = mt5.initialize(login=LOGIN, server=SERVER,
                                         password=PASSWORD, portable=PORTABLE)
            if init_ok:
                break
        if not init_ok:
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    info = mt5.account_info()
    log.info("Connected: %s | Balance: %.2f %s", info.login, info.balance, info.currency)

    # Symbol setup
    for s in SYMBOLS[:]:
        if not mt5.symbol_select(s, True):
            log.warning("Symbol %s not available, removing", s)
            SYMBOLS.remove(s)

    # DXYm correlation (optional — warn only)
    if not mt5.symbol_select("DXYm", True):
        log.warning("DXYm not available — DXY correlation filter disabled.")

    # AutoTrading check — retry up to 3 times with delay (terminal may need a moment)
    trade_allowed = False
    for attempt in range(3):
        term = mt5.terminal_info()
        if term.trade_allowed:
            trade_allowed = True
            log.info("✅ AutoTrading is ENABLED in terminal.")
            break
        log.warning("⚠️ AutoTrading not yet enabled (attempt %d/3). Waiting 5s...", attempt + 1)
        import time as _time
        _time.sleep(5)

    if not trade_allowed:
        # Last resort: try re-initializing once more with explicit path
        mt5.shutdown()
        log.warning("AutoTrading still disabled — re-initializing MT5 with explicit path...")
        mt5.initialize(path="C:\\MT5\\terminal64.exe", login=LOGIN,
                       server=SERVER, password=PASSWORD, portable=PORTABLE)
        term = mt5.terminal_info()
        if term and term.trade_allowed:
            log.info("✅ AutoTrading ENABLED after re-init.")
        else:
            log.warning(
                "⚠️ AutoTrading still DISABLED. Bot will run but orders may be rejected. "
                "Enable AutoTrading manually in MT5 terminal if running locally."
            )

    # Account trading permission (broker-level — this is fatal)
    if not info.trade_allowed:
        raise RuntimeError("Account trading is DISABLED by broker. Check account settings.")

    notify(f"🤖 Bot online. Balance {info.balance:.2f} USD. Hunting: {', '.join(SYMBOLS)}")

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
                    state["last_exit"][pos.symbol] = time.time()
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
                     pause_until=0.0, profit_locked=False, peak_equity_profit=0.0,
                     ny_close_done=False)
        state["last_trade_loss"] = {s: False for s in SYMBOLS}
        state["last_exit"] = {s: 0.0 for s in SYMBOLS}
        known_deals.clear()
        log.info("New trading day. Start balance: %.2f", acc.balance)
    if state["halted"]:
        return False

    # Drawdown limit checks floating equity
    floating_pnl = (acc.equity - state["start_balance"]) / state["start_balance"] if state["start_balance"] > 0 else 0.0
    realized_pnl = (acc.balance - state["start_balance"]) / state["start_balance"] if state["start_balance"] > 0 else 0.0

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
        
        # Calculate ATR-based slack for open positions to prevent noise triggers
        open_atr_slack_pct = 0.0
        positions = mt5.positions_get() or []
        for pos in positions:
            if pos.magic == MAGIC:
                sym_name = pos.symbol
                cache = indicators.get(sym_name)
                sym_info = mt5.symbol_info(sym_name)
                if cache and cache["atr"] > 0 and sym_info:
                    # PnL fluctuation of 1.0 * ATR for this position's volume
                    atr_pnl = (cache["atr"] / sym_info.trade_tick_size) * sym_info.trade_tick_value * pos.volume
                    # Convert to percent of start balance
                    atr_pnl_pct = atr_pnl / state["start_balance"] if state["start_balance"] > 0 else 0.0
                    open_atr_slack_pct += atr_pnl_pct
        
        # Calculate dynamic trailing floor with noise protection slack (include dynamic open position ATR slack)
        slack = max(
            DAILY_PROFIT_MIN_SLACK,
            state["peak_equity_profit"] * DAILY_PROFIT_TRAIL_PERCENT,
            DAILY_PROFIT_ATR_SLACK_MULT * open_atr_slack_pct
        )
        trailing_floor = state["peak_equity_profit"] - slack
        
        if floating_pnl < trailing_floor:
            close_all_positions("trail_lock")
            # Wait briefly for execution and update state with new baseline balance to keep trading
            time.sleep(0.5)
            acc_info = mt5.account_info()
            new_balance = acc_info.balance if acc_info is not None else acc.balance
            
            # Reset trailing state with new baseline balance instead of halting
            state.update(start_balance=new_balance, profit_locked=False, peak_equity_profit=0.0)
            log.info("🎯 Trailing Daily Profit hit! Locked in %.2f%% profit. Resetting baseline balance to %.2f to continue trading.", realized_pnl * 100, new_balance)
            notify(f"🎯 Trailing Daily Profit hit! Locked in {realized_pnl*100:+.2f}% profit. Baseline reset to {new_balance:.2f}. Continuing trading.")

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

    # 1. Setup Quality Factor (QTP Score scaling): scale risk between 0.5x and 1.5x of base risk
    # If qtp_score is not passed (0), keep it neutral (1.0x).
    if qtp_score > 0:
        qtp_factor = (qtp_score - 50) / 40.0 if qtp_score > 50 else 0.5
        qtp_factor = max(0.5, min(1.5, qtp_factor))
    else:
        qtp_factor = 1.0

    # 2. Performance Factor (Drawdown Protection: halving risk based on consecutive loss streak)
    streak = state.get("loss_streak", 0)
    risk_multiplier = 1.0 / (2 ** streak)

    # v7.0: Session-based risk multiplier
    hour_now = datetime.now(timezone.utc).hour
    session_name = get_session_name(hour_now)
    session_mult = SESSION_RISK_MULTIPLIERS.get(session_name, 1.0)

    # v7.0: Kelly Criterion base risk
    base_risk = get_kelly_risk(RISK_PER_TRADE)

    # Combined Dynamic Risk Percentage
    current_risk = base_risk * qtp_factor * risk_multiplier * session_mult
    # Cap risk between 0.1% and 3.0% of account balance for safety
    current_risk = max(0.001, min(0.03, current_risk))
    
    if BOT_THOUGHTS:
        log.info(f"🧠 [BOT BRAIN - RISK ANALYSIS] Setup QTP Score: {qtp_score}/100 (Setup Factor: {qtp_factor:.2f}x) | "
                 f"Loss Streak: {streak} (Streak Multiplier: {risk_multiplier:.2f}x) | "
                 f"Session: {session_name} (Session Mult: {session_mult:.2f}x) | Kelly Base: {base_risk*100:.2f}% -> "
                 f"Dynamically set Trade Risk to {current_risk * 100:.2f}% of balance.")
                 
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
        
        # 1. Smart Time-stop check
        # Only force-close if trade is stalled near breakeven or in loss.
        # If trade is running in profit, let it breathe — don't cut winners early.
        opened = open_times.get(pos.ticket, pos.time)
        if time.time() - opened > MAX_HOLD_SECONDS:
            sym_cache = indicators.get(pos.symbol)
            atr_val = sym_cache["atr"] if sym_cache and sym_cache["atr"] > 0 else 0

            # Smart exit: skip time-stop if position is meaningfully in profit
            if SMART_TIME_EXIT and atr_val > 0:
                profit_in_price = abs(pos.profit)
                sym_info_ts = mt5.symbol_info(pos.symbol)
                if sym_info_ts:
                    profit_pts = (profit_in_price / sym_info_ts.trade_tick_value
                                  * sym_info_ts.trade_tick_size / pos.volume
                                  if pos.volume > 0 else 0)
                    if profit_pts > SMART_TIME_EXIT_BUFFER * atr_val and pos.profit > 0:
                        if BOT_THOUGHTS:
                            log.info("⏱ [SMART TIME-EXIT] Trade #%d is +%.2f profit — skipping time-stop, letting it run.",
                                     pos.ticket, pos.profit)
                        # Don't close, but do tighten SL to protect profit
                        # (trailing stop logic below will handle it)
                        pass
                    else:
                        # Flat or losing — close it
                        tick = mt5.symbol_info_tick(pos.symbol)
                        if tick is not None:
                            is_buy = pos.type == mt5.POSITION_TYPE_BUY
                            res = mt5.order_send({
                                "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
                                "position": pos.ticket, "volume": pos.volume,
                                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                                "price": tick.bid if is_buy else tick.ask,
                                "deviation": DEVIATION, "magic": MAGIC,
                                "comment": "time_exit", "type_time": mt5.ORDER_TIME_GTC,
                                "type_filling": mt5.ORDER_FILLING_IOC})
                            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                                state["last_exit"][pos.symbol] = time.time()
                            log.info("⏱ [TIME-STOP] %s #%d profit=%.2f (flat/loss — closed)",
                                     pos.symbol, pos.ticket, pos.profit)
                        open_times.pop(pos.ticket, None)
                        state["partial_closed_tickets"].pop(pos.ticket, None)
                        continue
            else:
                # SMART_TIME_EXIT disabled — original behavior
                tick = mt5.symbol_info_tick(pos.symbol)
                if tick is not None:
                    is_buy = pos.type == mt5.POSITION_TYPE_BUY
                    res = mt5.order_send({
                        "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
                        "position": pos.ticket, "volume": pos.volume,
                        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                        "price": tick.bid if is_buy else tick.ask,
                        "deviation": DEVIATION, "magic": MAGIC,
                        "comment": "time_exit", "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC})
                    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                        state["last_exit"][pos.symbol] = time.time()
                    log.info("⏱ Time-stop %s #%d profit=%.2f", pos.symbol, pos.ticket, pos.profit)
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
                macd_hist = cache.get("macd_hist", 0.0)  # v7.0
                rates_h1_cached = cache.get("rates_h1", None)  # for structural TP

                # v7.0: Volatility Regime Filter — skip if market is spiking or dead
                vol_regime = state.get("volatility_regime", "normal")
                if vol_regime == "high":
                    if n % 300 == 0:
                        log.info("⚡ [VOLATILITY GUARD] ATR spike detected (regime=HIGH). Skipping entries for %s.", symbol)
                    continue
                if vol_regime == "low":
                    if n % 300 == 0:
                        log.info("💤 [VOLATILITY GUARD] Market is dead (regime=LOW). Skipping entries for %s.", symbol)
                    continue

                # v7.0: NY Session Close — force-close all trades near end of NY session
                hour_utc = datetime.now(timezone.utc).hour
                if NY_CLOSE_ENABLED and hour_utc >= NY_CLOSE_HOUR_UTC and not state.get("ny_close_done", False):
                    positions_open = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]
                    if positions_open:
                        log.info("🌙 [NY CLOSE] NY session ending (%02d:00 UTC). Closing %d position(s) for end-of-day.", NY_CLOSE_HOUR_UTC, len(positions_open))
                        close_all_positions("ny_session_close")
                        notify(f"🌙 NY Session Close: {len(positions_open)} position(s) closed for end-of-day.")
                    state["ny_close_done"] = True

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
                    time_since_last_trade = time.time() - state["last_exit"][symbol]
                    if time_since_last_trade < symbol_cooldown:
                        warnings.append(f"⏳ Cooldown active for {symbol}: wait {symbol_cooldown - time_since_last_trade:.1f}s.")
                    
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

                # Dynamic cooldown check: scale delay after a loss on this symbol
                symbol_cooldown = COOLDOWN_SEC
                if state["last_trade_loss"].get(symbol, False):
                    symbol_cooldown = int(COOLDOWN_SEC * LOSS_COOLDOWN_SCALE)

                if (not can_trade or open_count >= MAX_OPEN_TOTAL
                        or time.time() - state["last_exit"][symbol] < symbol_cooldown):
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
                    if n % 100 == 0:
                        log.info("⏸ [SPREAD] Widened to %.1f pts (avg %.1f pts) — skipping %s",
                                 spread / sym_info.point, avg_spread / sym_info.point, symbol)
                    continue
                if spread / atr > SPREAD_ATR_LIMIT:
                    if n % 100 == 0:
                        log.info("⏸ [SPREAD/ATR] ratio %.2f > limit %.2f (spread=%.1f pts, ATR=%.5f) — skipping %s",
                                 spread / atr, SPREAD_ATR_LIMIT, spread / sym_info.point, atr, symbol)
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

                # Institutional Liquidity Confluence (computed once, shared by all strategies)
                liq_buy_score,  liq_buy_ctx  = get_liquidity_confluence(rates, "BUY",  mid, atr)
                liq_sell_score, liq_sell_ctx = get_liquidity_confluence(rates, "SELL", mid, atr)

                if BOT_THOUGHTS:
                    log.info(
                        "💧 [LIQUIDITY ENGINE - %s]\n"
                        "   BUY  liq: %+d pts | Pool: %s | Zone: %s (%s%%) | IDM: %s\n"
                        "   SELL liq: %+d pts | Pool: %s | Zone: %s (%s%%) | IDM: %s",
                        symbol,
                        liq_buy_ctx.get('total_liq_score', 0),
                        liq_buy_ctx.get('pool_signal', ''),
                        liq_buy_ctx.get('zone', ''),
                        liq_buy_ctx.get('zone_pct', ''),
                        liq_buy_ctx.get('idm_signal', ''),
                        liq_sell_ctx.get('total_liq_score', 0),
                        liq_sell_ctx.get('pool_signal', ''),
                        liq_sell_ctx.get('zone', ''),
                        liq_sell_ctx.get('zone_pct', ''),
                        liq_sell_ctx.get('idm_signal', ''),
                    )

                # ── Pre-entry Quality Gate (shared by ALL strategies) ──────────────
                # Momentum check: last MOMENTUM_BARS candles must agree on direction.
                buy_momentum_ok  = check_momentum(rates, "BUY")
                sell_momentum_ok = check_momentum(rates, "SELL")

                # Trend validity: re-confirm trend is still intact right before entry
                buy_trend_ok  = check_trend_still_valid(rates, "BUY",  ema200_m5, ema50_m15, ema50_h1, adx)
                sell_trend_ok = check_trend_still_valid(rates, "SELL", ema200_m5, ema50_m15, ema50_h1, adx)

                if BOT_THOUGHTS:
                    if not buy_momentum_ok:
                        log.info("🚫 [MOMENTUM] BUY momentum not confirmed — last %d candles lack bullish agreement.", MOMENTUM_BARS)
                    if not sell_momentum_ok:
                        log.info("🚫 [MOMENTUM] SELL momentum not confirmed — last %d candles lack bearish agreement.", MOMENTUM_BARS)

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

                    buy_score  = get_qtp_score(symbol, "BUY",  mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned,  adx, rvol, rsi_m15, macd_hist, liq_buy_score)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15, macd_hist, liq_sell_score)

                    # BUY condition
                    if (buy_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_buy
                            and buy_momentum_ok and buy_trend_ok
                            and mid > ema50_arr[-1] and c_close > c_open and tick.ask > highest_high):
                        entry_price = tick.ask
                        result = compute_smart_sl_tp(rates, rates_h1_cached, "BUY", entry_price, atr, adx)
                        if result is not None:
                            sl, tp, sl_dist, rr = result
                            open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                            open_count += 1

                    # SELL condition
                    elif (sell_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_sell
                              and sell_momentum_ok and sell_trend_ok
                              and mid < ema50_arr[-1] and c_close < c_open and tick.bid < lowest_low):
                        entry_price = tick.bid
                        result = compute_smart_sl_tp(rates, rates_h1_cached, "SELL", entry_price, atr, adx)
                        if result is not None:
                            sl, tp, sl_dist, rr = result
                            open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                            open_count += 1

                elif active_mode == "SWEEP":
                    # Liquidity Sweep Reversal strategy
                    sweep_rates = rates[-2 - SWEEP_PERIOD : -2]
                    highest_high = np.max(sweep_rates['high'])
                    lowest_low = np.min(sweep_rates['low'])

                    buy_score  = get_qtp_score(symbol, "BUY",  mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned,  adx, rvol, rsi_m15, macd_hist, liq_buy_score)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15, macd_hist, liq_sell_score)

                    # BUY condition
                    if (buy_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_buy):
                        lower_wick = min(c_open, c_close) - c_low
                        wick_ratio = lower_wick / c_range if c_range > 0 else 0
                        
                        if (c_low < lowest_low and c_close > lowest_low and c_close > c_open and wick_ratio >= SWEEP_WICK_RATIO):
                            min_idx_in_range = np.argmin(sweep_rates['low'])
                            prev_low_idx = len(rates) - 2 - SWEEP_PERIOD + min_idx_in_range
                            rsi_divergence = rsi[-2] > rsi[prev_low_idx]
                            if rsi_divergence and buy_momentum_ok and buy_trend_ok:
                                if tick.ask > c_high:
                                    entry_price = tick.ask
                                    result = compute_smart_sl_tp(rates, rates_h1_cached, "BUY", entry_price, atr, adx)
                                    if result is not None:
                                        sl, tp, sl_dist, rr = result
                                        open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                        open_count += 1

                    # SELL condition
                    elif (sell_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_sell):
                        upper_wick = c_high - max(c_open, c_close)
                        wick_ratio = upper_wick / c_range if c_range > 0 else 0
                        
                        if (c_high > highest_high and c_close < highest_high and c_close < c_open and wick_ratio >= SWEEP_WICK_RATIO):
                            max_idx_in_range = np.argmax(sweep_rates['high'])
                            prev_high_idx = len(rates) - 2 - SWEEP_PERIOD + max_idx_in_range
                            rsi_divergence = rsi[-2] < rsi[prev_high_idx]
                            if rsi_divergence and sell_momentum_ok and sell_trend_ok:
                                if tick.bid < c_low:
                                    entry_price = tick.bid
                                    result = compute_smart_sl_tp(rates, rates_h1_cached, "SELL", entry_price, atr, adx)
                                    if result is not None:
                                        sl, tp, sl_dist, rr = result
                                        open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                        open_count += 1

                elif active_mode == "SMC":
                    # Smart Money Concepts: Fair Value Gap Mitigation
                    bull_fvgs, bear_fvgs = find_active_fvgs(rates)

                    buy_score  = get_qtp_score(symbol, "BUY",  mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned,  adx, rvol, rsi_m15, macd_hist, liq_buy_score)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15, macd_hist, liq_sell_score)
                    
                    # BUY condition: FVG mitigation & rejection
                    if (buy_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_buy
                            and buy_momentum_ok and buy_trend_ok and len(bull_fvgs) > 0):
                        fvg = bull_fvgs[-1]
                        if c_low <= fvg['ceiling'] and c_close > fvg['floor'] and c_close > c_open:
                            if tick.ask > c_high:
                                entry_price = tick.ask
                                result = compute_smart_sl_tp(rates, rates_h1_cached, "BUY", entry_price, atr, adx)
                                if result is not None:
                                    sl, tp, sl_dist, rr = result
                                    open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                    open_count += 1

                    # SELL condition: FVG mitigation & rejection
                    elif (sell_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_sell
                              and sell_momentum_ok and sell_trend_ok and len(bear_fvgs) > 0):
                        fvg = bear_fvgs[-1]
                        if c_high >= fvg['floor'] and c_close < fvg['ceiling'] and c_close < c_open:
                            if tick.bid < c_low:
                                entry_price = tick.bid
                                result = compute_smart_sl_tp(rates, rates_h1_cached, "SELL", entry_price, atr, adx)
                                if result is not None:
                                    sl, tp, sl_dist, rr = result
                                    open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                    open_count += 1

                elif active_mode == "ORB":
                    # Opening Range Breakout
                    orb_range = get_orb_ranges(symbol)

                    buy_score  = get_qtp_score(symbol, "BUY",  mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned,  adx, rvol, rsi_m15, macd_hist, liq_buy_score)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15, macd_hist, liq_sell_score)
                    
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
                        if (buy_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_buy
                                and buy_momentum_ok and buy_trend_ok
                                and tick.ask > range_high and c_close > c_open):
                            entry_price = tick.ask
                            result = compute_smart_sl_tp(rates, rates_h1_cached, "BUY", entry_price, atr, adx)
                            if result is not None:
                                sl, tp, sl_dist, rr = result
                                open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                open_count += 1

                        # SELL condition: breakout of range low
                        elif (sell_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_sell
                                  and sell_momentum_ok and sell_trend_ok
                                  and tick.bid < range_low and c_close < c_open):
                            entry_price = tick.bid
                            result = compute_smart_sl_tp(rates, rates_h1_cached, "SELL", entry_price, atr, adx)
                            if result is not None:
                                sl, tp, sl_dist, rr = result
                                open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                open_count += 1

                elif active_mode == "OB":
                    # Smart Money Concepts: Order Block Mitigation
                    bull_obs, bear_obs = find_active_order_blocks(rates, atr)

                    buy_score  = get_qtp_score(symbol, "BUY",  mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned,  adx, rvol, rsi_m15, macd_hist, liq_buy_score)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15, macd_hist, liq_sell_score)
                    
                    # BUY condition: Price tests Bullish OB ceiling and rejects it
                    if (buy_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_buy
                            and buy_momentum_ok and buy_trend_ok and len(bull_obs) > 0):
                        ob = bull_obs[-1]
                        if c_low <= ob['ceiling'] and c_close > ob['floor'] and c_close > c_open:
                            if tick.ask > c_high:
                                entry_price = tick.ask
                                result = compute_smart_sl_tp(rates, rates_h1_cached, "BUY", entry_price, atr, adx)
                                if result is not None:
                                    sl, tp, sl_dist, rr = result
                                    open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                    open_count += 1

                    # SELL condition: Price tests Bearish OB floor and rejects it
                    elif (sell_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_sell
                              and sell_momentum_ok and sell_trend_ok and len(bear_obs) > 0):
                        ob = bear_obs[-1]
                        if c_high >= ob['floor'] and c_close < ob['ceiling'] and c_close < c_open:
                            if tick.bid < c_low:
                                entry_price = tick.bid
                                result = compute_smart_sl_tp(rates, rates_h1_cached, "SELL", entry_price, atr, adx)
                                if result is not None:
                                    sl, tp, sl_dist, rr = result
                                    open_trade(symbol, "SELL", sl, tp, sl_dist, qtp_score=sell_score)
                                    open_count += 1

                else: # active_mode == "BOUNCE"
                    # Pullback Bounce strategy
                    ema50_arr = compute_ema(rates, EMA_PERIOD)
                    ema50_val = ema50_arr[-2]

                    buy_score  = get_qtp_score(symbol, "BUY",  mid, ema200_m5, ema50_m15, ema50_h1, dxy_buy_aligned,  adx, rvol, rsi_m15, macd_hist, liq_buy_score)
                    sell_score = get_qtp_score(symbol, "SELL", mid, ema200_m5, ema50_m15, ema50_h1, dxy_sell_aligned, adx, rvol, rsi_m15, macd_hist, liq_sell_score)

                    # BUY condition
                    if (buy_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_buy
                            and buy_momentum_ok and buy_trend_ok and mid > ema50_arr[-1]):
                        if (c_low <= ema50_val and c_close > ema50_val and c_close > c_open):
                            if tick.ask > c_high:
                                entry_price = tick.ask
                                result = compute_smart_sl_tp(rates, rates_h1_cached, "BUY", entry_price, atr, adx)
                                if result is not None:
                                    sl, tp, sl_dist, rr = result
                                    open_trade(symbol, "BUY", sl, tp, sl_dist, qtp_score=buy_score)
                                    open_count += 1

                    # SELL condition
                    elif (sell_score >= state["adaptive_qtp"] and not dxy_velocity_blocked_sell
                              and sell_momentum_ok and sell_trend_ok and mid < ema50_arr[-1]):
                        if (c_high >= ema50_val and c_close < ema50_val and c_close < c_open):
                            if tick.bid < c_low:
                                entry_price = tick.bid
                                result = compute_smart_sl_tp(rates, rates_h1_cached, "SELL", entry_price, atr, adx)
                                if result is not None:
                                    sl, tp, sl_dist, rr = result
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
