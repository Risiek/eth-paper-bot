"""Short-focused strategy: RSI + BB + volume + EMA200 trend filter."""
from dataclasses import dataclass
from config import RSI_OVERBOUGHT, RSI_OVERSOLD


@dataclass
class Signal:
    action: str
    reason: str
    price: float
    rsi: float
    bb_up: float
    bb_low: float
    ema_trend: float


def decide(df) -> Signal:
    row = df.iloc[-1]
    price = float(row["close"])
    r = float(row["rsi"])
    up = float(row["bb_up"])
    low = float(row["bb_low"])
    ema_t = float(row["ema_trend"])
    vol_rising = bool(row["vol_rising"])

    short_signal = r > RSI_OVERBOUGHT and price > up and vol_rising
    long_signal = r < RSI_OVERSOLD and price < low and vol_rising

    # Trend filter: counter-trend trades only in line with broader trend
    # SHORT (mean-revert from overbought) allowed only in downtrend
    # LONG (mean-revert from oversold) allowed only in uptrend
    downtrend = price < ema_t
    uptrend = price > ema_t

    if short_signal and downtrend:
        return Signal("SHORT",
                      f"RSI={r:.1f}>{RSI_OVERBOUGHT}, price>BB_up, vol rising, downtrend (price<EMA{int(ema_t)})",
                      price, r, up, low, ema_t)
    if long_signal and uptrend:
        return Signal("LONG",
                      f"RSI={r:.1f}<{RSI_OVERSOLD}, price<BB_low, vol rising, uptrend (price>EMA{int(ema_t)})",
                      price, r, up, low, ema_t)

    reasons = []
    if short_signal and not downtrend:
        reasons.append(f"SHORT blocked: price {price:.2f} >= EMA {ema_t:.2f}")
    elif long_signal and not uptrend:
        reasons.append(f"LONG blocked: price {price:.2f} <= EMA {ema_t:.2f}")
    else:
        if not (r > RSI_OVERBOUGHT or r < RSI_OVERSOLD):
            reasons.append(f"RSI={r:.1f} neutral")
        if low <= price <= up:
            reasons.append("price inside BB")
        if not vol_rising:
            reasons.append("vol not rising")
    return Signal("HOLD", "; ".join(reasons) or "no edge", price, r, up, low, ema_t)


if __name__ == "__main__":
    from data_fetcher import fetch_ohlc
    from indicators import enrich
    print(decide(enrich(fetch_ohlc())))
