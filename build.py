"""Macro Lens builder: data/macro.json + template.html -> dashboard/index.html

Adds a weekly snapshot table and plain-language insights (what moved, what is
stretched). Insights describe the data; they are not trade calls.
"""
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
D = json.loads((ROOT / "data" / "macro.json").read_text(encoding="utf-8"))
S = D["series"]


def ser(sid, daily=False):
    if sid not in S:
        return None
    pts = S[sid].get("daily") if daily and S[sid].get("daily") else S[sid]["data"]
    s = pd.Series({pd.Timestamp(d): v for d, v in pts}).sort_index()
    return s if len(s) else None


def derived(kind, a, b):
    x, y = ser(a), ser(b)
    if x is None or y is None:
        return None
    df = pd.concat([x, y], axis=1, sort=True).dropna()
    return df.iloc[:, 0] / df.iloc[:, 1] if kind == "ratio" else df.iloc[:, 0] * df.iloc[:, 1]


# Snapshot rows: (label, series id or derived spec, kind) kind: pct = % change, bp = basis-point change, pt = point change
SNAP = [
    ("India", [
        ("Nifty 50", "Y:^NSEI", "pct"), ("Nifty 500", "Y:^CRSLDX", "pct"), ("Nifty Midcap 50", "Y:^NSEMDCP50", "pct"),
        ("India VIX", "Y:^INDIAVIX", "pt"), ("USD/INR", "Y:USDINR=X", "pct"), ("CNY/INR", "Y:CNYINR=X", "pct"),
        ("Brent in INR", ("mult", "Y:BZ=F", "Y:USDINR=X"), "pct"), ("Nifty in USD", ("ratio", "Y:^NSEI", "Y:USDINR=X"), "pct"),
    ]),
    ("Global equity", [
        ("S&P 500", "Y:^GSPC", "pct"), ("Nasdaq", "Y:^IXIC", "pct"), ("MSCI ACWI", "Y:ACWI", "pct"),
        ("Emerging markets", "Y:EEM", "pct"), ("Nikkei 225", "Y:^N225", "pct"), ("Hang Seng", "Y:^HSI", "pct"),
        ("Euro Stoxx 50", "Y:^STOXX50E", "pct"), ("KOSPI", "Y:^KS11", "pct"), ("Brazil Bovespa", "Y:^BVSP", "pct"),
    ]),
    ("AI complex", [
        ("Semiconductors (SMH)", "Y:SMH", "pct"), ("Nvidia", "Y:NVDA", "pct"), ("Apple", "Y:AAPL", "pct"),
        ("CoreWeave", "Y:CRWV", "pct"), ("Oracle", "Y:ORCL", "pct"),
    ]),
    ("Rates and credit", [
        ("US 2Y", "DGS2", "bp"), ("US 10Y", "DGS10", "bp"), ("US 30Y", "DGS30", "bp"), ("US 10Y real", "DFII10", "bp"),
        ("10Y minus 2Y", "T10Y2Y", "bp"), ("High-yield spread", "BAMLH0A0HYM2", "bp"),
        ("CCC spread", "BAMLH0A3HYC", "bp"), ("Investment-grade spread", "BAMLC0A0CM", "bp"),
        ("EM corporate spread", "BAMLEMCBPIOAS", "bp"), ("MOVE (bond vol)", "Y:^MOVE", "pt"), ("VIX", "Y:^VIX", "pt"),
    ]),
    ("Currencies", [
        ("DXY", "Y:DX-Y.NYB", "pct"), ("EUR/USD", "Y:EURUSD=X", "pct"), ("USD/JPY", "Y:USDJPY=X", "pct"),
        ("USD/CNY", "Y:USDCNY=X", "pct"), ("USD/KRW", "Y:USDKRW=X", "pct"), ("USD/BRL", "Y:USDBRL=X", "pct"),
    ]),
    ("Ratios", [
        ("Nifty 50 / gold", "R:NIFTY_GOLD", "pct"), ("Nifty 50 / Smallcap 100", "R:NIFTY_SMALL", "pct"),
        ("Nifty 50 / Midcap 100", "R:NIFTY_MID", "pct"),
    ]),
    ("Commodities", [
        ("Brent", "Y:BZ=F", "pct"), ("WTI", "Y:CL=F", "pct"), ("Natural gas", "Y:NG=F", "pct"), ("Gold", "Y:GC=F", "pct"),
        ("Silver", "Y:SI=F", "pct"), ("Copper", "Y:HG=F", "pct"), ("Wheat", "Y:ZW=F", "pct"), ("Bitcoin", "Y:BTC-USD", "pct"),
        ("Gold / silver ratio", ("ratio", "Y:GC=F", "Y:SI=F"), "pct"),
    ]),
]


def get(spec):
    if isinstance(spec, tuple):
        return derived(*spec)
    s = ser(spec)
    return s


def change(s, weeks, kind):
    if s is None or len(s) < 2:
        return None
    last_d = s.index[-1]
    past = s[s.index <= last_d - pd.Timedelta(weeks=weeks)]
    if past.empty:
        return None
    a, b = s.iloc[-1], past.iloc[-1]
    if kind == "pct":
        return (a / b - 1) * 100 if b else None
    if kind == "bp":
        return (a - b) * 100
    return a - b


def ytd(s, kind):
    if s is None:
        return None
    prev = s[s.index < pd.Timestamp(s.index[-1].year, 1, 1)]
    if prev.empty:
        return None
    a, b = s.iloc[-1], prev.iloc[-1]
    return (a / b - 1) * 100 if kind == "pct" else (a - b) * (100 if kind == "bp" else 1)


def rnd(x, d=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), d)


snapshot, moves = [], []
for group, rows in SNAP:
    out = []
    for label, spec, kind in rows:
        s = get(spec)
        if s is None or s.empty:
            continue
        s = s.dropna()
        yr = s[s.index > s.index[-1] - pd.Timedelta(weeks=52)]
        pos = (s.iloc[-1] - yr.min()) / (yr.max() - yr.min()) * 100 if yr.max() > yr.min() else None
        row = {"label": label, "kind": kind, "last": rnd(s.iloc[-1], 4), "asof": s.index[-1].strftime("%d %b %Y"),
               "w1": rnd(change(s, 1, kind)), "m1": rnd(change(s, 4, kind)), "m3": rnd(change(s, 13, kind)),
               "ytd": rnd(ytd(s, kind)), "y1": rnd(change(s, 52, kind)), "pos52": rnd(pos, 0),
               "spark": [rnd(v, 4) for v in yr.iloc[-26:]]}
        out.append(row)
        # how unusual is this week's move?
        if kind == "pct":
            wk = s.pct_change() * 100
        else:
            wk = s.diff() * (100 if kind == "bp" else 1)
        wk = wk.dropna().iloc[-260:]
        if len(wk) > 30 and wk.std() > 0:
            z = (wk.iloc[-1] - wk.mean()) / wk.std()
            bigger = wk.iloc[:-1][wk.iloc[:-1].abs() >= abs(wk.iloc[-1])]
            since = (len(wk) - 1 - wk.index.get_loc(bigger.index[-1])) if len(bigger) else len(wk)
            moves.append({"label": label, "kind": kind, "move": wk.iloc[-1], "z": z, "since": since,
                          "hi52": s.iloc[-1] >= yr.max(), "lo52": s.iloc[-1] <= yr.min()})
    snapshot.append({"group": group, "rows": out})


def fmt_move(m):
    v = m["move"]
    if m["kind"] == "pct":
        return f"{'rose' if v > 0 else 'fell'} {abs(v):.1f}%"
    if m["kind"] == "bp":
        return f"{'rose' if v > 0 else 'fell'} {abs(v):.0f} bp"
    return f"{'rose' if v > 0 else 'fell'} {abs(v):.1f} pts"


insights = []
for m in sorted(moves, key=lambda m: -abs(m["z"]))[:7]:
    if abs(m["z"]) < 1.5:
        break
    tail = (f", the largest weekly move in {m['since']} weeks" if m["since"] >= 8 else "")
    insights.append({"tag": "Big move", "text": f"{m['label']} {fmt_move(m)} this week{tail}."})
hi = [m["label"] for m in moves if m["hi52"]]
lo = [m["label"] for m in moves if m["lo52"]]
if hi:
    insights.append({"tag": "52-week high", "text": ", ".join(hi) + "."})
if lo:
    insights.append({"tag": "52-week low", "text": ", ".join(lo) + "."})


def add_if(cond, tag, text):
    if cond:
        insights.append({"tag": tag, "text": text})


hy = ser("BAMLH0A0HYM2")
if hy is not None:
    y1 = hy[hy.index > hy.index[-1] - pd.Timedelta(weeks=52)]
    insights.append({"tag": "Credit", "text": f"US high-yield spread is {hy.iloc[-1]*100:.0f} bp, "
                     f"{(hy.iloc[-1]-y1.min())*100:.0f} bp above its 12-month low of {y1.min()*100:.0f} bp. "
                     "Credit moves first: spreads widen before equities react."})
r = derived("ratio", "Y:ACWI", "Y:^GSPC")
if r is not None and len(r) > 30:
    c26 = (r.iloc[-1] / r.iloc[-27] - 1) * 100
    insights.append({"tag": "Rotation", "text": f"MSCI ACWI vs S&P 500 over 26 weeks: {c26:+.1f}%. "
                     + ("The world outside the US is outperforming." if c26 > 0 else "The US is still outperforming the world.")})
ab = derived("ratio", "Y:AAPL", "Y:SMH")
cw = ser("Y:CRWV")
if cw is not None:
    hi_ = cw.max()
    insights.append({"tag": "AI health", "text": f"CoreWeave is {(1-cw.iloc[-1]/hi_)*100:.0f}% below its peak of ${hi_:.0f}. "
                     "Neocloud stocks and their bond yields are an early read on the AI capex cycle."})
br = D.get("breadth")
if br and br.get("above200"):
    a200 = [v for v in br["above200"] if v is not None]
    if len(a200) > 21:
        insights.append({"tag": "Breadth", "text": f"{a200[-1]:.0f}% of Nifty 500 stocks are above their 200-day average "
                         f"(4 weeks ago: {a200[-21]:.0f}%). New 52-week highs vs lows today: {br['high52'][-1]:.0f} vs {br['low52'][-1]:.0f}."})
cpi = ser("IN_CPI")
if cpi is not None and len(cpi) > 1:
    insights.append({"tag": "India inflation", "text": f"India CPI inflation was {cpi.iloc[-1]:.2f}% in {cpi.index[-1]:%b %Y} "
                     f"(previous month {cpi.iloc[-2]:.2f}%)."})
ng = ser("IN_GDP_NOM")
if ng is not None:
    insights.append({"tag": "India growth", "text": f"India nominal GDP grew {ng.iloc[-1]:.1f}% YoY in the quarter to {ng.index[-1]:%b %Y}. "
                     "The sweet spot for mid and small caps is about 12% nominal (7% real plus 5% inflation)."})
fl = D.get("flows") or []
if fl:
    df = pd.DataFrame(fl)
    days = sorted(df["date"].unique())[-5:]
    w = df[df["date"].isin(days)].groupby("category")["net"].sum()
    span = "In the latest session" if len(days) == 1 else f"Over the last {len(days)} recorded sessions"
    insights.append({"tag": "Flows", "text": f"{span} FIIs net "
                     f"{'bought' if w.get('FII',0)>0 else 'sold'} INR {abs(w.get('FII',0)):,.0f} cr and DIIs net "
                     f"{'bought' if w.get('DII',0)>0 else 'sold'} INR {abs(w.get('DII',0)):,.0f} cr in the cash market."})
g = D.get("gold")
if g and g.get("table"):
    buyers = sorted([x for x in g["table"] if x.get("chg12m")], key=lambda x: -x["chg12m"])[:4]
    insights.append({"tag": "Gold", "text": "Biggest central bank gold buyers over 12 months (IMF data): "
                     + ", ".join(f"{b['country']} {b['chg12m']:+.0f} t" for b in buyers) + "."})

# Where today sits against history: z-score from the median (in standard deviations) and percentile
HIST = [
    ("Valuation", [("US equity market value to GDP", "US_MCAP_GDP"), ("India market cap to GDP (Buffett indicator)", "IN_MCAP_GDP")]),
    ("Ratios", [("Nifty 50 / gold", "R:NIFTY_GOLD"), ("Sensex / gold", "R:SENSEX_GOLD"), ("Nifty 50 / Smallcap 100", "R:NIFTY_SMALL"),
                ("Nifty 50 / Midcap 100", "R:NIFTY_MID"), ("Gold / silver", ("ratio", "Y:GC=F", "Y:SI=F")),
                ("Copper / gold", ("ratio", "Y:HG=F", "Y:GC=F"))]),
    ("Commodities", [("Brent (spot)", "DCOILBRENTEU"), ("WTI (spot)", "DCOILWTICO"), ("Gold", "Y:GC=F"), ("Silver", "Y:SI=F"),
                     ("Copper", "Y:HG=F"), ("US natural gas", "Y:NG=F"), ("Wheat", "Y:ZW=F"),
                     ("Brent in INR", ("mult", "DCOILBRENTEU", "DEXINUS"))]),
    ("Rates and credit", [("US 10Y yield", "DGS10"), ("US 10Y real yield", "DFII10"), ("US 10Y minus 2Y", "T10Y2Y"),
                          ("Moody's Baa minus 10Y", "BAA10Y"), ("India 10Y yield", "INDIRLTLT01STM"), ("VIX", "Y:^VIX"),
                          ("India VIX", "Y:^INDIAVIX")]),
    ("India macro and banking", [("India CPI inflation", "IN_CPI"), ("India real GDP growth", "IN_GDP_REAL"),
                                 ("Bank credit growth", "IN_CREDIT_YOY"), ("Bank deposit growth", "IN_DEPOSITS_YOY"),
                                 ("Credit to GDP (BIS)", "IN_CREDIT_GDP"), ("USD/INR", "DEXINUS")]),
]


def zstats(s):
    s = s.dropna()
    if len(s) < 24:
        return None
    med, sd, last = s.median(), s.std(ddof=0), s.iloc[-1]
    return {"z": rnd((last - med) / sd if sd else 0, 2), "pct": rnd((s < last).mean() * 100, 0), "med": rnd(med, 4)}


history = []
for group, rows in HIST:
    out = []
    for label, spec in rows:
        s = get(spec)
        if s is None or len(s) < 24:
            continue
        s = s.dropna()
        full, ten = zstats(s), zstats(s[s.index >= s.index[-1] - pd.DateOffset(years=10)])
        if not full:
            continue
        out.append({"label": label, "last": rnd(s.iloc[-1], 4), "asof": s.index[-1].strftime("%b %Y"), "since": int(s.index[0].year),
                    "med": full["med"], "z": full["z"], "pct": full["pct"], "z10": ten["z"] if ten else None,
                    "pct10": ten["pct"] if ten else None})
    history.append({"group": group, "rows": out})
stretched = [r for g in history for r in g["rows"] if r["z"] is not None and abs(r["z"]) >= 1.5]
if stretched:
    insights.append({"tag": "Vs history", "text": "Furthest from their long-run median: " + ", ".join(
        f"{r['label']} {r['z']:+.1f}σ" for r in sorted(stretched, key=lambda r: -abs(r["z"]))[:6]) + "."})
D["history"] = history

ev = ROOT / "data" / "events.json"
D["events"] = json.loads(ev.read_text(encoding="utf-8")) if ev.exists() else {}
D["snapshot"] = snapshot
D["insights"] = insights
D.pop("log", None)

html = (ROOT / "template.html").read_text(encoding="utf-8")
# optional add-ons: chart notes and company maps, sector scorecard, extra page scripts in site/
notes = ROOT / "data" / "notes.json"
D["notes"] = json.loads(notes.read_text(encoding="utf-8")) if notes.exists() else {}
try:
    import scorecard
    D["scorecard"] = scorecard.compute_scorecard(D)
except Exception as e:  # noqa: BLE001
    print(f"scorecard skipped: {e}")
    D["scorecard"] = None
extra_js = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "site").glob("*.js")))
payload = json.dumps(D, separators=(",", ":")).replace("</", "<\\/")
html = html.replace("/*__DATA__*/null", payload).replace("/*__EXTRA_JS__*/", extra_js)
out = ROOT / "dashboard" / "index.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html, encoding="utf-8")
print(f"Wrote {out} ({out.stat().st_size/1e6:.1f} MB), {len(insights)} insights")
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
