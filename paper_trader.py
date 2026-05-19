"""Paper trading engine: risk-based sizing, fees, SL/TP, drawdown."""
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from config import (
    START_EUR, PLN_PER_EUR, LEVERAGE, RISK_PCT, SL_PCT, TP_PCT, FEE_PCT,
    DAILY_DD_STOP, TOTAL_DD_STOP, STATE_FILE,
)


# Derived: price-distance for SL/TP given leverage and equity-% targets
# Hit SL = lose SL_PCT of margin (× LEVERAGE on price). So price move = SL_PCT / LEVERAGE
SL_PRICE_PCT = SL_PCT / LEVERAGE
TP_PRICE_PCT = TP_PCT / LEVERAGE


@dataclass
class Position:
    side: str
    entry: float
    size_eur: float      # margin (EUR) actually deployed
    notional_eur: float  # margin × leverage
    fee_open_eur: float  # fee on entry leg
    opened_at: str
    sl: float
    tp: float


@dataclass
class Trade:
    side: str
    entry: float
    exit: float
    size_eur: float
    notional_eur: float
    gross_pnl_eur: float
    fees_eur: float
    pnl_eur: float
    pnl_pct: float        # % of equity at entry
    opened_at: str
    closed_at: str
    reason: str           # TP / SL / SIGNAL


@dataclass
class State:
    equity_eur: float = START_EUR
    peak_equity_eur: float = START_EUR
    day_start_equity_eur: float = START_EUR
    day_key: str = ""
    position: Optional[Position] = None
    trades: list = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_position(raw) -> Optional[Position]:
    if not raw:
        return None
    return Position(
        side=raw["side"], entry=raw["entry"], size_eur=raw["size_eur"],
        notional_eur=raw.get("notional_eur", raw["size_eur"] * LEVERAGE),
        fee_open_eur=raw.get("fee_open_eur", 0.0),
        opened_at=raw["opened_at"], sl=raw["sl"], tp=raw["tp"],
    )


def _load_trade(raw) -> Trade:
    return Trade(
        side=raw["side"], entry=raw["entry"], exit=raw["exit"],
        size_eur=raw.get("size_eur", 0.0),
        notional_eur=raw.get("notional_eur", 0.0),
        gross_pnl_eur=raw.get("gross_pnl_eur", raw.get("pnl_eur", 0.0)),
        fees_eur=raw.get("fees_eur", 0.0),
        pnl_eur=raw["pnl_eur"], pnl_pct=raw["pnl_pct"],
        opened_at=raw["opened_at"], closed_at=raw["closed_at"], reason=raw["reason"],
    )


def load_state() -> State:
    if not os.path.exists(STATE_FILE):
        s = State(day_key=_today_key())
        save_state(s)
        return s
    with open(STATE_FILE, "r") as f:
        raw = json.load(f)
    s = State(
        equity_eur=raw["equity_eur"],
        peak_equity_eur=raw["peak_equity_eur"],
        day_start_equity_eur=raw["day_start_equity_eur"],
        day_key=raw["day_key"],
        position=_load_position(raw.get("position")),
        trades=[_load_trade(t) for t in raw.get("trades", [])],
        halted=raw.get("halted", False),
        halt_reason=raw.get("halt_reason", ""),
    )
    return s


def save_state(s: State):
    raw = {
        "equity_eur": s.equity_eur,
        "peak_equity_eur": s.peak_equity_eur,
        "day_start_equity_eur": s.day_start_equity_eur,
        "day_key": s.day_key,
        "position": asdict(s.position) if s.position else None,
        "trades": [asdict(t) for t in s.trades],
        "halted": s.halted,
        "halt_reason": s.halt_reason,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(raw, f, indent=2)


def _roll_day(s: State):
    today = _today_key()
    if s.day_key != today:
        s.day_key = today
        s.day_start_equity_eur = s.equity_eur


def check_circuit_breakers(s: State) -> Optional[str]:
    total_dd = 1 - (s.equity_eur / START_EUR)
    if total_dd >= TOTAL_DD_STOP:
        s.halted = True
        s.halt_reason = f"TOTAL drawdown {total_dd*100:.2f}% >= {TOTAL_DD_STOP*100:.0f}%"
        return s.halt_reason
    daily_dd = 1 - (s.equity_eur / s.day_start_equity_eur) if s.day_start_equity_eur > 0 else 0
    if daily_dd >= DAILY_DD_STOP:
        return f"DAILY drawdown {daily_dd*100:.2f}% >= {DAILY_DD_STOP*100:.0f}% (paused today)"
    return None


def _size_margin(equity_eur: float) -> float:
    """Risk-based sizing: lose RISK_PCT of equity when SL hits.

    SL price distance = SL_PRICE_PCT. With leverage L, hit SL -> margin loss = SL_PRICE_PCT * L.
    Want equity loss = RISK_PCT * equity. So margin = (RISK_PCT * equity) / (SL_PRICE_PCT * L).
    Capped at equity (no >100% margin).
    """
    target_margin = (RISK_PCT * equity_eur) / (SL_PRICE_PCT * LEVERAGE)
    return min(target_margin, equity_eur)


def open_position(s: State, side: str, price: float):
    if s.position is not None:
        return
    margin = _size_margin(s.equity_eur)
    notional = margin * LEVERAGE
    fee_open = notional * (FEE_PCT / 2.0)  # half of round-trip on entry

    if side == "LONG":
        sl = price * (1 - SL_PRICE_PCT)
        tp = price * (1 + TP_PRICE_PCT)
    else:
        sl = price * (1 + SL_PRICE_PCT)
        tp = price * (1 - TP_PRICE_PCT)

    # Charge entry fee immediately
    s.equity_eur -= fee_open

    s.position = Position(
        side=side, entry=price, size_eur=margin, notional_eur=notional,
        fee_open_eur=fee_open,
        opened_at=datetime.now(timezone.utc).isoformat(),
        sl=sl, tp=tp,
    )


def _price_move_pct(side: str, entry: float, exit_p: float) -> float:
    m = (exit_p - entry) / entry
    return m if side == "LONG" else -m


def close_position(s: State, exit_price: float, reason: str):
    if s.position is None:
        return None
    p = s.position
    move_pct = _price_move_pct(p.side, p.entry, exit_price)
    gross_pnl = p.notional_eur * move_pct
    fee_close = p.notional_eur * (FEE_PCT / 2.0)
    total_fees = p.fee_open_eur + fee_close
    net_pnl = gross_pnl - fee_close  # entry fee already deducted

    equity_at_entry = s.equity_eur + p.fee_open_eur  # pre-entry-fee equity
    s.equity_eur += net_pnl
    s.peak_equity_eur = max(s.peak_equity_eur, s.equity_eur)

    pnl_pct_equity = ((gross_pnl - total_fees) / equity_at_entry) * 100

    t = Trade(
        side=p.side, entry=p.entry, exit=exit_price,
        size_eur=p.size_eur, notional_eur=p.notional_eur,
        gross_pnl_eur=gross_pnl, fees_eur=total_fees,
        pnl_eur=gross_pnl - total_fees,
        pnl_pct=pnl_pct_equity,
        opened_at=p.opened_at,
        closed_at=datetime.now(timezone.utc).isoformat(),
        reason=reason,
    )
    s.trades.append(t)
    s.position = None
    return t


def check_sl_tp(s: State, high: float, low: float):
    if s.position is None:
        return None
    p = s.position
    if p.side == "LONG":
        if low <= p.sl:
            return close_position(s, p.sl, "SL")
        if high >= p.tp:
            return close_position(s, p.tp, "TP")
    else:
        if high >= p.sl:
            return close_position(s, p.sl, "SL")
        if low <= p.tp:
            return close_position(s, p.tp, "TP")
    return None


def step(s: State, candle, signal):
    _roll_day(s)
    closed = check_sl_tp(s, float(candle["high"]), float(candle["low"]))
    breaker = check_circuit_breakers(s)
    if s.halted:
        return closed, breaker
    if breaker:
        save_state(s)
        return closed, breaker
    if s.position is None and signal.action in ("LONG", "SHORT"):
        open_position(s, signal.action, signal.price)
    save_state(s)
    return closed, None
