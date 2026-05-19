"""Polished Flask dashboard with interactive Plotly chart.

Run bot in one terminal (python bot.py), app in another (python app.py).
Visit http://127.0.0.1:5000
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from flask import Flask, jsonify, render_template_string, Response

import paper_trader as pt
from config import (
    START_EUR, PLN_PER_EUR, STATE_FILE, RSI_OVERBOUGHT, RSI_OVERSOLD,
    EMA_TREND, CYCLE_SECONDS,
)
from data_fetcher import fetch_ohlc
from indicators import enrich
from strategy import decide
from news import fetch_news

app = Flask(__name__)

_lock = threading.Lock()
_cache = {
    "df": None,
    "signal": None,
    "ts": 0.0,
}
REFRESH_SEC = 30  # data refresh in-app


def _refresh():
    with _lock:
        df = enrich(fetch_ohlc())
        sig = decide(df)
        _cache["df"] = df
        _cache["signal"] = sig
        _cache["ts"] = time.time()


def _background():
    while True:
        try:
            _refresh()
        except Exception as e:
            print(f"[refresh err] {e}")
        time.sleep(REFRESH_SEC)


def _build_chart_figure(df: pd.DataFrame, position=None, trades=None):
    """Plotly figure: candles + BB + EMA200 + RSI subplot + volume + trade markers."""
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.18, 0.22],
        subplot_titles=("", "", ""),
    )

    # Candles
    fig.add_trace(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="ETH/USDT", increasing_line_color="#3fb950", decreasing_line_color="#f85149",
    ), row=1, col=1)

    # Bollinger
    fig.add_trace(go.Scatter(x=df["time"], y=df["bb_up"], name="BB upper",
                             line=dict(color="rgba(248,81,73,0.6)", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["time"], y=df["bb_low"], name="BB lower",
                             line=dict(color="rgba(63,185,80,0.6)", width=1),
                             fill="tonexty", fillcolor="rgba(88,166,255,0.05)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["time"], y=df["bb_mid"], name="BB mid",
                             line=dict(color="rgba(88,166,255,0.5)", width=1, dash="dot")), row=1, col=1)

    # EMA trend
    fig.add_trace(go.Scatter(x=df["time"], y=df["ema_trend"], name=f"EMA{EMA_TREND}",
                             line=dict(color="#d29922", width=1.6)), row=1, col=1)

    # Open position lines
    if position:
        for y, lbl, color in [
            (position["entry"], f"entry {position['entry']:.2f}", "#e6edf3"),
            (position["sl"], f"SL {position['sl']:.2f}", "#f85149"),
            (position["tp"], f"TP {position['tp']:.2f}", "#3fb950"),
        ]:
            fig.add_hline(y=y, line=dict(color=color, width=1, dash="dash"),
                          annotation_text=lbl, annotation_position="right",
                          annotation=dict(font=dict(color=color, size=10)),
                          row=1, col=1)

    # Trade markers
    if trades:
        for t in trades:
            try:
                opened = pd.to_datetime(t["opened_at"])
                closed = pd.to_datetime(t["closed_at"])
            except Exception:
                continue
            color = "#3fb950" if t["pnl_eur"] >= 0 else "#f85149"
            marker_in = "triangle-down" if t["side"] == "SHORT" else "triangle-up"
            fig.add_trace(go.Scatter(
                x=[opened, closed], y=[t["entry"], t["exit"]],
                mode="lines+markers",
                line=dict(color=color, width=2, dash="solid"),
                marker=dict(size=[10, 10], symbol=[marker_in, "x"], color=color),
                name=f"{t['side']} {t['pnl_pct']:+.2f}%",
                showlegend=False,
                hovertemplate=f"{t['side']} {t['entry']:.2f}->{t['exit']:.2f} pnl {t['pnl_eur']:+.3f} EUR<extra></extra>",
            ), row=1, col=1)

    # Volume bars
    vol_colors = ["#3fb950" if c >= o else "#f85149" for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df["time"], y=df["volume"], name="Volume",
                        marker_color=vol_colors, showlegend=False), row=2, col=1)

    # RSI
    fig.add_trace(go.Scatter(x=df["time"], y=df["rsi"], name="RSI",
                             line=dict(color="#a371f7", width=1.5)), row=3, col=1)
    fig.add_hline(y=RSI_OVERBOUGHT, line=dict(color="#f85149", width=1, dash="dash"), row=3, col=1)
    fig.add_hline(y=RSI_OVERSOLD, line=dict(color="#3fb950", width=1, dash="dash"), row=3, col=1)
    fig.add_hline(y=50, line=dict(color="rgba(139,148,158,0.4)", width=1, dash="dot"), row=3, col=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font=dict(family="ui-monospace, Menlo, Consolas, monospace", color="#e6edf3", size=11),
        margin=dict(l=50, r=80, t=30, b=30),
        height=720,
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Price (USDT)", row=1, col=1, gridcolor="#21262d")
    fig.update_yaxes(title_text="Vol", row=2, col=1, gridcolor="#21262d")
    fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100], gridcolor="#21262d")
    fig.update_xaxes(gridcolor="#21262d", showspikes=True, spikemode="across", spikethickness=1)

    return fig


def _build_equity_figure(trades: list):
    eq = [START_EUR]
    times = [None]
    for t in trades:
        eq.append(eq[-1] + t["pnl_eur"])
        times.append(t["closed_at"])
    xs = list(range(len(eq)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=eq, mode="lines+markers",
                             line=dict(color="#58a6ff", width=2),
                             marker=dict(size=6, color=["#8b949e"] + ["#3fb950" if t["pnl_eur"] >= 0 else "#f85149" for t in trades]),
                             name="Equity", hovertext=times, hovertemplate="%{y:.2f} EUR<br>%{hovertext}<extra></extra>"))
    fig.add_hline(y=START_EUR, line=dict(color="#8b949e", width=1, dash="dash"),
                  annotation_text=f"start {START_EUR:.2f}", annotation_position="right")
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(family="ui-monospace, Menlo, Consolas, monospace", color="#e6edf3", size=11),
        margin=dict(l=50, r=60, t=20, b=30),
        height=260,
        xaxis_title="trade #", yaxis_title="EUR",
        showlegend=False, hovermode="x",
    )
    fig.update_xaxes(gridcolor="#21262d")
    fig.update_yaxes(gridcolor="#21262d")
    return fig


@app.route("/")
def index():
    return render_template_string(TEMPLATE, EMA_TREND=EMA_TREND)


@app.route("/api/state")
def api_state():
    if not os.path.exists(STATE_FILE):
        s = pt.load_state()
    else:
        with open(STATE_FILE, "r") as f:
            raw = json.load(f)
        s = raw  # already dict
    if isinstance(s, pt.State):
        from dataclasses import asdict
        raw = {
            "equity_eur": s.equity_eur, "peak_equity_eur": s.peak_equity_eur,
            "day_start_equity_eur": s.day_start_equity_eur, "day_key": s.day_key,
            "position": asdict(s.position) if s.position else None,
            "trades": [asdict(t) for t in s.trades],
            "halted": s.halted, "halt_reason": s.halt_reason,
        }
    else:
        raw = s

    raw["start_eur"] = START_EUR
    raw["start_pln"] = START_EUR * PLN_PER_EUR
    raw["pln_per_eur"] = PLN_PER_EUR
    raw["equity_pln"] = raw["equity_eur"] * PLN_PER_EUR
    raw["pnl_eur"] = raw["equity_eur"] - START_EUR
    raw["pnl_pln"] = raw["pnl_eur"] * PLN_PER_EUR
    raw["pnl_pct"] = (raw["equity_eur"] / START_EUR - 1) * 100

    sig = _cache["signal"]
    if sig:
        raw["signal"] = {
            "price": sig.price, "rsi": sig.rsi,
            "bb_up": sig.bb_up, "bb_low": sig.bb_low,
            "ema_trend": sig.ema_trend,
            "action": sig.action, "reason": sig.reason,
        }
    else:
        raw["signal"] = None
    raw["last_refresh"] = _cache["ts"]
    raw["next_refresh_in"] = max(0, REFRESH_SEC - (time.time() - _cache["ts"])) if _cache["ts"] else 0
    return jsonify(raw)


@app.route("/api/chart")
def api_chart():
    import traceback
    try:
        if _cache["df"] is None:
            _refresh()
        state_raw = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                state_raw = json.load(f)
        pos = state_raw.get("position")
        trades = state_raw.get("trades", [])
        fig = _build_chart_figure(_cache["df"], position=pos, trades=trades)
        return Response(pio.to_json(fig), mimetype="application/json")
    except Exception as e:
        with open("app_err.log", "a") as ferr:
            traceback.print_exc(file=ferr)
            ferr.write(f"---\n")
        return jsonify({"error": str(e)}), 500


@app.route("/api/equity")
def api_equity():
    state_raw = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            state_raw = json.load(f)
    trades = state_raw.get("trades", [])
    fig = _build_equity_figure(trades)
    return Response(pio.to_json(fig), mimetype="application/json")


@app.route("/api/news")
def api_news():
    force = "refresh" in os.environ.get("QUERY_STRING", "")  # placeholder
    try:
        items = fetch_news()
        return jsonify({"items": items, "ts": _cache.get("ts", 0)})
    except Exception as e:
        return jsonify({"items": [], "error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        _refresh()
        return jsonify({"ok": True, "ts": _cache["ts"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


TEMPLATE = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>ETH Paper Trading Bot</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root { color-scheme: dark; --bg:#0a0e14; --bg2:#0d1117; --panel:#161b22; --panel2:#1c232c; --border:#30363d; --border2:#21262d; --text:#e6edf3; --muted:#8b949e; --dim:#6e7681; --green:#3fb950; --red:#f85149; --blue:#58a6ff; --yellow:#d29922; --purple:#a371f7; --accent:#58a6ff; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; }
  body { font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background:var(--bg); color:var(--text); font-size:13px; line-height:1.5; }
  .mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }

  header { padding:14px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; background:linear-gradient(180deg, var(--panel) 0%, var(--bg2) 100%); position:sticky; top:0; z-index:50; }
  header .brand { display:flex; align-items:center; gap:12px; }
  header h1 { margin:0; font-size:15px; font-weight:600; letter-spacing:0.3px; }
  header .meta { font-size:11px; color:var(--muted); display:flex; gap:18px; align-items:center; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:var(--green); box-shadow:0 0 8px var(--green); animation:pulse 1.8s infinite; }
  @keyframes pulse { 0%,100%{opacity:1; transform:scale(1);} 50%{opacity:0.4; transform:scale(0.85);} }

  .hero { padding:18px 24px; display:grid; grid-template-columns:repeat(6, 1fr); gap:12px; border-bottom:1px solid var(--border); background:var(--bg2); }
  .kpi { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:12px 16px; position:relative; transition:border-color 0.15s; }
  .kpi:hover { border-color:var(--accent); }
  .kpi .label { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.8px; font-weight:600; display:flex; align-items:center; gap:6px; }
  .kpi .value { font-size:22px; font-weight:700; margin-top:6px; letter-spacing:-0.3px; }
  .kpi .sub { font-size:11px; color:var(--muted); margin-top:3px; }

  .info { display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px; border-radius:50%; background:var(--border); color:var(--muted); font-size:9px; font-weight:700; cursor:help; }
  .info:hover { background:var(--accent); color:white; }
  #tooltip { position:fixed; max-width:300px; background:#0a0e14; color:var(--text); padding:8px 12px; border-radius:6px; border:1px solid var(--accent); font-size:11px; font-weight:400; line-height:1.5; z-index:9999; box-shadow:0 4px 20px rgba(0,0,0,0.6); pointer-events:none; opacity:0; transition:opacity 0.1s; text-transform:none; letter-spacing:0; font-family:"Inter", system-ui, sans-serif; }
  #tooltip.show { opacity:1; }

  .badge { display:inline-flex; align-items:center; padding:4px 12px; border-radius:6px; font-weight:700; font-size:11px; letter-spacing:0.5px; }
  .badge.SHORT { background:rgba(248,81,73,0.15); color:var(--red); border:1px solid rgba(248,81,73,0.4); }
  .badge.LONG { background:rgba(63,185,80,0.15); color:var(--green); border:1px solid rgba(63,185,80,0.4); }
  .badge.HOLD { background:rgba(139,148,158,0.15); color:var(--muted); border:1px solid rgba(139,148,158,0.4); }
  .badge.TP { background:rgba(63,185,80,0.18); color:var(--green); }
  .badge.SL { background:rgba(248,81,73,0.18); color:var(--red); }
  .badge.SIGNAL { background:rgba(88,166,255,0.18); color:var(--blue); }

  .green{color:var(--green);} .red{color:var(--red);} .yellow{color:var(--yellow);} .blue{color:var(--blue);} .muted{color:var(--muted);} .purple{color:var(--purple);}

  .body { display:grid; grid-template-columns:1fr 380px; gap:16px; padding:16px 24px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:10px; overflow:hidden; }
  .card h2 { margin:0; padding:12px 16px; font-size:11px; font-weight:600; letter-spacing:0.8px; text-transform:uppercase; color:var(--muted); border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .card h2 .title { display:flex; align-items:center; gap:8px; }
  .card .content { padding:14px 16px; }

  #chart { height:680px; }
  #equity { height:240px; }

  .stat { display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-bottom:1px dashed var(--border2); font-size:12px; gap:8px; }
  .stat:last-child{border-bottom:none;}
  .stat .k { color:var(--muted); display:flex; align-items:center; gap:6px; }
  .stat .v { color:var(--text); font-weight:500; }

  table { width:100%; border-collapse:collapse; font-size:11px; }
  th, td { padding:7px 10px; border-bottom:1px solid var(--border2); text-align:left; }
  th { color:var(--muted); font-weight:600; font-size:10px; text-transform:uppercase; letter-spacing:0.5px; background:var(--bg2); position:sticky; top:0; }
  tr:hover td { background:rgba(88,166,255,0.04); }

  button { background:var(--accent); color:#fff; border:none; padding:6px 14px; border-radius:6px; cursor:pointer; font-family:inherit; font-size:11px; font-weight:600; transition:background 0.15s; }
  button:hover { background:#79b8ff; }
  button.ghost { background:transparent; border:1px solid var(--border); color:var(--muted); }
  button.ghost:hover { background:var(--panel); color:var(--text); }

  .countdown { font-variant-numeric: tabular-nums; color:var(--text); }
  .empty { padding:20px; text-align:center; color:var(--muted); font-size:11px; font-style:italic; }
  .pill { padding:3px 8px; border-radius:5px; background:var(--bg2); border:1px solid var(--border); font-size:10px; color:var(--muted); font-weight:600; }

  details.glossary { background:var(--panel); border:1px solid var(--border); border-radius:10px; margin:0 24px 16px; }
  details.glossary > summary { padding:12px 16px; cursor:pointer; color:var(--muted); font-size:11px; font-weight:600; letter-spacing:0.5px; text-transform:uppercase; list-style:none; display:flex; align-items:center; gap:8px; }
  details.glossary > summary::-webkit-details-marker { display:none; }
  details.glossary > summary::before { content:"▶"; font-size:9px; transition:transform 0.15s; color:var(--accent); }
  details.glossary[open] > summary::before { transform:rotate(90deg); }
  details.glossary .glossary-grid { display:grid; grid-template-columns:repeat(2, 1fr); gap:14px; padding:0 16px 16px; }
  details.glossary .term { padding:10px 12px; background:var(--bg2); border-radius:6px; border-left:3px solid var(--accent); }
  details.glossary .term b { color:var(--text); font-size:12px; display:block; margin-bottom:4px; }
  details.glossary .term span { color:var(--muted); font-size:11px; line-height:1.5; }

  .news-list { padding:8px; display:grid; grid-template-columns:repeat(2, 1fr); gap:10px; }
  .news-item { padding:12px 14px; background:var(--bg2); border-radius:8px; border-left:3px solid var(--accent); transition:transform 0.12s, border-color 0.12s; }
  .news-item:hover { transform:translateY(-1px); border-left-color:var(--yellow); }
  .news-item .news-head { display:flex; justify-content:space-between; gap:8px; margin-bottom:6px; font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; }
  .news-item .news-title { font-size:13px; font-weight:600; color:var(--text); margin-bottom:6px; line-height:1.35; }
  .news-item .news-title a { color:inherit; text-decoration:none; }
  .news-item .news-title a:hover { color:var(--accent); text-decoration:underline; }
  .news-item .news-summary { font-size:11px; color:var(--muted); line-height:1.5; }
  .news-item .news-src { color:var(--accent); font-weight:600; }
  @media(max-width: 900px){ .news-list { grid-template-columns:1fr; } }

  @media(max-width: 1200px){ .body{ grid-template-columns:1fr; } .hero{ grid-template-columns:repeat(3,1fr);} }
  @media(max-width: 700px){ .hero{ grid-template-columns:repeat(2,1fr);} details.glossary .glossary-grid{ grid-template-columns:1fr;} }
</style>
</head>
<body>
<div id="tooltip"></div>
<header>
  <div class="brand">
    <span class="dot" id="dot" title="Status bota"></span>
    <h1>ETH/USDT - Bot na demo (paper trading)</h1>
    <span class="pill">świece 5m</span>
    <span class="pill">dźwignia 3x</span>
  </div>
  <div class="meta">
    <span>ostatnie odświeżenie: <span id="last-refresh" class="mono">-</span></span>
    <span>następne za: <span class="countdown mono" id="countdown">-</span>s</span>
    <button onclick="manualRefresh()">Odśwież teraz</button>
  </div>
</header>

<section class="hero">
  <div class="kpi">
    <div class="label">Sygnał <span class="info" data-tip="Decyzja bota co zrobić TERAZ. SHORT = obstaw spadek. LONG = obstaw wzrost. HOLD = czekaj, brak okazji."> ? </span></div>
    <div class="value"><span class="badge HOLD" id="signal-badge">HOLD</span></div>
    <div class="sub" id="signal-reason">-</div>
  </div>
  <div class="kpi">
    <div class="label">Kurs ETH <span class="info" data-tip="Aktualna cena 1 ETH w USDT (stablecoin ≈ 1 USD). Pobierana z Krakena."> ? </span></div>
    <div class="value yellow mono" id="price">-</div>
    <div class="sub">USDT za 1 ETH</div>
  </div>
  <div class="kpi">
    <div class="label">RSI(14) <span class="info" data-tip="Relative Strength Index - wskaźnik siły 0-100. Powyżej 70 = rynek 'przegrzany' (może spaść). Poniżej 30 = 'wyprzedany' (może odbić). Liczone z 14 ostatnich świec."> ? </span></div>
    <div class="value mono" id="rsi">-</div>
    <div class="sub" id="rsi-sub">-</div>
  </div>
  <div class="kpi">
    <div class="label">Kapitał <span class="info" data-tip="Aktualna wartość konta w EUR i PLN. Start: 500 PLN ≈ 117.65 EUR (kurs 4.25). Rośnie/maleje w miarę zamykania transakcji."> ? </span></div>
    <div class="value mono" id="equity-val">-</div>
    <div class="sub mono" id="equity-sub">-</div>
  </div>
  <div class="kpi">
    <div class="label">Wynik P&amp;L <span class="info" data-tip="Profit & Loss - zysk/strata od startu. Procent pokazuje zmianę kapitału. Zielony = na plusie, czerwony = na minusie. To pieniądze NIEREALNE (demo)."> ? </span></div>
    <div class="value mono" id="pnl-val">-</div>
    <div class="sub mono" id="pnl-sub">-</div>
  </div>
  <div class="kpi">
    <div class="label">Pozycja <span class="info" data-tip="Aktywna transakcja. LONG = bot kupił ETH licząc na wzrost. SHORT = bot 'sprzedał' licząc na spadek (zarabia jak cena spadnie). 'flat' = brak otwartej pozycji."> ? </span></div>
    <div class="value" id="pos-side">flat</div>
    <div class="sub mono" id="pos-sub">brak transakcji</div>
  </div>
</section>

<details class="glossary">
  <summary>Słowniczek - co oznaczają te wszystkie skróty? (kliknij, żeby rozwinąć)</summary>
  <div class="glossary-grid">
    <div class="term"><b>SHORT / LONG</b><span><b>SHORT</b> - obstawiasz że cena <i>spadnie</i> (sprzedajesz pożyczone, odkupujesz taniej, zgarniasz różnicę). <b>LONG</b> - obstawiasz <i>wzrost</i> (kupujesz, sprzedajesz drożej).</span></div>
    <div class="term"><b>Paper trading</b><span>Handel "na papierze" - wszystko symulowane, żadnych prawdziwych pieniędzy. Bot zapisuje wyimaginowane transakcje na realnych cenach z giełdy.</span></div>
    <div class="term"><b>Dźwignia (leverage 3x)</b><span>Bot handluje za 3× więcej niż ma w portfelu. Zysk/strata też × 3. Jak cena ruszy 1% to bot zyskuje/traci 3% kapitału.</span></div>
    <div class="term"><b>Margin (depozyt)</b><span>Kwota kapitału "zamrożona" jako zabezpieczenie pozycji. Z dźwignią 3x: za 100 EUR margin handlujesz "kontraktem" wartym 300 EUR (notional).</span></div>
    <div class="term"><b>Stop-Loss (SL)</b><span>Cena automatycznego zamknięcia pozycji ze stratą. Bot ustawia tak, żeby strata = max 2% kapitału. Chroni przed katastrofą.</span></div>
    <div class="term"><b>Take-Profit (TP)</b><span>Cena automatycznego zamknięcia z zyskiem. Bot ustawia na +4% kapitału. Gdy cena tam dotrze - bot inkasuje zysk.</span></div>
    <div class="term"><b>RSI</b><span>Wskaźnik 0-100 mierzący "pęd" ceny. >70 = za szybko rosło, prawdopodobne odbicie w dół. <30 = za szybko spadło, prawdopodobne odbicie w górę.</span></div>
    <div class="term"><b>Bollinger Bands (BB)</b><span>Dwie linie (górna i dolna) tworzące "tunel" wokół ceny. Cena rzadko wychodzi poza tunel - jeśli wyjdzie, często wraca. Strategia bota: szuka takich wyjść.</span></div>
    <div class="term"><b>EMA200</b><span>Exponential Moving Average z 200 świec - średnia "linia trendu". Cena powyżej = uptrend (rynek rośnie). Poniżej = downtrend (spada). Bot otwiera tylko zgodnie z trendem.</span></div>
    <div class="term"><b>Świeca (candle)</b><span>Jeden słupek na wykresie = jeden okres czasu (tu 5 minut). Pokazuje cenę otwarcia, zamknięcia, najwyższą i najniższą. Zielona = cena wzrosła. Czerwona = spadła.</span></div>
    <div class="term"><b>Drawdown</b><span>Strata od szczytu kapitału. Bot ma "wyłączniki" - jeśli dzienny drawdown >5%, pauza na dziś. Jeśli >15% od startu, bot STOP całkowicie (ochrona przed totalną stratą).</span></div>
    <div class="term"><b>Fee (prowizja)</b><span>0.10% wartości pozycji - kosztuje otwarcie+zamknięcie. Na giełdzie realnie płacisz prowizję. Bot symuluje to żeby wynik był realistyczny.</span></div>
  </div>
</details>

<section class="body">
  <div>
    <div class="card">
      <h2><span class="title">Wykres ceny <span class="info" data-tip="Świece 5-minutowe. Linie kolorowe: czerwona = górna BB, zielona = dolna BB, niebieska kropka = środek BB, żółta = EMA200 (trend). Pod spodem: wolumen i RSI."> ? </span></span><span class="pill">świece + BB + EMA + RSI</span></h2>
      <div id="chart"></div>
    </div>
    <div class="card" style="margin-top:16px;">
      <h2><span class="title">Krzywa kapitału <span class="info" data-tip="Jak zmieniał się Twój wirtualny kapitał po każdej zamkniętej transakcji. Linia przerywana = startowy 117.65 EUR. Zielone kropki = trade zyskowny, czerwone = stratny."> ? </span></span><span class="pill">EUR</span></h2>
      <div id="equity"></div>
    </div>
  </div>
  <aside>
    <div class="card">
      <h2><span class="title">Otwarta pozycja <span class="info" data-tip="Detale aktywnej transakcji - jeśli bot ma otwartą pozycję, tu zobaczysz cenę wejścia, SL, TP i bieżący niezrealizowany P&L."> ? </span></span></h2>
      <div class="content" id="position-card"><div class="empty">Brak otwartej pozycji</div></div>
    </div>
    <div class="card" style="margin-top:16px;">
      <h2><span class="title">Wskaźniki <span class="info" data-tip="Aktualne wartości BB i EMA. Bot porównuje je z ceną żeby zdecydować SHORT/LONG/HOLD."> ? </span></span></h2>
      <div class="content" id="indicators-card"></div>
    </div>
    <div class="card" style="margin-top:16px;">
      <h2><span class="title">Ostatnie 10 transakcji <span class="info" data-tip="Historia zamkniętych pozycji. 'Powód' = TP (take profit), SL (stop loss) albo SIGNAL (zamknięte przez przeciwny sygnał)."> ? </span></span><span class="pill" id="trades-count">0</span></h2>
      <div id="trades-card"><div class="empty">Brak transakcji</div></div>
    </div>
  </aside>
</section>

<section style="padding:0 24px 24px;">
  <div class="card">
    <h2><span class="title">📰 Wiadomości z rynku <span class="info" data-tip="Najświeższe wiadomości po polsku z BitHub, Comparic i Bankier. Filtrowane pod kątem kryptowalut, FED/EBC, stóp procentowych, dolara, ważnych ruchów rynkowych. Mogą wpływać na cenę ETH."> ? </span></span><span class="pill" id="news-meta">PL · auto co 10 min</span></h2>
    <div id="news-card"><div class="empty">Ładowanie wiadomości...</div></div>
  </div>
</section>

<script>
const fmt = (n, d=2) => (n>=0?"+":"") + Number(n).toFixed(d);
const num = (n, d=2) => Number(n).toFixed(d);
const ACTION_PL = { SHORT: "Sprzedaj (SHORT)", LONG: "Kup (LONG)", HOLD: "Czekaj (HOLD)" };

let _chartInit = false;
let _equityInit = false;
let _nextRefreshAt = 0;

async function loadState() {
  try {
    const r = await fetch("/api/state"); if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    const dot = document.getElementById("dot");
    dot.style.background = s.halted ? "var(--red)" : "var(--green)";
    dot.style.boxShadow = s.halted ? "0 0 8px var(--red)" : "0 0 8px var(--green)";
    document.getElementById("last-refresh").textContent = s.last_refresh ? new Date(s.last_refresh*1000).toLocaleTimeString("pl-PL") : "nigdy";
    _nextRefreshAt = (s.last_refresh + 30) * 1000;

    const sig = s.signal || {};
    const action = sig.action || "HOLD";
    const badge = document.getElementById("signal-badge");
    badge.className = "badge " + action;
    badge.textContent = action;
    document.getElementById("signal-reason").textContent = sig.reason || "-";
    document.getElementById("price").textContent = sig.price ? num(sig.price) : "-";

    const rsiEl = document.getElementById("rsi");
    if (sig.rsi !== undefined) {
      rsiEl.textContent = num(sig.rsi);
      rsiEl.className = "value mono " + (sig.rsi > 70 ? "red" : sig.rsi < 30 ? "green" : "");
      document.getElementById("rsi-sub").textContent = sig.rsi > 70 ? "przegrzany (możliwy spadek)" : sig.rsi < 30 ? "wyprzedany (możliwe odbicie)" : "neutralny";
    }

    document.getElementById("equity-val").textContent = num(s.equity_eur) + " EUR";
    document.getElementById("equity-sub").textContent = num(s.equity_pln) + " PLN (start " + num(s.start_pln) + ")";

    const pnlEl = document.getElementById("pnl-val");
    pnlEl.textContent = fmt(s.pnl_pct) + "%";
    pnlEl.className = "value mono " + (s.pnl_eur >= 0 ? "green" : "red");
    document.getElementById("pnl-sub").textContent = fmt(s.pnl_eur) + " EUR / " + fmt(s.pnl_pln) + " PLN";

    if (s.position) {
      const p = s.position;
      document.getElementById("pos-side").innerHTML = `<span class="badge ${p.side}">${p.side}</span>`;
      document.getElementById("pos-sub").textContent = `wejście ${num(p.entry)} | SL ${num(p.sl)} | TP ${num(p.tp)}`;
      let html = "";
      html += `<div class="stat"><span class="k">Strona <span class="info" data-tip="SHORT = obstawia spadek. LONG = obstawia wzrost.">?</span></span><span class="v"><span class="badge ${p.side}">${p.side}</span></span></div>`;
      html += `<div class="stat"><span class="k">Cena wejścia <span class="info" data-tip="Cena, po której bot 'wszedł' w pozycję.">?</span></span><span class="v mono">${num(p.entry)} USDT</span></div>`;
      html += `<div class="stat"><span class="k">Stop-Loss (SL) <span class="info" data-tip="Cena automatycznego zamknięcia ze stratą. Chroni przed dużą stratą.">?</span></span><span class="v red mono">${num(p.sl)} USDT</span></div>`;
      html += `<div class="stat"><span class="k">Take-Profit (TP) <span class="info" data-tip="Cena automatycznego zamknięcia z zyskiem. Bot inkasuje profit.">?</span></span><span class="v green mono">${num(p.tp)} USDT</span></div>`;
      html += `<div class="stat"><span class="k">Margin (depozyt) <span class="info" data-tip="Kapitał zamrożony na zabezpieczenie pozycji.">?</span></span><span class="v mono">${num(p.size_eur)} EUR</span></div>`;
      html += `<div class="stat"><span class="k">Notional (ekspozycja) <span class="info" data-tip="Rzeczywista wartość pozycji = margin × dźwignia. Tu × 3.">?</span></span><span class="v mono">${num(p.notional_eur||0)} EUR</span></div>`;
      html += `<div class="stat"><span class="k">Otwarta od</span><span class="v mono">${new Date(p.opened_at).toLocaleString("pl-PL")}</span></div>`;
      if (sig.price) {
        const unreal = p.side === "LONG" ? (sig.price - p.entry)/p.entry : (p.entry - sig.price)/p.entry;
        const unrealPct = unreal * 100 * 3;
        const unrealEur = (p.size_eur || 0) * unreal * 3;
        html += `<div class="stat"><span class="k">Aktualnie <span class="info" data-tip="Niezrealizowany P&L - co zarobiłbyś/stracił gdybyś zamknął teraz po aktualnej cenie. Dopóki pozycja otwarta, ta liczba się waha.">?</span></span><span class="v ${unreal>=0?'green':'red'} mono">${fmt(unrealPct)}% (${fmt(unrealEur, 3)} EUR)</span></div>`;
      }
      document.getElementById("position-card").innerHTML = html;
    } else {
      document.getElementById("pos-side").textContent = "flat";
      document.getElementById("pos-sub").textContent = "brak transakcji";
      document.getElementById("position-card").innerHTML = `<div class="empty">Brak otwartej pozycji - bot czeka na okazję</div>`;
    }

    const trend = sig.price > sig.ema_trend ? '<span class="green">wzrostowy ↑</span>' : '<span class="red">spadkowy ↓</span>';
    let ind = "";
    ind += `<div class="stat"><span class="k">Górna BB <span class="info" data-tip="Górna Bollinger Band - jeśli cena przekroczy, rynek 'przegrzany'.">?</span></span><span class="v red mono">${num(sig.bb_up||0)}</span></div>`;
    ind += `<div class="stat"><span class="k">Środkowa BB <span class="info" data-tip="Średnia 20-okresowa. Linia 'środka' rynku.">?</span></span><span class="v blue mono">${num((sig.bb_up+sig.bb_low)/2||0)}</span></div>`;
    ind += `<div class="stat"><span class="k">Dolna BB <span class="info" data-tip="Dolna Bollinger Band - jeśli cena spadnie poniżej, rynek 'wyprzedany'.">?</span></span><span class="v green mono">${num(sig.bb_low||0)}</span></div>`;
    ind += `<div class="stat"><span class="k">EMA{{EMA_TREND}} <span class="info" data-tip="Średnia z 200 świec - linia trendu długoterminowego.">?</span></span><span class="v yellow mono">${num(sig.ema_trend||0)}</span></div>`;
    ind += `<div class="stat"><span class="k">Trend <span class="info" data-tip="Cena powyżej EMA = trend wzrostowy. Poniżej = spadkowy.">?</span></span><span class="v">${trend}</span></div>`;
    document.getElementById("indicators-card").innerHTML = ind;

    const trades = (s.trades || []).slice(-10).reverse();
    document.getElementById("trades-count").textContent = (s.trades || []).length + " trade(s)";
    if (!trades.length) {
      document.getElementById("trades-card").innerHTML = `<div class="empty">Bot jeszcze nie zawarł żadnej transakcji</div>`;
    } else {
      let t = "<table><tr><th>zamknięte</th><th>typ</th><th>wejście</th><th>wyjście</th><th>zysk EUR</th><th>%</th><th>powód</th></tr>";
      for (const x of trades) {
        const col = x.pnl_eur >= 0 ? "green" : "red";
        const reasonBadge = `<span class="badge ${x.reason}">${x.reason}</span>`;
        t += `<tr><td class="mono">${new Date(x.closed_at).toLocaleString("pl-PL")}</td>`;
        t += `<td><span class="badge ${x.side}">${x.side}</span></td>`;
        t += `<td class="mono">${num(x.entry)}</td><td class="mono">${num(x.exit)}</td>`;
        t += `<td class="${col} mono">${fmt(x.pnl_eur, 3)}</td>`;
        t += `<td class="${col} mono">${fmt(x.pnl_pct)}%</td>`;
        t += `<td>${reasonBadge}</td></tr>`;
      }
      t += "</table>";
      document.getElementById("trades-card").innerHTML = t;
    }
  } catch (e) {
    console.error(e);
  }
}

async function loadChart() {
  try {
    const r = await fetch("/api/chart"); if (!r.ok) return;
    const fig = await r.json();
    if (!_chartInit) {
      Plotly.newPlot("chart", fig.data, fig.layout, {responsive:true, displayModeBar:false});
      _chartInit = true;
    } else {
      Plotly.react("chart", fig.data, fig.layout);
    }
  } catch(e) { console.error(e); }
}

async function loadEquity() {
  try {
    const r = await fetch("/api/equity"); if (!r.ok) return;
    const fig = await r.json();
    if (!_equityInit) {
      Plotly.newPlot("equity", fig.data, fig.layout, {responsive:true, displayModeBar:false});
      _equityInit = true;
    } else {
      Plotly.react("equity", fig.data, fig.layout);
    }
  } catch(e) { console.error(e); }
}

async function loadNews() {
  try {
    const r = await fetch("/api/news"); if (!r.ok) return;
    const data = await r.json();
    const items = data.items || [];
    if (!items.length) {
      document.getElementById("news-card").innerHTML = `<div class="empty">Brak wiadomości</div>`;
      return;
    }
    const now = Date.now() / 1000;
    let html = `<div class="news-list">`;
    for (const n of items) {
      const ageMin = Math.round((now - n.ts) / 60);
      const ageStr = ageMin < 60 ? `${ageMin} min temu` : ageMin < 1440 ? `${Math.round(ageMin/60)} h temu` : `${Math.round(ageMin/1440)} dni temu`;
      html += `<div class="news-item">`;
      html += `<div class="news-head"><span class="news-src">${n.source}</span><span>${ageStr}</span></div>`;
      html += `<div class="news-title"><a href="${n.link}" target="_blank" rel="noopener">${n.title}</a></div>`;
      html += `<div class="news-summary">${n.summary}</div>`;
      html += `</div>`;
    }
    html += `</div>`;
    document.getElementById("news-card").innerHTML = html;
  } catch(e) { console.error(e); }
}

async function manualRefresh() {
  await fetch("/api/refresh", {method:"POST"});
  await Promise.all([loadState(), loadChart(), loadEquity(), loadNews()]);
}

function tickCountdown() {
  const remaining = Math.max(0, Math.round((_nextRefreshAt - Date.now())/1000));
  document.getElementById("countdown").textContent = remaining;
}

async function tick() {
  await loadState();
  await Promise.all([loadChart(), loadEquity()]);
}

tick();
loadNews();
setInterval(tick, 10000);
setInterval(loadNews, 600000);  // refresh news every 10 min
setInterval(tickCountdown, 1000);

// Viewport-clamped tooltips (event delegation for dynamic content)
const tooltipEl = document.getElementById("tooltip");
const MARGIN = 8;
function positionTooltip(target) {
  const tip = target.getAttribute("data-tip");
  if (!tip) return;
  tooltipEl.textContent = tip;
  tooltipEl.classList.add("show");
  const rect = target.getBoundingClientRect();
  // Render first to measure
  tooltipEl.style.left = "0px";
  tooltipEl.style.top = "0px";
  const tipRect = tooltipEl.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // Prefer above element, centered horizontally
  let top = rect.top - tipRect.height - 8;
  let left = rect.left + rect.width/2 - tipRect.width/2;
  // Flip below if no room above
  if (top < MARGIN) top = rect.bottom + 8;
  // If still off-bottom, clamp
  if (top + tipRect.height > vh - MARGIN) top = vh - tipRect.height - MARGIN;
  // Clamp horizontally
  if (left < MARGIN) left = MARGIN;
  if (left + tipRect.width > vw - MARGIN) left = vw - tipRect.width - MARGIN;
  tooltipEl.style.left = left + "px";
  tooltipEl.style.top = top + "px";
}
function hideTooltip() {
  tooltipEl.classList.remove("show");
}
document.body.addEventListener("mouseover", (e) => {
  const t = e.target.closest(".info[data-tip]");
  if (t) positionTooltip(t);
});
document.body.addEventListener("mouseout", (e) => {
  const t = e.target.closest(".info[data-tip]");
  if (t) hideTooltip();
});
window.addEventListener("scroll", hideTooltip, true);
window.addEventListener("resize", hideTooltip);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    threading.Thread(target=_background, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
