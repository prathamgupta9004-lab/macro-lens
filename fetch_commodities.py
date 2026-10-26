"""Macro Lens: commodity dashboard data (Yahoo Finance futures and ETF proxies).

fetch_commodities() returns a JSON-ready dict that fetch.py stores as D.commodities:
  {"generated", "groups", "items": [{sym, name, group, unit, last, last_date, changes, lo52, hi52,
    pos52, med10, sd10, daily, weekly, monthly, stats, insights, india}], "dropped": [...]}
Insights are observations derived from each price history, never trade calls.
"""
import math
import time

import numpy as np
import pandas as pd
import yfinance as yf

# sym: (name, short name used in sentences, group, unit, money prefix, money suffix)
ITEMS = {
    # Energy
    "CL=F": ("WTI crude oil", "WTI", "Energy", "USD/bbl", "$", "/bbl"),
    "BZ=F": ("Brent crude oil", "Brent", "Energy", "USD/bbl", "$", "/bbl"),
    "NG=F": ("Natural gas (Henry Hub)", "US natural gas", "Energy", "USD/MMBtu", "$", "/MMBtu"),
    "HO=F": ("Heating oil (diesel)", "Heating oil", "Energy", "USD/gal", "$", "/gal"),
    "RB=F": ("Gasoline (RBOB)", "Gasoline", "Energy", "USD/gal", "$", "/gal"),
    # Metals
    "GC=F": ("Gold", "Gold", "Metals", "USD/t oz", "$", "/oz"),
    "SI=F": ("Silver", "Silver", "Metals", "USD/t oz", "$", "/oz"),
    "PL=F": ("Platinum", "Platinum", "Metals", "USD/t oz", "$", "/oz"),
    "PA=F": ("Palladium", "Palladium", "Metals", "USD/t oz", "$", "/oz"),
    "HG=F": ("Copper (COMEX)", "Copper", "Metals", "USD/lb", "$", "/lb"),
    "ALI=F": ("Aluminium (COMEX)", "Aluminium", "Metals", "USD/t", "$", "/t"),
    "HRC=F": ("US steel HRC", "US hot-rolled steel", "Metals", "USD/short ton", "$", "/short ton"),
    "TIO=F": ("Iron ore 62% Fe", "Iron ore", "Metals", "USD/t", "$", "/t"),
    "LIT": ("Lithium (LIT ETF proxy)", "The LIT lithium ETF", "Metals", "USD/share (ETF)", "$", ""),
    "URA": ("Uranium (URA ETF proxy)", "The URA uranium ETF", "Metals", "USD/share (ETF)", "$", ""),
    # Agriculture
    "ZW=F": ("Wheat (Chicago)", "Wheat", "Agriculture", "US cents/bu", "", " cents/bu"),
    "ZC=F": ("Corn", "Corn", "Agriculture", "US cents/bu", "", " cents/bu"),
    "ZS=F": ("Soybeans", "Soybeans", "Agriculture", "US cents/bu", "", " cents/bu"),
    "ZM=F": ("Soybean meal", "Soybean meal", "Agriculture", "USD/short ton", "$", "/short ton"),
    "ZL=F": ("Soybean oil", "Soybean oil", "Agriculture", "US cents/lb", "", " cents/lb"),
    "ZO=F": ("Oats", "Oats", "Agriculture", "US cents/bu", "", " cents/bu"),
    "ZR=F": ("Rough rice", "Rough rice", "Agriculture", "USD/cwt", "$", "/cwt"),
    "KC=F": ("Coffee (arabica)", "Coffee", "Agriculture", "US cents/lb", "", " cents/lb"),
    "SB=F": ("Sugar No. 11", "Raw sugar", "Agriculture", "US cents/lb", "", " cents/lb"),
    "CT=F": ("Cotton", "Cotton", "Agriculture", "US cents/lb", "", " cents/lb"),
    "CC=F": ("Cocoa", "Cocoa", "Agriculture", "USD/t", "$", "/t"),
    "OJ=F": ("Orange juice", "Orange juice", "Agriculture", "US cents/lb", "", " cents/lb"),
    # Livestock
    "LE=F": ("Live cattle", "Live cattle", "Livestock", "US cents/lb", "", " cents/lb"),
    "GF=F": ("Feeder cattle", "Feeder cattle", "Livestock", "US cents/lb", "", " cents/lb"),
    "HE=F": ("Lean hogs", "Lean hogs", "Livestock", "US cents/lb", "", " cents/lb"),
}
# rupee prices derived from the dollar contracts and USDINR (international price converted, before duty and GST)
INR_ITEMS = {
    "GOLD_INR": ("Gold in rupees", "Gold in rupees", "India prices", "INR/10 g", "₹", " per 10 g", "GC=F", 10 / 31.1034768),
    "SILVER_INR": ("Silver in rupees", "Silver in rupees", "India prices", "INR/kg", "₹", " per kg", "SI=F", 1000 / 31.1034768),
    "BRENT_INR": ("Brent in rupees", "Brent in rupees", "India prices", "INR/bbl", "₹", " per barrel", "BZ=F", 1.0),
}
GROUPS = ["Energy", "Metals", "Agriculture", "Livestock", "India prices"]
REFS = {"GC=F": "gold", "BZ=F": "Brent", "HG=F": "copper", "DX-Y.NYB": "the US dollar index", "^GSPC": "the S&P 500", "^NSEI": "the Nifty 50"}

INDIA = {
    "CL=F": "WTI sets the price of US crude exports and feeds into the global oil benchmark India pays. India imports about 85% of its crude, so a higher oil price widens the current account deficit and pressures the rupee.",
    "BZ=F": "Brent is the benchmark for most of India's crude imports (India imports about 85% of its oil). Higher Brent widens the current account deficit, weakens the rupee, lifts inflation and raises input costs for paints, tyres, airlines, chemicals and oil marketing companies.",
    "NG=F": "India imports about half its gas as LNG, much of it on oil-linked or spot contracts. Gas prices drive costs for city gas distributors, fertiliser plants and gas-based power, and feed into the domestic APM gas price formula.",
    "HO=F": "Heating oil tracks global diesel, India's largest fuel by volume. Diesel cracks drive refining margins for Reliance and state refiners, and diesel costs feed into freight and food inflation.",
    "RB=F": "Gasoline cracks drive Indian refiners' export margins and petrol marketing margins for oil marketing companies when retail prices are held steady.",
    "GC=F": "India is the world's second-largest gold consumer and imports almost all of it, so gold prices swing the trade deficit. Higher gold lifts collateral values for gold-loan lenders (Muthoot, Manappuram) and hurts jewellery volumes even as it raises realisations.",
    "SI=F": "India is one of the largest silver importers, for jewellery, investment and solar panels. Silver prices move import bills and margins for jewellers and solar module makers.",
    "PL=F": "Platinum is used in autocatalysts for diesel vehicles and in jewellery; India imports nearly all it uses.",
    "PA=F": "Palladium goes into petrol-car catalytic converters, a cost item for Indian auto component makers.",
    "HG=F": "Copper feeds cables, wires, transformers and power equipment, and India imports most of its copper concentrate. Higher copper lifts realisations for Hindalco and Vedanta but raises costs for cable makers, electrical equipment and EV supply chains.",
    "ALI=F": "Aluminium prices drive earnings for Hindalco, Vedanta and NALCO, and costs for auto, packaging and power transmission companies.",
    "HRC=F": "US hot-rolled coil prices are a reference for global steel. Indian steelmakers (Tata Steel, JSW, SAIL) price against import parity, so global HRC affects their realisations and the cost of capital goods and construction.",
    "TIO=F": "Iron ore is the main raw material for Indian steel and NMDC's key product; India is also an ore exporter, so global prices affect both steel margins and export earnings.",
    "LIT": "Lithium sets battery cell costs for India's EV and energy storage plans, which rely almost fully on imported cells. This is an ETF of lithium miners and battery makers, used as a price proxy.",
    "URA": "India plans a large nuclear power build-out and imports most of its uranium fuel. This is an ETF of uranium miners, used as a price proxy.",
    "ZW=F": "India is the second-largest wheat producer and is mostly self-sufficient; global wheat matters for export bans, import duty decisions and flour millers' and biscuit makers' costs.",
    "ZC=F": "Corn feeds poultry and starch makers and is a growing ethanol feedstock in India, linking global corn to feed and blending costs.",
    "ZS=F": "Soybeans drive soymeal export competitiveness and edible oil prices; India is a large soy grower in Madhya Pradesh and Maharashtra.",
    "ZM=F": "Soymeal is the main protein in poultry feed, a large cost for poultry companies, and an Indian export crop.",
    "ZL=F": "India is the world's largest edible oil importer, so soybean oil prices move food inflation and margins for edible oil packers and FMCG companies.",
    "ZO=F": "Oats are a small but growing breakfast and health-food input for Indian packaged food companies, almost all imported.",
    "ZR=F": "India is the world's largest rice exporter, so global rice prices affect export earnings, export-ban decisions and margins for rice exporters.",
    "KC=F": "India exports coffee (mainly robusta from Karnataka), so global prices lift growers' and exporters' income and raise costs for coffee brands.",
    "SB=F": "India is the second-largest sugar producer; world prices decide whether exports are viable and drive margins for sugar mills and diversion to ethanol.",
    "CT=F": "India is among the largest cotton producers; cotton is the key raw material cost for textile and apparel exporters.",
    "CC=F": "Cocoa is the main cost for chocolate makers (Nestle India, Mondelez, ITC) and is largely imported.",
    "OJ=F": "Orange juice concentrate is a niche import for Indian juice brands; it mostly signals global weather and crop stress.",
    "LE=F": "US cattle prices have little direct link to India, but they are a gauge of global protein inflation; India is a large buffalo meat exporter.",
    "GF=F": "Feeder cattle prices track US herd economics; for India they are a distant gauge of global meat and feed demand.",
    "HE=F": "Lean hog prices track US pork supply and global protein demand; the link to India is indirect, through feed grain demand.",
    "GOLD_INR": "This is the international gold price converted to rupees, before import duty and GST, so it sits below MCX and jeweller rates. It is what matters for Indian households, gold-loan collateral values and the gold import bill.",
    "SILVER_INR": "International silver converted to rupees, before import duty and GST. It reflects the cost for Indian jewellers, silver investors and solar module makers.",
    "BRENT_INR": "Brent in rupee terms is what India's refiners actually pay, so it combines the oil price and the exchange rate. It is the cleaner gauge of the hit to the import bill, fuel subsidies and oil marketing margins.",
}


def log(msg):
    print(msg, flush=True)


def retry(fn, tries=3, wait=3):
    for i in range(tries):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))


def r(v):
    """Round to about 5 significant figures."""
    if v is None or not np.isfinite(v):
        return None
    if v == 0:
        return 0.0
    return round(float(v), max(0, 4 - int(math.floor(math.log10(abs(v))))))


def r1(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 1)


def pts(s):
    return [[d.strftime("%Y-%m-%d"), r(v)] for d, v in s.items() if np.isfinite(v)]


def ordinal(n):
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def indian(v, d=0):
    """Indian digit grouping: 1,23,456."""
    neg, v = v < 0, abs(v)
    s = f"{v:.{d}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def money(v, pre, suf):
    a = abs(v)
    d = 0 if a >= 1000 else 1 if a >= 100 else 2 if a >= 1 else 3
    if pre == "₹":
        return f"{pre}{indian(v, 0 if a >= 100 else 2)}{suf}"
    return f"{pre}{v:,.{d}f}{suf}"


def sgn(v, d=0):
    x = round(v, d)
    return f"{0:.{d}f}%" if x == 0 else f"{x:+.{d}f}%"


def mon(d):
    return pd.Timestamp(d).strftime("%b %Y")


# --------------------------------------------------------------------------- analytics
def change(s, last_d, offset):
    base = s[s.index <= last_d - offset]
    return None if base.empty or base.iloc[-1] <= 0 else (s.iloc[-1] / base.iloc[-1] - 1) * 100


def ytd_table(s):
    """YTD change for the current year and for every past year over the same calendar window."""
    last_d = s.index[-1]
    out = {}
    for y in range(s.index[0].year + 1, last_d.year + 1):
        prev = s[s.index.year == y - 1]
        end = pd.Timestamp(y, last_d.month, 1) + pd.Timedelta(days=last_d.day - 1)
        cur = s[(s.index.year == y) & (s.index <= end)]
        if prev.empty or cur.empty or prev.iloc[-1] <= 0 or (end - cur.index[-1]).days > 10:
            continue
        out[y] = (cur.iloc[-1] / prev.iloc[-1] - 1) * 100
    return out


def episodes(s, last, last_d):
    """Non-overlapping past dates when price was within 5% of today, and what happened 3/6/12 months later."""
    old = s[s.index <= last_d - pd.Timedelta(days=365)]
    near = old[(old / last - 1).abs() <= 0.05]
    picks, prev = [], None
    for d in near.index:
        if prev is None or (d - prev).days >= 182:
            picks.append(d)
            prev = d
    res = {}
    for k in (3, 6, 12):
        fw = []
        for d in picks:
            tgt = d + pd.DateOffset(months=k)
            if tgt > last_d:
                continue
            v0, v1 = s.loc[d], s[s.index <= tgt].iloc[-1]
            if v0 > 0:
                fw.append((v1 / v0 - 1) * 100)
        if fw:
            a = np.array(fw)
            res[f"m{k}"] = {"n": len(a), "avg": r1(a.mean()), "med": r1(np.median(a)), "fell": int((a < 0).sum()), "fell_pct": r1((a < 0).mean() * 100)}
    return {"n": len(picks), "dates": [d.strftime("%Y-%m-%d") for d in picks], **res}


def fwd12(s, ep):
    out = []
    for d in ep["dates"]:
        d = pd.Timestamp(d)
        tgt = d + pd.DateOffset(months=12)
        if tgt <= s.index[-1] and s.loc[d] > 0:
            out.append((s[s.index <= tgt].iloc[-1] / s.loc[d] - 1) * 100)
    return out


def analyse(sym, s, refs_m):
    meta = ITEMS.get(sym) or INR_ITEMS[sym]
    name, short, group, unit, pre, suf = meta[:6]
    last, last_d = float(s.iloc[-1]), s.index[-1]
    s10 = s[s.index > last_d - pd.DateOffset(years=10)]
    med10, sd10 = float(s10.median()), float(s10.std())
    pct10 = float((s10 < last).mean() * 100)
    pct_all = float((s < last).mean() * 100)
    z10 = (last - med10) / sd10 if sd10 else 0.0
    high_side = pct10 >= 50
    # last time at/above (or at/below) today's level, at least 3 months ago
    past = s[s.index <= last_d - pd.Timedelta(days=91)]
    hit = past[past >= last] if high_side else past[past <= last]
    last_seen = hit.index[-1].strftime("%Y-%m-%d") if not hit.empty else None
    # if that was recent, find where the current stretch at this level began and the time before it
    allhit = s[s >= last] if high_side else s[s <= last]
    run_start, prior = None, None
    if last_seen and (last_d - pd.Timestamp(last_seen)).days < 365 and len(allhit):
        idx = allhit.index
        gaps = np.where(np.diff(idx.values).astype("timedelta64[D]").astype(int) >= 182)[0]
        if len(gaps):
            run_start, prior = idx[gaps[-1] + 1], idx[gaps[-1]]
        else:
            run_start = idx[0]
    ep = episodes(s, last, last_d)
    yt = ytd_table(s)
    ytd = yt.get(last_d.year)
    ytd_info = None
    if ytd is not None and len(yt) >= 6:
        past_y = {y: v for y, v in yt.items() if y < last_d.year}
        better = sum(1 for v in past_y.values() if v > ytd)
        rank = better + 1  # 1 = strongest
        if ytd >= 0:
            since = max([y for y, v in past_y.items() if v >= ytd], default=None)
        else:
            since = max([y for y, v in past_y.items() if v <= ytd], default=None)
        ytd_info = {"ytd": r1(ytd), "rank": rank, "years": len(yt), "since": since, "first": min(yt)}
    ath_d = s.idxmax()
    ath = float(s.max())
    dd = (last / ath - 1) * 100
    atl_d = s.idxmin()
    # 3-year correlation of monthly returns
    m = s.resample("ME").last()
    corr = {}
    for ref, rm in refs_m.items():
        if ref == sym or (sym in INR_ITEMS and INR_ITEMS[sym][6] == ref):
            continue
        j = pd.concat([m, rm], axis=1).dropna()
        j = j[j.index > last_d - pd.DateOffset(years=3)].pct_change().dropna()
        j = j.replace([np.inf, -np.inf], np.nan).dropna()
        if len(j) >= 24:
            corr[ref] = round(float(j.iloc[:, 0].corr(j.iloc[:, 1])), 2)
    stats = {"pct10": r1(pct10), "pct_all": r1(pct_all), "since": s.index[0].year, "z10": round(z10, 2), "med10": r(med10), "sd10": r(sd10),
             "last_seen": last_seen, "run_start": run_start.strftime("%Y-%m-%d") if run_start is not None else None,
             "prior": prior.strftime("%Y-%m-%d") if prior is not None else None, "side": "high" if high_side else "low", "episodes": ep, "ytd": ytd_info,
             "ath": r(ath), "ath_date": ath_d.strftime("%Y-%m-%d"), "drawdown": r1(dd), "corr3y": corr}

    # ---- sentences
    px = money(last, pre, suf)
    out = []
    zt = f"{z10:+.1f} standard deviations from" if abs(z10) >= 0.05 else "right at"
    out.append(f"{short} at {px} is in the {ordinal(pct10)} percentile of the last 10 years, {zt} the 10-year median of {money(med10, pre, suf)}.")
    word = "this high" if high_side else "this low"
    if run_start is not None and (last_d - run_start).days > 120:
        if prior is not None:
            yrs = (run_start - prior).days / 365.25
            gap = f", {int(yrs)} years earlier" if yrs >= 2 else ""
            out.append(f"It has traded at or {'above' if high_side else 'below'} this level on and off since {mon(run_start)}; before that, the last time it was {word} was {mon(prior)}{gap}.")
        else:
            out.append(f"It first reached this level in {mon(run_start)}; before that it had never been {word} in data going back to {s.index[0].year}.")
    elif last_seen:
        ls = pd.Timestamp(last_seen)
        if (last_d - ls).days > 365 * 2:
            out.append(f"The last time it was {word} was {mon(ls)}, {int((last_d - ls).days / 365.25)} years ago.")
        else:
            out.append(f"The last time it was {word} was {mon(ls)}.")
    else:
        out.append(f"It has not been {'this high' if high_side else 'this low'} at any point since the data begins in {s.index[0].year}." if high_side or (last_d - atl_d).days < 120 else
                   f"It is near the low end of its range since {s.index[0].year}.")
    e12, e3 = ep.get("m12"), ep.get("m3")
    if e12 and e12["n"] >= 3:
        lower = e12["fell"]
        tail = f"was lower 12 months later {lower} {'time' if lower == 1 else 'times'} (median {sgn(e12['med'])}, average {sgn(e12['avg'])})" if lower * 2 >= e12["n"] else \
            f"was higher 12 months later {e12['n'] - lower} {'time' if e12['n'] - lower == 1 else 'times'} (median {sgn(e12['med'])}, average {sgn(e12['avg'])})"
        s3 = f"; three months on, the median move was {sgn(e3['med'])}" if e3 else ""
        out.append(f"In {e12['n']} past episodes within 5% of today's level, it {tail}{s3}.")
    elif e12:
        lead = "in the single past episode" if e12["n"] == 1 else f"in the only {e12['n']} past episodes"
        out.append(f"History is thin at this price: {lead} within 5% of today's level, the move 12 months later was {' and '.join(sgn(v) for v in fwd12(s, ep))}.")
    elif ep["n"] == 0:
        out.append(f"It never traded within 5% of today's level before the last 12 months, so history offers no precedent at this price.")
    if ytd_info:
        yv = ytd_info["ytd"]
        n = ytd_info["years"]
        if ytd_info["rank"] == 1 and yv > 0:
            out.append(f"Year to date it is {sgn(yv, 1)}, the strongest start to a year in the {n}-year record.")
        elif ytd_info["rank"] == n and yv < 0:
            out.append(f"Year to date it is {sgn(yv, 1)}, the weakest start to a year in the {n}-year record.")
        else:
            since, rk = ytd_info["since"], ytd_info["rank"]
            top = rk <= max(2, n // 4) if yv >= 0 else rk > n - max(2, n // 4)
            if since and last_d.year - since >= 3 and top:
                out.append(f"Year to date it is {sgn(yv, 1)}, the {'best' if yv >= 0 else 'worst'} performance over this calendar window since {since} ({ordinal(rk) + ' best' if yv >= 0 else ordinal(n - rk + 1) + ' weakest'} of {n} years).")
            else:
                out.append(f"Year to date it is {sgn(yv, 1)}, which ranks {ordinal(ytd_info['rank'])} of {n} years over the same calendar window (1st is the strongest).")
    if dd > -2:
        out.append(f"It is within 2% of its all-time high of {money(ath, pre, suf)} set in {mon(ath_d)}." if dd < -0.05 else f"It is at an all-time high (data since {s.index[0].year}).")
    else:
        out.append(f"It sits {abs(dd):.0f}% below its all-time high of {money(ath, pre, suf)} from {mon(ath_d)}.")
    if corr:
        best = sorted(corr.items(), key=lambda kv: -abs(kv[1]))
        k1, v1 = best[0]
        msg = f"Over the past 3 years its monthly moves tracked {REFS[k1]} most closely (correlation {v1:+.2f})"
        neg = [(k, v) for k, v in best if v <= -0.3 and k != k1]
        if v1 < 0:
            msg = f"Over the past 3 years its monthly moves ran most strongly against {REFS[k1]} (correlation {v1:+.2f})"
        elif neg:
            msg += f" and moved against {REFS[neg[0][0]]} ({neg[0][1]:+.2f})"
        if abs(v1) < 0.3:
            msg = f"Over the past 3 years its monthly moves had little link to gold, oil, copper, the dollar or equities (strongest correlation {v1:+.2f} with {REFS[k1]})"
        out.append(msg + ".")
    return stats, out[:7]


def series_of(px, sym):
    if sym not in px:
        return None
    s = px[sym].dropna()
    s = s[s.index.dayofweek < 5]
    return s if len(s) else None


def quality_ok(s, today):
    """Reject dead or frozen Yahoo contracts: stale last print or a long run of unchanged closes."""
    if s is None or len(s) < 500:
        return False, "too little history"
    if (today - s.index[-1]).days > 21:
        return False, f"stale since {s.index[-1].date()}"
    tail = s.iloc[-500:]
    flat = float((tail.diff().iloc[1:] == 0).mean())
    if flat > 0.5:  # thin contracts such as HRC settle unchanged on ~30% of days; frozen feeds are far higher
        return False, f"{flat:.0%} unchanged closes in last 500 sessions"
    return True, ""


def fetch_commodities():
    log("Commodities (Yahoo Finance)")
    tick = list(ITEMS) + ["USDINR=X"] + [k for k in REFS if k not in ITEMS]
    px = retry(lambda: yf.download(tick, period="max", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"])
    for t in tick:
        if t not in px or px[t].dropna().empty:
            try:
                one = yf.download(t, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
                px[t] = one.squeeze() if hasattr(one, "squeeze") else one
            except Exception as e:  # noqa: BLE001
                log(f"  ! retry {t}: {e}")
    px.index = pd.to_datetime(px.index).tz_localize(None) if getattr(px.index, "tz", None) else pd.to_datetime(px.index)
    today = pd.Timestamp.today().normalize()

    data, dropped = {}, []
    for sym in ITEMS:
        s = series_of(px, sym)
        ok, why = quality_ok(s, today)
        if ok:
            data[sym] = s
        else:
            dropped.append({"sym": sym, "why": why})
            log(f"  ! drop {sym}: {why}")
    fx = series_of(px, "USDINR=X")
    if fx is not None:
        for k, meta in INR_ITEMS.items():
            base = data.get(meta[6])
            if base is None:
                continue
            j = pd.concat([base, fx.reindex(base.index.union(fx.index)).ffill(limit=5).reindex(base.index)], axis=1).dropna()
            data[k] = (j.iloc[:, 0] * j.iloc[:, 1] * meta[7]).rename(k)

    refs_m = {}
    for ref in REFS:
        s = data.get(ref) if ref in data else series_of(px, ref)
        if s is not None:
            refs_m[ref] = s.resample("ME").last()

    items = []
    order = list(ITEMS) + list(INR_ITEMS)
    for sym in order:
        if sym not in data:
            continue
        s = data[sym]
        meta = ITEMS.get(sym) or INR_ITEMS[sym]
        last_d = s.index[-1]
        y52 = s[s.index > last_d - pd.Timedelta(days=365)]
        lo, hi = float(y52.min()), float(y52.max())
        try:
            stats, ins = analyse(sym, s, refs_m)
        except Exception as e:  # noqa: BLE001
            log(f"  ! stats {sym}: {e}")
            stats, ins = {}, []
        prev_y = s[s.index.year < last_d.year]
        wk = s[s.index > last_d - pd.DateOffset(years=10)].resample("W-FRI").last().dropna()
        items.append({
            "sym": sym, "name": meta[0], "short": meta[1], "group": meta[2], "unit": meta[3],
            "proxy": sym in ("LIT", "URA"), "derived": sym in INR_ITEMS,
            "last": r(s.iloc[-1]), "last_date": last_d.strftime("%Y-%m-%d"), "since": s.index[0].strftime("%Y-%m-%d"),
            "changes": {
                "d1": r1((s.iloc[-1] / s.iloc[-2] - 1) * 100) if len(s) > 1 and s.iloc[-2] > 0 else None,
                "w1": r1(change(s, last_d, pd.Timedelta(days=7))),
                "m1": r1(change(s, last_d, pd.DateOffset(months=1))),
                "m3": r1(change(s, last_d, pd.DateOffset(months=3))),
                "ytd": r1((s.iloc[-1] / prev_y.iloc[-1] - 1) * 100) if len(prev_y) and prev_y.iloc[-1] > 0 else None,
                "y1": r1(change(s, last_d, pd.DateOffset(years=1))),
                "y5": r1(change(s, last_d, pd.DateOffset(years=5))),
            },
            "lo52": r(lo), "hi52": r(hi), "pos52": r1((s.iloc[-1] - lo) / (hi - lo) * 100) if hi > lo else 50.0,
            "med10": stats.get("med10"), "sd10": stats.get("sd10"),
            "daily": pts(s.iloc[-400:]), "weekly": pts(wk), "monthly": pts(s.resample("ME").last().dropna()),
            "stats": stats, "insights": ins, "india": INDIA.get(sym, ""),
        })
    log(f"  commodities: {len(items)} kept, {len(dropped)} dropped")
    return {"generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "groups": GROUPS, "items": items, "dropped": dropped,
            "source": "Yahoo Finance (front-month futures; LIT and URA are ETF proxies; rupee prices converted at USDINR=X)"}


if __name__ == "__main__":
    import json
    d = fetch_commodities()
    txt = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
    print(len(txt), "bytes")
    for it in d["items"]:
        print(it["sym"], it["last"], it["last_date"], it["changes"])
