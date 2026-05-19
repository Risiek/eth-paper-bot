"""Kraken public OHLC fetcher."""
import requests
import pandas as pd
from config import PAIR, INTERVAL_MIN, CANDLE_LIMIT

KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"


def fetch_ohlc(pair: str = PAIR, interval: int = INTERVAL_MIN, limit: int = CANDLE_LIMIT) -> pd.DataFrame:
    r = requests.get(KRAKEN_URL, params={"pair": pair, "interval": interval}, timeout=15)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError(f"Kraken error: {payload['error']}")
    result = payload["result"]
    key = [k for k in result.keys() if k != "last"][0]
    rows = result[key]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    for c in ["open", "high", "low", "close", "vwap", "volume"]:
        df[c] = df[c].astype(float)
    df = df.tail(limit).reset_index(drop=True)
    return df


if __name__ == "__main__":
    df = fetch_ohlc()
    print(f"Fetched {len(df)} candles. Last close: {df['close'].iloc[-1]:.2f} USDT")
