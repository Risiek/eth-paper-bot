"""Central config."""

# Market
PAIR = "ETHUSDT"
INTERVAL_MIN = 5
CANDLE_LIMIT = 250  # enough for EMA200 warmup

# FX
PLN_PER_EUR = 4.25
START_PLN = 500.0
START_EUR = START_PLN / PLN_PER_EUR

# Trading
LEVERAGE = 3.0
RISK_PCT = 0.02       # 2% equity at risk per trade
SL_PCT = 0.02         # 2% equity stop-loss target (price move = SL_PCT / LEVERAGE)
TP_PCT = 0.04         # 4% equity take-profit target
FEE_PCT = 0.001       # 0.10% round-trip fee (on notional)
MAX_POSITIONS = 1

# Indicators
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
EMA_TREND = 200
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Risk breakers
DAILY_DD_STOP = 0.05
TOTAL_DD_STOP = 0.15

# Loop
CYCLE_SECONDS = 5 * 60  # 5m

# Files
STATE_FILE = "state.json"
LOG_FILE = "trading_log.txt"
CHART_FILE = "chart.png"
EQUITY_FILE = "equity.png"
