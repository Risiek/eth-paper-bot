"""Main bot loop. Runs every CYCLE_SECONDS. Logs + colored terminal + charts."""
import logging
import time
from datetime import datetime, timezone

from colorama import Fore, Style, init as colorama_init

from config import (
    START_EUR, PLN_PER_EUR, CYCLE_SECONDS, LOG_FILE, EMA_TREND,
)
from data_fetcher import fetch_ohlc
from indicators import enrich
from strategy import decide
import paper_trader as pt
from visualize import plot_chart, plot_equity

colorama_init(autoreset=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bot")


def c(color, text):
    return f"{color}{text}{Style.RESET_ALL}"


def fmt_eur_pln(eur):
    return f"{eur:.2f} EUR ({eur * PLN_PER_EUR:.2f} PLN)"


def print_report(df, sig, state, breaker_msg, closed_trade):
    last = df.iloc[-1]
    print("\n" + "=" * 70)
    print(c(Fore.CYAN, f"ETH/USDT report  |  {datetime.now(timezone.utc).isoformat(timespec='seconds')}"))
    print("=" * 70)
    print(f"Price:    {c(Fore.YELLOW, f'{sig.price:.2f} USDT')}   (candle close {last['close']:.2f})")
    rsi_color = Fore.RED if sig.rsi > 70 else (Fore.GREEN if sig.rsi < 30 else Fore.WHITE)
    print(f"RSI(14):  {c(rsi_color, f'{sig.rsi:.2f}')}")
    print(f"BB up:    {sig.bb_up:.2f}   mid: {float(last['bb_mid']):.2f}   low: {sig.bb_low:.2f}")
    print(f"EMA{EMA_TREND}:   {sig.ema_trend:.2f}  ({'uptrend' if sig.price > sig.ema_trend else 'downtrend'})")
    print(f"Vol:      {float(last['volume']):.4f}  rising={bool(last['vol_rising'])}")

    color = Fore.RED if sig.action == "SHORT" else (Fore.GREEN if sig.action == "LONG" else Fore.WHITE)
    print(f"Decision: {c(color, sig.action)} - {sig.reason}")

    if closed_trade:
        tcol = Fore.GREEN if closed_trade.pnl_eur >= 0 else Fore.RED
        print(c(tcol, f"CLOSED {closed_trade.side} @ {closed_trade.exit:.2f} ({closed_trade.reason}) "
                     f"net={closed_trade.pnl_eur:+.3f} EUR fees={closed_trade.fees_eur:.3f} ({closed_trade.pnl_pct:+.2f}%)"))

    if state.position:
        p = state.position
        print(c(Fore.MAGENTA, f"OPEN {p.side} entry={p.entry:.2f}  SL={p.sl:.2f}  TP={p.tp:.2f}  "
                              f"margin={p.size_eur:.2f} notional={p.notional_eur:.2f} EUR"))
    else:
        print("Position: flat")

    print("-" * 70)
    print(f"Start:    {fmt_eur_pln(START_EUR)}")
    print(f"Equity:   {fmt_eur_pln(state.equity_eur)}")
    total_pnl_eur = state.equity_eur - START_EUR
    total_pnl_pct = (state.equity_eur / START_EUR - 1) * 100
    pcol = Fore.GREEN if total_pnl_eur >= 0 else Fore.RED
    print(c(pcol, f"P&L:      {total_pnl_eur:+.2f} EUR ({total_pnl_eur * PLN_PER_EUR:+.2f} PLN) | {total_pnl_pct:+.2f}%"))

    if breaker_msg:
        print(c(Fore.RED + Style.BRIGHT, f"CIRCUIT BREAKER: {breaker_msg}"))
    if state.halted:
        print(c(Fore.RED + Style.BRIGHT, f"HALTED: {state.halt_reason}"))

    print("-" * 70)
    print("Last 10 trades:")
    if not state.trades:
        print("  (none yet)")
    for t in state.trades[-10:]:
        col = Fore.GREEN if t.pnl_eur >= 0 else Fore.RED
        print(c(col, f"  {t.opened_at[:19]} -> {t.closed_at[:19]}  {t.side:5s}  "
                    f"{t.entry:.2f} -> {t.exit:.2f}  net={t.pnl_eur:+.3f} EUR ({t.pnl_pct:+.2f}%)  [{t.reason}]"))
    print("=" * 70 + "\n")


def run_once():
    log.info("=== cycle start ===")
    df = enrich(fetch_ohlc())
    sig = decide(df)
    state = pt.load_state()

    if state.halted:
        print(c(Fore.RED, f"Bot halted: {state.halt_reason}"))
        log.warning(f"halted: {state.halt_reason}")
        print_report(df, sig, state, state.halt_reason, None)
        plot_chart(df, sig, state.position)
        plot_equity(state.trades, START_EUR)
        return

    candle = df.iloc[-1]
    closed, breaker = pt.step(state, candle, sig)

    print_report(df, sig, state, breaker, closed)
    log.info(f"price={sig.price:.2f} rsi={sig.rsi:.2f} ema={sig.ema_trend:.2f} decision={sig.action} reason={sig.reason}")
    if closed:
        log.info(f"CLOSED {closed.side} @ {closed.exit:.2f} net={closed.pnl_eur:+.3f} EUR fees={closed.fees_eur:.3f} reason={closed.reason}")
    if state.position:
        p = state.position
        log.info(f"OPEN {p.side} entry={p.entry:.2f} sl={p.sl:.2f} tp={p.tp:.2f} margin={p.size_eur:.2f}")
    log.info(f"equity={state.equity_eur:.4f} EUR")

    plot_chart(df, sig, state.position)
    plot_equity(state.trades, START_EUR)


def main_loop():
    print(c(Fore.CYAN + Style.BRIGHT, f"ETH/USDT paper trading bot starting ({CYCLE_SECONDS}s cycle)"))
    log.info("bot started")
    while True:
        try:
            run_once()
        except Exception as e:
            log.exception(f"cycle error: {e}")
            print(c(Fore.RED, f"Cycle error: {e}"))
        print(c(Fore.BLUE, f"Sleeping {CYCLE_SECONDS}s..."))
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        main_loop()
