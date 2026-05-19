"""Static chart: candles + BB + EMA + RSI + trade markers + equity curve."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from config import CHART_FILE, EQUITY_FILE, EMA_TREND


def plot_chart(df: pd.DataFrame, signal=None, position=None, out: str = CHART_FILE):
    d = df.set_index("time")[["open", "high", "low", "close", "volume"]].copy()
    d.index.name = "Date"

    apds = [
        mpf.make_addplot(df["bb_up"].values, color="#f85149", width=0.8),
        mpf.make_addplot(df["bb_mid"].values, color="#58a6ff", width=0.6),
        mpf.make_addplot(df["bb_low"].values, color="#3fb950", width=0.8),
        mpf.make_addplot(df["ema_trend"].values, color="#d29922", width=1.2),
        mpf.make_addplot(df["rsi"].values, panel=2, color="#a371f7", ylabel="RSI", ylim=(0, 100)),
    ]

    fig, axes = mpf.plot(
        d, type="candle", style="charles", addplot=apds,
        volume=True, panel_ratios=(3, 1, 1), figsize=(12, 8),
        returnfig=True,
        title=f"ETH/USDT 5m  |  signal: {signal.action if signal else 'N/A'}",
    )
    if axes:
        rsi_ax = axes[-1]
        rsi_ax.axhline(70, color="#f85149", linestyle="--", linewidth=0.6)
        rsi_ax.axhline(30, color="#3fb950", linestyle="--", linewidth=0.6)

    if position is not None:
        ax = axes[0]
        ax.axhline(position.entry, color="black", linestyle=":", linewidth=0.8, label=f"entry {position.entry:.2f}")
        ax.axhline(position.sl, color="red", linestyle=":", linewidth=0.8, label=f"SL {position.sl:.2f}")
        ax.axhline(position.tp, color="green", linestyle=":", linewidth=0.8, label=f"TP {position.tp:.2f}")
        ax.legend(loc="upper left", fontsize=7)

    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_equity(trades, start_eur: float, out: str = EQUITY_FILE):
    if not trades:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.axhline(start_eur, color="gray", linestyle="--")
        ax.set_title("Equity curve (no trades yet)")
        ax.set_ylabel("EUR")
        fig.savefig(out, dpi=110, bbox_inches="tight")
        plt.close(fig)
        return out
    eq = [start_eur]
    for t in trades:
        eq.append(eq[-1] + t.pnl_eur)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(range(len(eq)), eq, marker="o", color="navy")
    ax.axhline(start_eur, color="gray", linestyle="--", label=f"start {start_eur:.2f} EUR")
    ax.set_title("Equity curve (EUR, fees included)")
    ax.set_xlabel("trade #")
    ax.set_ylabel("EUR")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out


if __name__ == "__main__":
    from data_fetcher import fetch_ohlc
    from indicators import enrich
    from strategy import decide
    df = enrich(fetch_ohlc())
    sig = decide(df)
    print(plot_chart(df, sig))
