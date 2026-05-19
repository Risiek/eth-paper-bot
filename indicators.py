"""RSI + Bollinger Bands + EMA trend."""
import pandas as pd
import numpy as np
from config import RSI_PERIOD, BB_PERIOD, BB_STD, EMA_TREND


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger(close: pd.Series, period: int = BB_PERIOD, std_mult: float = BB_STD):
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return ma, ma + std_mult * std, ma - std_mult * std


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = rsi(df["close"])
    df["bb_mid"], df["bb_up"], df["bb_low"] = bollinger(df["close"])
    df["ema_trend"] = ema(df["close"], EMA_TREND)
    df["vol_rising"] = df["volume"] > df["volume"].shift(1)
    return df


if __name__ == "__main__":
    from data_fetcher import fetch_ohlc
    df = enrich(fetch_ohlc())
    print(df[["time", "close", "rsi", "bb_up", "bb_low", "ema_trend", "vol_rising"]].tail(5))
