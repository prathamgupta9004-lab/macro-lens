"""Macro Lens weekly fetcher.

Pulls Indian and global macro data from official or reliable free sources and
writes data/macro.json, which build.py turns into the dashboard.

Sources
  FRED (St. Louis Fed)  US growth, inflation, liquidity, rates, credit, oil stocks
  IMF IRFCL             central bank reserves and gold holdings by country
  MOSPI (Govt of India) CPI (base 2024), IIP, WPI, quarterly GDP
  NSE                   FII / DII cash flows, Nifty 500 constituents
  Yahoo Finance         prices: indices, currencies, commodities, ETFs, stocks
  RBI website           policy repo rate

Run:  python fetch.py          (about 3 to 5 minutes)
"""
import io
import json
import re
import ssl
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TODAY = pd.Timestamp.today().normalize()
LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def retry(fn, tries=3, wait=3):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))


def to_points(s, digits=4):
    """Series -> [[YYYY-MM-DD, value], ...] with NaNs dropped."""
    s = s.dropna()
    return [[d.strftime("%Y-%m-%d"), round(float(v), digits)] for d, v in s.items()]


def weekly(s, rule="W-FRI"):
    """Week-end resample that never stamps a date later than the last real observation."""
    s = s.dropna()
    if s.empty:
        return s
    w = s.resample(rule).last()
    last = s.index[-1]
    w.index = w.index.where(w.index <= last, last)
    return w


SERIES = {}
EXTRA = {}  # intermediate series shared between fetchers (e.g. India nominal GDP level)


def add(sid, s, name, unit, source, group, freq, digits=3, note=""):
    s = s.dropna()
    if s.empty:
        log(f"  ! {sid}: empty, skipped")
        return
    if freq in ("weekly", "daily"):
        # full history, but anything older than 10 years is kept at month-end to keep the page light
        cut = TODAY - pd.DateOffset(years=10)
        s = pd.concat([s[s.index < cut].resample("ME").last().dropna(), s[s.index >= cut]])
    SERIES[sid] = {"name": name, "unit": unit, "source": source, "group": group,
                   "freq": freq, "note": note, "data": to_points(s, digits)}


# --------------------------------------------------------------------------- FRED
FRED = [
    # id, name, unit, group, transform, start
    ("GDP", "US nominal GDP growth", "% YoY", "us", "yoy4", "2000"),
    ("GDPC1", "US real GDP growth", "% YoY", "us", "yoy4", "2000"),
    ("CPIAUCSL", "US CPI inflation", "% YoY", "us", "yoy12", "2000"),
    ("CPILFESL", "US core CPI inflation", "% YoY", "us", "yoy12", "2000"),
    ("PCEPILFE", "US core PCE inflation", "% YoY", "us", "yoy12", "2000"),
    ("UNRATE", "US unemployment rate", "%", "us", None, "2000"),
    ("ICSA", "US initial jobless claims", "thousand", "us", "k", "2015"),
    ("PAYEMS", "US non-farm payrolls, monthly change", "thousand", "us", "diff", "2015"),
    ("HOUST", "US housing starts", "thousand, SAAR", "us", None, "2000"),
    ("INDPRO", "US industrial production", "% YoY", "us", "yoy12", "2000"),
    ("DRCLACBS", "US consumer loan delinquency rate", "%", "us", None, "2000"),
    ("DRCCLACBS", "US credit card delinquency rate", "%", "us", None, "2000"),
    ("GFDEGDQ188S", "US federal debt to GDP", "% of GDP", "us", None, "1990"),
    ("FYFSGDA188S", "US federal surplus / deficit", "% of GDP", "us", None, "1990"),
    ("VAPGDPMA", "US manufacturing value added", "% of GDP", "us", "share_ma", "1997"),
    ("M2SL", "US M2 money supply", "% YoY", "liq", "yoy12", "2000"),
    ("WALCL", "Fed balance sheet", "USD bn", "liq", "m2b", "2008"),
    ("WTREGEN", "US Treasury General Account", "USD bn", "liq", "m2b", "2008"),
    ("RRPONTSYD", "Fed overnight reverse repo", "USD bn", "liq", None, "2013"),
    ("DFF", "Fed funds effective rate", "%", "rates", None, "2000"),
    ("DGS2", "US 2Y Treasury yield", "%", "rates", None, "2000"),
    ("DGS10", "US 10Y Treasury yield", "%", "rates", None, "2000"),
    ("DGS30", "US 30Y Treasury yield", "%", "rates", None, "2000"),
    ("T10Y2Y", "US 10Y minus 2Y curve", "% pts", "rates", None, "2000"),
    ("DFII10", "US 10Y real yield (TIPS)", "%", "rates", None, "2003"),
    ("T10YIE", "US 10Y breakeven inflation", "%", "rates", None, "2003"),
    ("MORTGAGE30US", "US 30Y mortgage rate", "%", "rates", None, "2000"),
    ("BAMLH0A0HYM2", "US high-yield credit spread", "% pts", "credit", None, "2000"),
    ("BAMLC0A0CM", "US investment-grade credit spread", "% pts", "credit", None, "2000"),
    ("BAMLH0A3HYC", "US CCC-and-below credit spread", "% pts", "credit", None, "2000"),
    ("BAMLEMCBPIOAS", "Emerging market corporate spread", "% pts", "credit", None, "2000"),
    ("IRLTLT01FRM156N", "France 10Y yield", "%", "rates", None, "2000"),
    ("IRLTLT01DEM156N", "Germany 10Y yield", "%", "rates", None, "2000"),
    ("IRLTLT01JPM156N", "Japan 10Y yield", "%", "rates", None, "2000"),
    ("IRLTLT01GBM156N", "UK 10Y yield", "%", "rates", None, "2000"),
    ("INDIRLTLT01STM", "India 10Y yield", "%", "india", None, "2005"),
    ("DTWEXBGS", "Broad US dollar index (Fed)", "index", "fx", None, "2006"),
    ("DCOILBRENTEU", "Brent crude (EIA spot)", "USD/bbl", "energy", None, "2000"),
    ("DCOILWTICO", "WTI crude (EIA spot)", "USD/bbl", "energy", None, "2000"),
    ("DEXINUS", "USD/INR (Fed H.10)", "INR per USD", "fx", None, "1973"),
    ("BAA10Y", "Moody's Baa corporate yield minus 10Y Treasury", "% pts", "credit", None, "1986"),
    ("AAA10Y", "Moody's Aaa corporate yield minus 10Y Treasury", "% pts", "credit", None, "1986"),
]


def fred(sid, start):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}-01-01"
    # FRED drops connections that send a browser user agent, so send none.
    r = retry(lambda: requests.get(url, timeout=60))
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "v"]
    df["date"] = pd.to_datetime(df["date"])
    return pd.to_numeric(df.set_index("date")["v"], errors="coerce").dropna()


def fetch_fred():
    log("FRED")
    raw = {}
    for sid, name, unit, group, tf, start in FRED:
        try:
            s = fred(sid, "1900")  # full history
        except Exception as e:  # noqa: BLE001
            log(f"  ! {sid}: {e}")
            continue
        raw[sid] = s
        if tf == "yoy4":
            s = s.pct_change(4) * 100
        elif tf == "yoy12":
            s = s.pct_change(12) * 100
        elif tf == "diff":
            s = s.diff()
        elif tf == "m2b":
            s = s / 1000
        elif tf == "k":
            s = s / 1000
        elif tf == "k2m":
            s = s / 1000
        freq = "daily" if sid.startswith(("D", "T10", "BAML", "RRP", "BAA", "AAA")) else "periodic"
        # daily series are thinned to weekly (Friday) to keep the page light
        if freq == "daily":
            s = weekly(s)
            freq = "weekly"
        add(sid, s, name, unit, "FRED", group, freq)
    # derived: net liquidity = Fed balance sheet - TGA - RRP (USD bn, weekly)
    try:
        w = weekly(raw["WALCL"], "W-WED") / 1000
        t = weekly(raw["WTREGEN"], "W-WED") / 1000
        r = weekly(raw["RRPONTSYD"], "W-WED")
        nl = (w - t - r.reindex(w.index).fillna(0)).dropna()
        add("NETLIQ", nl, "US net liquidity (Fed assets - TGA - RRP)", "USD bn", "FRED (derived)",
            "liq", "weekly", note="A common proxy for dollar liquidity available to markets.")
    except KeyError:
        pass
    try:
        sp = (raw["IRLTLT01FRM156N"] - raw["IRLTLT01DEM156N"]).dropna()
        add("FRDE", sp, "France minus Germany 10Y spread", "% pts", "FRED (derived)", "rates", "monthly",
            note="France is the third-largest bond market; a widening spread signals euro-area fiscal stress.")
    except KeyError:
        pass
    try:
        s = raw["GDP"]
        add("GDPLVL", s / 1000, "US nominal GDP level", "USD tn, SAAR", "FRED", "us", "quarterly")
    except KeyError:
        pass


def fetch_eia():
    """Weekly US crude stocks straight from EIA's history workbooks (thousand barrels)."""
    log("EIA crude stocks")
    for sid, name in [("WCESTUS1", "US commercial crude stocks"), ("WCSSTUS1", "US Strategic Petroleum Reserve")]:
        try:
            r = retry(lambda: requests.get(f"https://www.eia.gov/dnav/pet/hist_xls/{sid}w.xls", timeout=60))
            df = pd.read_excel(io.BytesIO(r.content), sheet_name="Data 1", skiprows=2)
            s = pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0])).dropna() / 1000
            add(sid, s[s.index >= "2005"], name, "mn barrels", "EIA", "energy", "weekly", digits=1)
        except Exception as e:  # noqa: BLE001
            log(f"  ! {sid}: {e}")


# --------------------------------------------------------------------------- Yahoo
YAHOO = {
    # India
    "^NSEI": ("Nifty 50", "india_mkt"), "^CRSLDX": ("Nifty 500", "india_mkt"),
    "^NSEMDCP50": ("Nifty Midcap 50", "india_mkt"), "^BSESN": ("Sensex", "india_mkt"),
    "^INDIAVIX": ("India VIX", "india_mkt"), "^NSEBANK": ("Nifty Bank", "india_sec"),
    "^CNXIT": ("Nifty IT", "india_sec"), "^CNXPHARMA": ("Nifty Pharma", "india_sec"),
    "^CNXAUTO": ("Nifty Auto", "india_sec"), "^CNXFMCG": ("Nifty FMCG", "india_sec"),
    "^CNXMETAL": ("Nifty Metal", "india_sec"), "^CNXENERGY": ("Nifty Energy", "india_sec"),
    "^CNXREALTY": ("Nifty Realty", "india_sec"), "^CNXINFRA": ("Nifty Infra", "india_sec"),
    "^CNXPSUBANK": ("Nifty PSU Bank", "india_sec"), "^CNXMEDIA": ("Nifty Media", "india_sec"),
    "^CNXFIN": ("Nifty Financial Services", "india_sec"), "^CNXPSE": ("Nifty PSE", "india_sec"),
    "^CNXCONSUM": ("Nifty Consumption", "india_sec"), "^CNXCMDT": ("Nifty Commodities", "india_sec"),
    # Global equity
    "^GSPC": ("S&P 500", "global_mkt"), "^IXIC": ("Nasdaq Composite", "global_mkt"),
    "^RUT": ("Russell 2000", "global_mkt"), "ACWI": ("MSCI ACWI (ETF)", "global_mkt"),
    "EEM": ("MSCI Emerging Markets (ETF)", "global_mkt"), "RSP": ("S&P 500 equal weight (ETF)", "global_mkt"),
    "^STOXX50E": ("Euro Stoxx 50", "global_mkt"), "^N225": ("Nikkei 225", "global_mkt"),
    "^HSI": ("Hang Seng", "global_mkt"), "000001.SS": ("Shanghai Composite", "global_mkt"),
    "^KS11": ("KOSPI", "global_mkt"), "^BVSP": ("Brazil Bovespa", "global_mkt"),
    "EWZ": ("Brazil (ETF, USD)", "latam"), "ECH": ("Chile (ETF, USD)", "latam"),
    "EPU": ("Peru (ETF, USD)", "latam"), "ARGT": ("Argentina (ETF, USD)", "latam"),
    "EWW": ("Mexico (ETF, USD)", "latam"), "INDA": ("India (ETF, USD)", "latam"),
    # AI complex
    "SMH": ("Semiconductors (SMH)", "ai"), "NVDA": ("Nvidia", "ai"), "AAPL": ("Apple", "ai"),
    "MSFT": ("Microsoft", "ai"), "GOOGL": ("Alphabet", "ai"), "AMZN": ("Amazon", "ai"),
    "META": ("Meta", "ai"), "ORCL": ("Oracle", "ai"), "CRWV": ("CoreWeave", "ai"),
    # Volatility
    "^VIX": ("VIX (S&P 500 volatility)", "vol"), "^MOVE": ("MOVE (bond volatility)", "vol"),
    # FX
    "DX-Y.NYB": ("US Dollar Index (DXY)", "fx"), "USDINR=X": ("USD/INR", "fx"),
    "USDCNY=X": ("USD/CNY", "fx"), "USDJPY=X": ("USD/JPY", "fx"), "EURUSD=X": ("EUR/USD", "fx"),
    "USDKRW=X": ("USD/KRW", "fx"),
    "USDBRL=X": ("USD/BRL", "fx"), "GBPUSD=X": ("GBP/USD", "fx"),
    # Commodities
    "BZ=F": ("Brent crude", "cmdty"), "CL=F": ("WTI crude", "cmdty"), "NG=F": ("US natural gas", "cmdty"),
    "GC=F": ("Gold", "cmdty"), "SI=F": ("Silver", "cmdty"), "HG=F": ("Copper", "cmdty"),
    "ZW=F": ("Wheat", "cmdty"), "URA": ("Uranium miners (ETF)", "cmdty"), "BTC-USD": ("Bitcoin", "cmdty"),
    "^TNX": ("US 10Y yield (market)", "rates"),
}


def fetch_yahoo():
    log("Yahoo Finance")
    tick = list(YAHOO)
    px = retry(lambda: yf.download(tick, period="max", interval="1d", auto_adjust=True,
                                   progress=False, threads=True)["Close"])
    missing = [t for t in tick if t not in px or px[t].dropna().empty]
    for t in missing:  # Yahoo sometimes drops tickers in a bulk call; retry them one by one
        try:
            one = yf.download(t, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
            px[t] = one.squeeze() if hasattr(one, "squeeze") else one
        except Exception as e:  # noqa: BLE001
            log(f"  ! retry {t}: {e}")
    px = px[px.index.dayofweek < 5]  # weekend FX ticks would pad equity series with a Sunday point
    daily = px.ffill(limit=5)
    for t in tick:
        if t not in daily or daily[t].dropna().empty:
            log(f"  ! {t}: no data")
            continue
        name, group = YAHOO[t]
        s = weekly(daily[t])
        unit = "%" if t == "^TNX" else ("index" if t.startswith("^") else "price")
        add("Y:" + t, s, name, unit, "Yahoo Finance", group, "weekly", digits=4)
        # keep last ~400 sessions of daily data for return calculations on the snapshot tab
        SERIES["Y:" + t]["daily"] = to_points(daily[t].dropna().iloc[-400:], 4)
    # rupee crosses derived from USD pairs (Yahoo's direct CNY/INR quote is unreliable)
    for sid, name, num, den, k in [("Y:CNYINR=X", "CNY/INR", "USDINR=X", "USDCNY=X", 1),
                                   ("Y:JPYINR=X", "JPY/INR (per 100 yen)", "USDINR=X", "USDJPY=X", 100)]:
        if num in daily and den in daily:
            x = daily[num] / daily[den] * k
            add(sid, weekly(x), name, "price", "Yahoo Finance (derived)", "fx", "weekly", digits=4)
            SERIES[sid]["daily"] = to_points(x.dropna().iloc[-400:], 4)
    return daily


# --------------------------------------------------------------------------- IMF reserves
GOLD_COUNTRIES = {
    "USA": "United States", "DEU": "Germany", "ITA": "Italy", "FRA": "France", "RUS": "Russia",
    "CHN": "China", "CHE": "Switzerland", "JPN": "Japan", "IND": "India", "NLD": "Netherlands",
    "TUR": "Turkiye", "POL": "Poland", "TWN": "Taiwan", "PRT": "Portugal", "UZB": "Uzbekistan",
    "SAU": "Saudi Arabia", "GBR": "United Kingdom", "KAZ": "Kazakhstan", "ESP": "Spain",
    "AUT": "Austria", "THA": "Thailand", "SGP": "Singapore", "BEL": "Belgium", "PHL": "Philippines",
    "BRA": "Brazil", "EGY": "Egypt", "IRQ": "Iraq", "CZE": "Czechia", "HUN": "Hungary",
    "QAT": "Qatar", "KOR": "Korea", "MEX": "Mexico", "SWE": "Sweden", "ZAF": "South Africa",
    "ARE": "UAE", "IDN": "Indonesia", "MYS": "Malaysia",
}
TROY_OZ_PER_TONNE = 32150.7466


def fetch_imf(gold_price):
    log("IMF reserves")
    start = "1995-01"
    keys = "+".join(GOLD_COUNTRIES)
    ind = "IRFCLDT1_IRFCL56V_FTO+IRFCLDT1_IRFCL56_USD+IRFCLDT1_IRFCL65_USD"
    url = (f"https://api.imf.org/external/sdmx/2.1/data/IMF.STA,IRFCL/{keys}.{ind}.S1XS1311.M"
           f"?startPeriod={start}")
    r = retry(lambda: requests.get(url, timeout=180))
    r.raise_for_status()
    rows = []
    for m in re.finditer(r"<Series ([^>]+)>(.*?)</Series>", r.text, re.S):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        for p, v in re.findall(r'TIME_PERIOD="([^"]+)" OBS_VALUE="([^"]+)"', m.group(2)):
            rows.append((a["COUNTRY"], a["INDICATOR"], pd.Period(p.replace("-M", "-"), "M").to_timestamp(), float(v)))
    df = pd.DataFrame(rows, columns=["c", "ind", "date", "v"])
    piv = df.pivot_table(index=["c", "date"], columns="ind", values="v").reset_index()
    piv = piv.rename(columns={"IRFCLDT1_IRFCL56V_FTO": "oz", "IRFCLDT1_IRFCL56_USD": "gold_book",
                              "IRFCLDT1_IRFCL65_USD": "total"})
    # IRFCL starts around 2015; IMF's International Liquidity dataset has gold ounces back to 1980
    try:
        url = f"https://api.imf.org/external/sdmx/2.1/data/IMF.STA,IL/{keys}.RGV_REVS.FTO.M?startPeriod=1980"
        r = retry(lambda: requests.get(url, timeout=240))
        old = []
        for m in re.finditer(r"<Series ([^>]+)>(.*?)</Series>", r.text, re.S):
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            for p, v in re.findall(r'TIME_PERIOD="([^"]+)" OBS_VALUE="([^"]+)"', m.group(2)):
                old.append((a["COUNTRY"], pd.Period(p.replace("-M", "-"), "M").to_timestamp(), float(v)))
        old = pd.DataFrame(old, columns=["c", "date", "oz"])
        first = piv.groupby("c")["date"].min()
        old = old[old["date"] < old["c"].map(first).fillna(pd.Timestamp.max)]
        piv = pd.concat([old, piv], ignore_index=True)
        log(f"  IL gold history: {len(old)} older monthly points")
    except Exception as e:  # noqa: BLE001
        log(f"  ! IL gold history: {e}")
    table, hist = [], {}
    for c, g in piv.groupby("c"):
        g = g.sort_values("date").dropna(subset=["oz"])
        g = g[g["oz"] > 0].copy()
        if g.empty:
            continue
        # some countries switch reporting scale mid-series (e.g. ounces vs thousand ounces): snap jumps of ~1000x back
        oz, prev = [], None
        for v in g["oz"]:
            if prev:
                for k in (1e3, 1e6):
                    if v / prev > k / 5:
                        v /= k
                    elif v / prev < 5 / k:
                        v *= k
            oz.append(v)
            prev = v
        g["oz"] = oz
        g["t"] = g["oz"] / TROY_OZ_PER_TONNE
        last = g.iloc[-1]
        def ago(months):
            past = g[g["date"] <= last["date"] - pd.DateOffset(months=months)]
            return float(past.iloc[-1]["t"]) if len(past) else None
        t12, t60 = ago(12), ago(60)
        gold_mkt = last["oz"] * gold_price / 1e9 if gold_price else None
        total = last.get("total")
        share = None
        if gold_mkt and pd.notna(total) and pd.notna(last.get("gold_book")):
            adj_total = total / 1e9 - last["gold_book"] / 1e9 + gold_mkt
            share = gold_mkt / adj_total * 100 if adj_total > 0 else None
        table.append({
            "code": c, "country": GOLD_COUNTRIES.get(c, c), "asof": last["date"].strftime("%b %Y"),
            "tonnes": round(float(last["t"]), 1),
            "chg12m": round(float(last["t"] - t12), 1) if t12 is not None else None,
            "chg5y": round(float(last["t"] - t60), 1) if t60 is not None else None,
            "gold_value_bn": round(float(gold_mkt), 1) if gold_mkt else None,
            "reserves_bn": round(float(total / 1e9), 1) if pd.notna(total) else None,
            "gold_share": round(float(share), 1) if share else None,
        })
        hist[c] = to_points(g.set_index("date")["t"], 1)
        if c == "IND" and g["total"].notna().any():
            add("IND_RES", g.set_index("date")["total"] / 1e9, "India total reserves (IMF template)",
                "USD bn", "IMF IRFCL", "india", "monthly", digits=1)
    table.sort(key=lambda x: -x["tonnes"])
    log(f"  gold table: {len(table)} countries")
    return {"table": table, "history": hist}


# --------------------------------------------------------------------------- MOSPI
class _LegacyTLS(HTTPAdapter):
    # api.mospi.gov.in still needs legacy TLS renegotiation
    def init_poolmanager(self, *a, **k):
        ctx = ssl.create_default_context()
        ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        k["ssl_context"] = ctx
        return super().init_poolmanager(*a, **k)


MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                      "August", "September", "October", "November", "December"], 1)}


def mospi_pages(sess, path, params, max_pages=40):
    out, page = [], 1
    while page <= max_pages:
        p = dict(params, Format="JSON", limit=100, page=page)
        j = retry(lambda: sess.get("https://api.mospi.gov.in" + path, params=p, timeout=60).json(), tries=5, wait=8)
        d = j.get("data") or []
        out += d
        meta = j.get("meta_data") or {}
        if page >= int(meta.get("totalPages", 1) or 1) or not d:
            break
        page += 1
    return out


def splice(*parts):
    """Join series from newest to oldest base year: each older part only fills dates before the newer ones start."""
    out = None
    for p in parts:
        if p is None or p.empty:
            continue
        out = p if out is None else out.combine_first(p[p.index < out.index.min()])
    return out if out is not None else pd.Series(dtype=float)


def fetch_mospi():
    log("MOSPI")
    s = requests.Session()
    s.mount("https://", _LegacyTLS())
    month = lambda r: pd.Timestamp(int(r["year"]), MONTHS[r["month"]], 1)

    # CPI: base 2024 (from Jan 2026) <- base 2012 (2014 to Dec 2025) <- OECD index on FRED (from 1958)
    cpi24 = food24 = cpi12 = oecd = None
    try:
        rows = []
        for div in ("0", "1"):  # 0 = CPI (General), 1 = Food and beverages
            rows += mospi_pages(s, "/api/cpi/getCPIData", {"base_year": "2024", "level": "Group", "series": "Current",
                                                            "state_code": "1", "sector_code": "3", "division_code": div})
        def cpi_series(div):
            pts = {month(r): float(r["inflation"]) for r in rows
                   if r.get("state") == "All India" and r.get("division") == div and r.get("sector") == "Combined"
                   and not r.get("group") and r.get("inflation") not in (None, "")}
            return pd.Series(pts, dtype=float).sort_index()
        cpi24, food24 = cpi_series("CPI (General)"), cpi_series("Food and beverages")
    except Exception as e:  # noqa: BLE001
        log(f"  ! CPI 2024: {e}")
    try:
        rows = mospi_pages(s, "/api/cpi/getCPIIndex", {"base_year": "2012", "series": "Current", "sector_code": "3",
                                                       "group_code": "0", "state_code": "99"}, max_pages=10)
        cpi12 = pd.Series({month(r): float(r["inflation"]) for r in rows
                           if r.get("sector") == "Combined" and r.get("inflation") not in (None, "")
                           and "General" in ((r.get("group") or "") + (r.get("subgroup") or ""))}, dtype=float).sort_index()
    except Exception as e:  # noqa: BLE001
        log(f"  ! CPI 2012: {e}")
    try:
        oecd = fred("INDCPIALLMINMEI", "1957").pct_change(12) * 100
    except Exception as e:  # noqa: BLE001
        log(f"  ! OECD CPI: {e}")
    add("IN_CPI", splice(cpi24, cpi12, oecd), "India CPI inflation", "% YoY", "MOSPI, OECD via FRED", "india",
        "monthly", digits=2, note="Base 2024 from 2026, base 2012 from 2014 to 2025, OECD index before that.")
    if food24 is not None:
        add("IN_CPI_FOOD", food24, "India CPI: food and beverages (base 2024)", "% YoY", "MOSPI", "india", "monthly", 2)

    # IIP: base 2022-23 <- base 2011-12 (from 2013)
    try:
        def iip_rows(base):
            return mospi_pages(s, "/api/iip/getIipData", {"base_year": base, "frequency": "Monthly", "type": "All"},
                               max_pages=80)
        new, old = iip_rows("2022-23"), iip_rows("2011-12")
        def iip(rows, cat):
            return pd.Series({month(r): float(r["growth_rate"]) for r in rows
                              if r.get("category") == cat and not r.get("sub_category")
                              and r.get("growth_rate") not in (None, "")}, dtype=float).sort_index()
        both = lambda cat: splice(iip(new, cat), iip(old, cat))
        add("IN_IIP", both("General"), "India IIP growth", "% YoY", "MOSPI", "india", "monthly", digits=1)
        add("IN_IIP_MFG", both("Manufacturing"), "India IIP manufacturing growth", "% YoY", "MOSPI", "india",
            "monthly", digits=1)
        for cat, nm in [("Capital Goods", "capital goods"), ("Infrastructure/ Construction Goods", "infra / construction goods"),
                        ("Consumer Durables", "consumer durables"), ("Consumer Non-durables", "consumer non-durables")]:
            add("IN_IIP_" + re.sub(r"\W", "", cat)[:10].upper(), both(cat), f"India IIP {nm}", "% YoY", "MOSPI",
                "india", "monthly", digits=1)
    except Exception as e:  # noqa: BLE001
        log(f"  ! IIP: {e}")

    # WPI headline: base 2022-23 <- base 2011-12 (from 2013)
    try:
        def wpi(base):
            rows = mospi_pages(s, "/api/wpi/getWpiRecords", {"base_year": base, "major_group_code": "1000000000"},
                               max_pages=20)
            idx = pd.Series({month(r): float(r["index_value"]) for r in rows
                             if (r.get("major_group") or r.get("majorgroup") or "").lower() == "wholesale price index"
                             and not r.get("group") and r.get("index_value")}, dtype=float).sort_index()
            return idx.pct_change(12) * 100
        add("IN_WPI", splice(wpi("2022-23"), wpi("2011-12")), "India WPI inflation", "% YoY", "MOSPI", "india",
            "monthly", digits=2)
    except Exception as e:  # noqa: BLE001
        log(f"  ! WPI: {e}")

    # Quarterly GDP and components: base 2022-23 <- base 2011-12 (from FY13) <- OECD real growth (from 2005)
    try:
        def nas(code, base):
            rows = mospi_pages(s, "/api/nas/getNASData", {"base_year": base, "series": "Current",
                                                          "frequency_code": "Quarterly", "indicator_code": str(code)})
            cur, con, name = {}, {}, ""
            for r in rows:
                if r.get("industry") or r.get("subindustry") or r.get("institutional_sector"):
                    continue
                name = r.get("indicator") or name
                fy = int(r["year"][:4])
                q = int(r["quarter"][1])
                d = pd.Timestamp(fy + (1 if q == 4 else 0), {1: 6, 2: 9, 3: 12, 4: 3}[q], 1) + pd.offsets.MonthEnd(0)
                if r.get("current_price"):
                    cur[d] = float(r["current_price"])
                if r.get("constant_price"):
                    con[d] = float(r["constant_price"])
            return pd.Series(cur, dtype=float).sort_index(), pd.Series(con, dtype=float).sort_index(), name
        g = lambda x: x.pct_change(4) * 100
        cur, con, _ = nas(5, "2022-23")
        cur0, con0, _ = nas(5, "2011-12")
        try:
            oecd = fred("INDGDPRQPSMEI", "2004")
            oecd.index = oecd.index + pd.offsets.QuarterEnd(0)
        except Exception:  # noqa: BLE001
            oecd = None
        nom, real = splice(g(cur), g(cur0)), splice(g(con), g(con0), oecd)
        both = cur.index.intersection(cur0.index)
        scale = (cur[both] / cur0[both]).mean() if len(both) else 1.0
        EXTRA["in_gdp_q"] = splice(cur, cur0 * scale)  # quarterly nominal GDP, INR crore, old base rescaled
        add("IN_GDP_NOM", nom, "India nominal GDP growth", "% YoY", "MOSPI", "india", "quarterly", 1,
            note="Base 2022-23 from FY24, base 2011-12 before.")
        add("IN_GDP_REAL", real, "India real GDP growth", "% YoY", "MOSPI, OECD via FRED", "india", "quarterly", 1)
        add("IN_DEFL", (nom - real).dropna(), "India GDP deflator (nominal minus real)", "% pts", "MOSPI (derived)",
            "india", "quarterly", 1)
        for code, nm, sid, key in [(9, "Gross fixed capital formation", "IN_GFCF", "Fixed Capital"),
                                   (10, "Private consumption", "IN_PFCE", "Private Final"),
                                   (11, "Government consumption", "IN_GFCE", "Government Final")]:
            c2, k2, _ = nas(code, "2022-23")
            c1, k1, n1 = nas(code, "2011-12")
            if key.lower() not in n1.lower():  # older base numbers its indicators differently
                c1 = k1 = None
            add(sid, splice(g(k2), g(k1) if k1 is not None else None), f"India {nm.lower()} growth (real)", "% YoY",
                "MOSPI", "india", "quarterly", 1)
            if sid == "IN_GFCF":
                share = splice((c2 / cur * 100).dropna(), (c1 / cur0 * 100).dropna() if c1 is not None else None)
                add("IN_GFCF_SHARE", share, "India investment rate (GFCF / GDP)", "% of GDP", "MOSPI (derived)",
                    "india", "quarterly", 1)
    except Exception as e:  # noqa: BLE001
        log(f"  ! NAS: {e}")


# --------------------------------------------------------------------------- NSE flows
def nse_session():
    s = requests.Session()
    s.headers.update(BROWSER)
    s.get("https://www.nseindia.com", timeout=30)
    return s


def fetch_flows():
    """NSE publishes only the latest day, so every run appends to a local history file."""
    log("NSE FII / DII")
    path = DATA / "fii_dii_history.csv"
    hist = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "category", "buy", "sell", "net"])
    try:
        s = nse_session()
        j = retry(lambda: s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=30).json())
        new = pd.DataFrame([{"date": pd.to_datetime(x["date"], format="%d-%b-%Y").strftime("%Y-%m-%d"),
                             "category": "FII" if "FII" in x["category"] else "DII",
                             "buy": float(x["buyValue"]), "sell": float(x["sellValue"]),
                             "net": float(x["netValue"])} for x in j])
        hist = pd.concat([hist, new]).drop_duplicates(["date", "category"], keep="last").sort_values("date")
        hist.to_csv(path, index=False)
        log(f"  flows history: {hist['date'].nunique()} days")
    except Exception as e:  # noqa: BLE001
        log(f"  ! flows: {e}")
    return hist.to_dict("records")


# --------------------------------------------------------------------------- NSE index history
NSE_INDEX_NAMES = {  # current name -> older names used in NSE's archive files
    "Nifty 50": ["Nifty 50", "CNX Nifty"],
    "Nifty 500": ["Nifty 500", "CNX 500"],
    "Nifty Midcap 100": ["NIFTY Midcap 100", "Nifty Midcap 100", "Nifty Free Float Midcap 100", "CNX Midcap"],
    "Nifty Smallcap 100": ["NIFTY Smallcap 100", "Nifty Smallcap 100", "Nifty Free Float Smallcap 100", "CNX Smallcap"],
    "Nifty Midcap 150": ["Nifty Midcap 150", "NIFTY Midcap 150"],
    "Nifty Smallcap 250": ["Nifty Smallcap 250", "NIFTY Smallcap 250"],
}


def fetch_nse_indices():
    """Month-end closes since 2011 plus every Friday of the last 3 years, from NSE's daily index files.
    Saved in data/nse_index_closes.csv so each run only downloads new dates."""
    log("NSE index history")
    path = DATA / "nse_index_closes.csv"
    hist = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "index", "close"])
    have = set(hist["date"])
    targets = list(pd.date_range("2011-01-01", TODAY, freq="BME")) + list(pd.date_range(TODAY - pd.DateOffset(years=3), TODAY, freq="W-FRI"))
    targets = sorted(set(targets + [TODAY]))
    if have:  # NSE's archive starts in 2013; don't re-request older dates every run
        targets = [d for d in targets if d >= pd.Timestamp(min(have))]
    alias = {old.lower(): new for new, olds in NSE_INDEX_NAMES.items() for old in olds}
    s = requests.Session()
    s.headers.update({**BROWSER, "Referer": "https://www.nseindia.com/"})
    rows, tried = [], 0
    for tgt in targets:
        # walk back over holidays to the last trading day on or before the target
        for back in range(5):
            d = tgt - pd.Timedelta(days=back)
            key = d.strftime("%Y-%m-%d")
            if key in have:
                break
            if d.dayofweek > 4 or d > TODAY:
                continue
            tried += 1
            try:
                r = s.get(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv", timeout=30)
            except Exception:  # noqa: BLE001
                continue
            if not r.ok:
                continue
            x = pd.read_csv(io.StringIO(r.text))
            for _, row in x.iterrows():
                nm = alias.get(str(row.iloc[0]).strip().lower())
                if nm:
                    try:
                        rows.append({"date": key, "index": nm, "close": float(row["Closing Index Value"])})
                    except (ValueError, KeyError):
                        pass
            have.add(key)
            break
    if rows:
        hist = pd.concat([hist, pd.DataFrame(rows)]).drop_duplicates(["date", "index"], keep="last").sort_values(["date", "index"])
        hist.to_csv(path, index=False)
    piv = hist.pivot_table(index="date", columns="index", values="close")
    piv.index = pd.to_datetime(piv.index)
    for nm in NSE_INDEX_NAMES:
        if nm in piv:
            add("NSE:" + nm, piv[nm], nm, "index", "NSE", "india_mkt", "periodic", digits=2)
    log(f"  NSE index closes: {piv.shape[0]} dates, {len(rows)} new rows, {tried} files tried")
    return piv


def fetch_ratios(daily, nse):
    """Relative-value ratios. Nifty / gold uses gold in rupees; size ratios use NSE's own index history."""
    log("Ratios")
    if "^NSEI" in daily and "GC=F" in daily and "USDINR=X" in daily:
        gold_inr = daily["GC=F"] * daily["USDINR=X"] / 31.1035 * 10  # INR per 10 grams
        add("R:NIFTY_GOLD", weekly(daily["^NSEI"] / gold_inr), "Nifty 50 / gold (INR per 10 g)", "ratio",
            "Yahoo Finance (derived)", "ratios", "weekly", digits=4,
            note="How many 10 g units of gold one Nifty buys. Rising = equities beating gold.")
    if "^BSESN" in daily and "GC=F" in daily and "USDINR=X" in daily:
        gold_inr = daily["GC=F"] * daily["USDINR=X"] / 31.1035 * 10
        add("R:SENSEX_GOLD", weekly(daily["^BSESN"] / gold_inr), "Sensex / gold (INR per 10 g)", "ratio",
            "Yahoo Finance (derived)", "ratios", "weekly", digits=4)
    if nse is not None and "Nifty 50" in nse:
        for nm, sid in [("Nifty Smallcap 100", "R:NIFTY_SMALL"), ("Nifty Midcap 100", "R:NIFTY_MID"),
                        ("Nifty Smallcap 250", "R:NIFTY_SMALL250"), ("Nifty Midcap 150", "R:NIFTY_MID150")]:
            if nm in nse:
                r = (nse["Nifty 50"] / nse[nm]).dropna()
                add(sid, r, f"Nifty 50 / {nm}", "ratio", "NSE (derived)", "ratios", "periodic", digits=4,
                    note="Rising = large caps beating the smaller index.")


# --------------------------------------------------------------------------- banking and credit
def fetch_banking():
    """Bank credit, deposits and broad money as reported by the RBI to the IMF (monetary survey), plus BIS credit/GDP."""
    log("Banking (RBI via IMF, BIS)")
    keys = "ODCORP_A_ACO_PS+ODCORP_L_F22_IBM+ODCORP_L_F29_IBM+DCORP_L_BM"
    url = f"https://api.imf.org/external/sdmx/2.1/data/IMF.STA,MFS_DC/IND.{keys}.XDC.M?startPeriod=2000"
    try:
        r = retry(lambda: requests.get(url, timeout=180))
        ser = {}
        for m in re.finditer(r"<Series ([^>]+)>(.*?)</Series>", r.text, re.S):
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            pts = {pd.Period(p.replace("-M", "-"), "M").to_timestamp(): float(v)
                   for p, v in re.findall(r'TIME_PERIOD="([^"]+)" OBS_VALUE="([^"]+)"', m.group(2))}
            ser[a["INDICATOR"]] = pd.Series(pts).sort_index() / 1e12  # INR lakh crore
        credit = ser["ODCORP_A_ACO_PS"]
        deposits = (ser["ODCORP_L_F22_IBM"] + ser["ODCORP_L_F29_IBM"]).dropna()
        m3 = ser["DCORP_L_BM"]
        src = "RBI data via IMF (monetary survey)"
        add("IN_CREDIT", credit, "Bank credit to the private sector", "INR lakh cr", src, "banking", "monthly", 1)
        add("IN_CREDIT_YOY", credit.pct_change(12) * 100, "Bank credit growth", "% YoY", src, "banking", "monthly", 1,
            note="Jul 2023 includes the HDFC-HDFC Bank merger, which lifts growth for 12 months.")
        add("IN_DEPOSITS", deposits, "Bank deposits", "INR lakh cr", src, "banking", "monthly", 1)
        add("IN_DEPOSITS_YOY", deposits.pct_change(12) * 100, "Bank deposit growth", "% YoY", src, "banking", "monthly", 1)
        add("IN_CD_RATIO", (credit / deposits * 100).dropna(), "Private credit to deposit ratio (monetary survey)", "%", src, "banking", "monthly", 1,
            note="Bank claims on the private sector over all bank deposits. RBI's headline scheduled-bank C-D ratio uses a narrower base and runs higher.")
        add("IN_M3_YOY", m3.pct_change(12) * 100, "Broad money (M3) growth", "% YoY", src, "banking", "monthly", 1)
        gap = (credit.pct_change(12) - deposits.pct_change(12)).dropna() * 100
        add("IN_CREDIT_DEP_GAP", gap, "Credit growth minus deposit growth", "% pts", src, "banking", "monthly", 1)
    except Exception as e:  # noqa: BLE001
        log(f"  ! IMF banking: {e}")
    for sid, nm, cc in [("QINPAM770A", "India credit to private sector", "IN_CREDIT_GDP"),
                        ("QUSPAM770A", "US credit to private sector", "US_CREDIT_GDP"),
                        ("QCNPAM770A", "China credit to private sector", "CN_CREDIT_GDP")]:
        try:
            s = fred(sid, "1900")
            s.index = s.index + pd.offsets.QuarterEnd(0)
            add(cc, s, f"{nm} (% of GDP)", "% of GDP", "BIS via FRED", "banking", "quarterly", 1)
        except Exception as e:  # noqa: BLE001
            log(f"  ! {sid}: {e}")


# --------------------------------------------------------------------------- market cap to GDP
def fetch_mcap_gdp(daily):
    log("Market cap to GDP")
    try:  # US: market value of corporate equities (Fed Z.1) / nominal GDP, the usual "Buffett indicator"
        eq, gdp = fred("NCBEILQ027S", "1900"), fred("GDP", "1900")
        r = (eq / 1000 / gdp * 100).dropna()
        add("US_MCAP_GDP", r, "US equity market value to GDP", "% of GDP", "Fed Z.1 and BEA via FRED", "valuation",
            "quarterly", 1, note="Market value of US non-financial corporate equities over nominal GDP.")
    except Exception as e:  # noqa: BLE001
        log(f"  ! US mcap/GDP: {e}")
    try:  # India: World Bank annual, then a weekly estimate from NSE's total market cap
        j = retry(lambda: requests.get("https://api.worldbank.org/v2/country/IND/indicator/CM.MKT.LCAP.GD.ZS"
                                       "?format=json&per_page=100", timeout=60).json())
        wb = pd.Series({pd.Timestamp(int(z["date"]), 12, 31): z["value"] for z in j[1] if z["value"]}).sort_index()
        wb = wb[~(wb > wb.shift() * 1.6)]  # drops an obviously broken year (2025 shows 267%)
        add("IN_MCAP_GDP_WB", wb, "India market cap to GDP (World Bank, annual)", "% of GDP", "World Bank", "valuation",
            "annual", 1)
        s = nse_session()
        mc = s.get("https://www.nseindia.com/api/marketStatus", timeout=30).json()["marketcap"]
        mcap_now = float(mc["marketCapinCRRupees"])
        EXTRA["nse_mcap_cr"] = mcap_now
        gdp_q = EXTRA.get("in_gdp_q")
        idx = daily.get("^CRSLDX")
        if gdp_q is not None and idx is not None:
            gdp4 = gdp_q.rolling(4).sum().dropna()
            idx = idx.dropna()
            est = mcap_now * idx / idx.iloc[-1]
            est = est[est.index >= est.index[-1] - pd.DateOffset(years=3)]
            g = gdp4.reindex(est.index, method="ffill")
            ratio = weekly((est / g * 100).dropna())
            add("IN_MCAP_GDP", ratio, "India market cap to GDP (estimate)", "% of GDP", "NSE, MOSPI (derived)",
                "valuation", "weekly", 1,
                note=f"NSE total market cap (INR {mcap_now/1e5:,.0f} lakh cr on {mc['timeStamp']}) scaled back with the "
                     "Nifty 500, over trailing four-quarter nominal GDP. Ignores new listings, so earlier points run high.")
    except Exception as e:  # noqa: BLE001
        log(f"  ! India mcap/GDP: {e}")


# --------------------------------------------------------------------------- sector mix and consumption
SECTOR_US = {  # Fama-French 49 industries -> common sector groups
    "Financials": ["Banks", "Insur", "Fin"], "Technology": ["Hardw", "Softw", "Chips", "LabEq", "BusSv"],
    "Healthcare and pharma": ["Hlth", "MedEq", "Drugs"], "Autos": ["Autos"],
    "FMCG and staples": ["Agric", "Food", "Soda", "Beer", "Smoke", "Hshld"],
    "Consumer discretionary": ["Toys", "Fun", "Clths", "Rtail", "Meals", "PerSv", "Txtls"],
    "Oil, gas and coal": ["Oil", "Coal"], "Metals and mining": ["Steel", "Gold", "Mines"],
    "Chemicals and materials": ["Chems", "Rubbr", "BldMt", "Paper", "Boxes"],
    "Capital goods and industrials": ["Mach", "ElcEq", "Aero", "Ships", "Guns", "FabPr"],
    "Transport and services": ["Trans", "Whlsl"], "Power and utilities": ["Util"],
    "Telecom and media": ["Telcm", "Books"], "Real estate and construction": ["RlEst", "Cnstr"], "Other": ["Other"],
}
SECTOR_IN = {  # NSE industry (Nifty 500 list) -> the same groups
    "Financial Services": "Financials", "Information Technology": "Technology", "Healthcare": "Healthcare and pharma",
    "Automobile and Auto Components": "Autos", "Fast Moving Consumer Goods": "FMCG and staples",
    "Consumer Durables": "Consumer discretionary", "Consumer Services": "Consumer discretionary",
    "Textiles": "Consumer discretionary", "Oil Gas & Consumable Fuels": "Oil, gas and coal",
    "Metals & Mining": "Metals and mining", "Chemicals": "Chemicals and materials",
    "Construction Materials": "Chemicals and materials", "Forest Materials": "Chemicals and materials",
    "Capital Goods": "Capital goods and industrials", "Services": "Transport and services", "Power": "Power and utilities",
    "Telecommunication": "Telecom and media", "Media Entertainment & Publication": "Telecom and media",
    "Realty": "Real estate and construction", "Construction": "Real estate and construction", "Diversified": "Other",
}


def fetch_sectors():
    log("Sector mix and consumption")
    out = {"groups": list(SECTOR_US)}
    # US market-cap mix by industry since 1926: number of firms x average firm size (Fama-French 49 industries)
    r = retry(lambda: requests.get("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/49_Industry_Portfolios_CSV.zip",
                                   headers={"User-Agent": "Mozilla/5.0"}, timeout=120))
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    txt = z.read(z.namelist()[0]).decode("latin1").replace("\r", "")

    def block(title):
        i = txt.index(title)
        lines = txt[i:].split("\n")[1:]
        head = [h.strip() for h in lines[0].split(",")]
        rows = []
        for ln in lines[1:]:
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) < 2 or not re.fullmatch(r"\d{6}", parts[0]):
                break
            rows.append(parts)
        df = pd.DataFrame([p[1:] for p in rows], index=[p[0] for p in rows], columns=head[1:]).astype(float)
        df.index = pd.to_datetime(df.index, format="%Y%m")
        return df.where(df > -99)

    n, size = block("Number of Firms in Portfolios"), block("Average Firm Size")
    mcap = (n * size).fillna(0)
    grp = pd.DataFrame({g: mcap[[c for c in cols if c in mcap]].sum(axis=1) for g, cols in SECTOR_US.items()})
    share = grp.div(grp.sum(axis=1), axis=0) * 100
    yearly = share[share.index.month == 12]
    latest = share.iloc[[-1]]
    us = pd.concat([yearly, latest]) if latest.index[0] not in yearly.index else yearly
    out["us"] = {"dates": [d.strftime("%Y-%m") for d in us.index],
                 **{g: [round(float(v), 2) for v in us[g]] for g in us.columns}}
    out["us_latest"] = latest.index[0].strftime("%b %Y")
    log(f"  US industries: {us.index[0]:%Y} to {us.index[-1]:%b %Y}")

    # India today: Nifty 500 market caps grouped the same way
    try:
        lst = pd.read_csv(io.StringIO(retry(lambda: requests.get(
            "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", headers=BROWSER, timeout=60)).text))
        from concurrent.futures import ThreadPoolExecutor

        def cap(sym):
            try:
                return yf.Ticker(sym + ".NS").fast_info["marketCap"]
            except Exception:  # noqa: BLE001
                return None
        with ThreadPoolExecutor(16) as ex:
            lst["mcap"] = list(ex.map(cap, lst["Symbol"].str.strip()))
        lst = lst.dropna(subset=["mcap"])
        lst["group"] = lst["Industry"].map(SECTOR_IN).fillna("Other")
        EXTRA["n500_mcap"] = dict(zip(lst["Symbol"].str.strip() + ".NS", lst["mcap"]))
        ind = lst.groupby("group")["mcap"].sum()
        ind = ind / ind.sum() * 100
        out["india"] = {g: round(float(ind.get(g, 0.0)), 2) for g in SECTOR_US}
        out["india_n"] = int(len(lst))
        out["india_mcap_cr"] = round(float(lst["mcap"].sum()) / 1e7)
        log(f"  India sector mix: {len(lst)} stocks priced")
    except Exception as e:  # noqa: BLE001
        log(f"  ! India sector mix: {e}")

    # real household consumption per head (constant 2015 USD), World Bank; US extended back with BEA real PCE per head
    try:
        j = retry(lambda: requests.get("https://api.worldbank.org/v2/country/IND;USA;CHN;KOR;JPN;BRA/indicator/"
                                       "NE.CON.PRVT.PC.KD?format=json&per_page=1000&date=1960:2030", timeout=60).json())
        wb = pd.DataFrame([(z["country"]["id"], int(z["date"]), z["value"]) for z in j[1] if z["value"]],
                          columns=["c", "y", "v"]).pivot(index="y", columns="c", values="v").sort_index()
        # chain older BEA series onto the World Bank one, each rescaled where they overlap:
        # quarterly real PCE per head (from 1947), then annual real PCE / population (from 1929)
        q = fred("A794RX0Q048SBEA", "1900")
        q = q.groupby(q.index.year).mean()
        a = fred("PCECCA", "1900") / fred("B230RC0A052NBEA", "1900")
        a = a.groupby(a.index.year).mean()
        us = wb["US"].dropna().copy()
        for older in (q, a):
            first = us.index[0]
            us = us.combine_first(older[older.index < first] * (us[first] / older[first]))
        wb = wb.reindex(sorted(set(wb.index) | set(us.index)))
        wb["US"] = us
        wb = wb.sort_index()
        out["consumption"] = {"years": [int(y) for y in wb.index],
                              **{c: [None if pd.isna(v) else round(float(v)) for v in wb[c]] for c in wb.columns}}
        india_now_year = int(wb["IN"].last_valid_index())
        india_now = float(wb["IN"][india_now_year])
        scen = []
        for gr in (4, 5, 6):
            for yrs in (10, 25, 50):
                val = india_now * (1 + gr / 100) ** yrs
                hit = us[us >= val]
                us_year = int(hit.index[0]) if len(hit) else None
                scen.append({"growth": gr, "year": india_now_year + yrs, "value": round(val), "multiple": round(val / india_now, 1),
                             "us_year": us_year, "us_before": us_year == int(us.index[0])})
        out["scenarios"] = scen
        out["india_now"] = {"year": india_now_year, "value": round(india_now)}
        log(f"  consumption: India {india_now:,.0f} in {india_now_year}; US from {int(us.index[0])}")
    except Exception as e:  # noqa: BLE001
        log(f"  ! consumption: {e}")
    return out


NSE_CAT = {  # NSE category-wise turnover labels -> one name per category
    "fpi": "FPI", "mutual funds": "Mutual funds", "insurance companies": "Insurance", "bank": "Banks",
    "aif": "AIFs", "aifs": "AIFs", "pms": "PMS", "retail": "Retail", "others": "Others",
}


def fetch_nse_category():
    """NSE's daily category-wise cash turnover (FPI, MFs, insurers, banks, AIFs, PMS, retail).
    The detailed split exists from 2023; days already saved in data/nse_category_daily.csv are skipped."""
    log("NSE category turnover")
    path = DATA / "nse_category_daily.csv"
    hist = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "category", "buy", "sell"])
    have = set(hist["date"])
    start = pd.Timestamp("2023-01-01") if hist.empty else pd.Timestamp(max(have)) - pd.Timedelta(days=10)
    days = [d for d in pd.bdate_range(start, TODAY) if d.strftime("%Y-%m-%d") not in have]
    s = requests.Session()
    s.headers.update({**BROWSER, "Referer": "https://www.nseindia.com/"})
    rows = []
    for d in days:
        url = f"https://nsearchives.nseindia.com/archives/equities/cat/cat_turnover_{d:%d%m%y}.xls"
        try:
            r = s.get(url, timeout=30)
            if not r.ok:
                continue  # holiday or not yet published
            x = pd.read_excel(io.BytesIO(r.content), header=None)
        except Exception:  # noqa: BLE001
            continue
        for _, row in x.iterrows():
            cat = NSE_CAT.get(str(row[1]).strip().lower())
            if cat and pd.notna(row[2]) and pd.notna(row[3]):
                try:
                    rows.append({"date": d.strftime("%Y-%m-%d"), "category": cat, "buy": float(row[2]), "sell": float(row[3])})
                except ValueError:
                    pass
    if rows:
        hist = pd.concat([hist, pd.DataFrame(rows)]).drop_duplicates(["date", "category"], keep="last").sort_values(["date", "category"])
        hist.to_csv(path, index=False)
    hist = hist[hist["category"] != "Others"]
    log(f"  category history: {hist['date'].nunique()} days ({len(rows)} new rows)")
    hist["net"] = hist["buy"] - hist["sell"]
    hist["month"] = pd.to_datetime(hist["date"]).dt.to_period("M").dt.to_timestamp()
    monthly = hist.pivot_table(index="month", columns="category", values="net", aggfunc="sum").round(0)
    daily = hist[pd.to_datetime(hist["date"]) >= TODAY - pd.Timedelta(days=120)].pivot_table(
        index="date", columns="category", values="net", aggfunc="sum").round(1)
    return {"monthly": {"dates": [d.strftime("%Y-%m-%d") for d in monthly.index],
                        **{c: [None if pd.isna(v) else float(v) for v in monthly[c]] for c in monthly.columns}},
            "daily": {"dates": list(daily.index), **{c: [None if pd.isna(v) else float(v) for v in daily[c]] for c in daily.columns}},
            "first": hist["date"].min()}


def fetch_nsdl_fpi():
    """Monthly FPI net investment (equity, debt, total) from NSDL, the depository's official record, 2002 onwards."""
    log("NSDL FPI monthly")
    url = "https://www.fpi.nsdl.co.in/web/Reports/Yearwise.aspx?RptType=6"
    s = requests.Session()
    s.headers.update(BROWSER)
    r = retry(lambda: s.get(url, timeout=60))
    months = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                          "September", "October", "November", "December"], 1)}

    def parse(html, year):
        # rows are not closed with </tr>, and since 2019 debt is split into General / VRR / FAR columns
        out, debt_cols = [], [1]
        for tr in re.split(r"<tr[^>]*>", html, flags=re.I)[1:]:
            cells = [re.sub(r"<[^>]+>|&nbsp;|\*", " ", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
            if cells and cells[0] == "Equity":  # second header row: first block is FPI, later blocks are MF/AIF
                block = cells[:cells.index("Hybrid")] if "Hybrid" in cells else cells[:2]
                debt_cols = [i for i, c in enumerate(block) if c.startswith("Debt")] or [1]
            if cells and cells[0].split(" ")[0] in months:
                nums = [float(c.replace(",", "")) for c in cells[1:] if re.fullmatch(r"-?[\d,]+(\.\d+)?", c.replace(" ", ""))]
                if len(nums) >= 3:
                    out.append({"month": pd.Timestamp(year, months[cells[0].split(" ")[0]], 1),
                                "equity": nums[0], "debt": sum(nums[i] for i in debt_cols if i < len(nums)), "total": nums[-1]})
        return out

    rows = parse(r.text, TODAY.year)
    form = dict(re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', r.text))
    sel = re.search(r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', r.text, re.S)
    if sel:
        for y in [int(v) for v in re.findall(r'value="(\d{4})"', sel.group(2)) if int(v) < TODAY.year]:
            got = []
            for attempt in range(4):  # the year postback sometimes comes back empty
                try:
                    rr = s.post(url, data={**form, sel.group(1): str(y), "__EVENTTARGET": sel.group(1)}, timeout=60)
                    got = parse(rr.text, y)
                except Exception as e:  # noqa: BLE001
                    log(f"  ! NSDL {y}: {e}")
                if len(got) >= 12:
                    break
                time.sleep(2 * (attempt + 1))
            rows += got
    if not rows:
        log("  ! NSDL: nothing parsed")
        return None
    df = pd.DataFrame(rows).drop_duplicates("month").set_index("month").sort_index()
    for c, nm in [("equity", "FPI net investment: equity"), ("debt", "FPI net investment: debt"), ("total", "FPI net investment: total")]:
        add("FPI_" + c.upper(), df[c], nm, "INR cr", "NSDL", "flows", "monthly", digits=0)
    log(f"  NSDL months: {len(df)} ({df.index.min():%b %Y} to {df.index.max():%b %Y})")
    return True


def fetch_repo():
    try:
        r = requests.get("https://www.rbi.org.in/", headers=BROWSER, timeout=40)
        text = re.sub(r"<[^>]+>", " ", r.text)
        m = re.search(r"Policy Repo Rate\s*:?\s*([\d.]+)\s*%", text)
        if m:
            log(f"  RBI repo: {m.group(1)}%")
            return float(m.group(1))
    except Exception as e:  # noqa: BLE001
        log(f"  ! RBI repo: {e}")
    return None


# --------------------------------------------------------------------------- Nifty 500 breadth
def fetch_breadth():
    log("Nifty 500 breadth (downloads ~500 stocks)")
    r = retry(lambda: requests.get("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
                                   headers=BROWSER, timeout=60))
    lst = pd.read_csv(io.StringIO(r.text))
    lst["t"] = lst["Symbol"].str.strip() + ".NS"
    ind = dict(zip(lst["t"], lst["Industry"]))
    px = retry(lambda: yf.download(list(lst["t"]), start="2005-01-01", interval="1d", auto_adjust=True,
                                   progress=False, threads=True))
    close, vol = px["Close"], px["Volume"]
    close = close.dropna(axis=1, thresh=200).ffill(limit=3)
    close = close[close.index.dayofweek < 5].dropna(how="all")
    n = close.notna().sum(axis=1)
    ema20 = close.ewm(span=20, adjust=False).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    hi52 = close.rolling(252, min_periods=240).max()
    lo52 = close.rolling(252, min_periods=240).min()
    chg = close.pct_change()
    pct = lambda m, base: (m.sum(axis=1) / base.notna().sum(axis=1) * 100)
    out = pd.DataFrame({
        "above20": pct(close > ema20, ema20),
        "above50": pct(close > sma50, sma50),
        "above200": pct(close > sma200, sma200),
        "high52": (close >= hi52).sum(axis=1),
        "low52": (close <= lo52).sum(axis=1),
        "adv": (chg > 0).sum(axis=1), "dec": (chg < 0).sum(axis=1),
        "up4": (chg >= 0.04).sum(axis=1), "dn4": (chg <= -0.04).sum(axis=1),
    })
    turnover = (close * vol.reindex(close.index)[close.columns]).sum(axis=1) / 1e7  # INR crore
    out["turnover_cr"] = turnover
    out = out[(n > 100) & (out.index >= "2006-01-01")]  # need enough of today's constituents trading
    # daily for the last 3 years, weekly (Friday) before that
    cut = out.index[-1] - pd.DateOffset(years=3)
    old = out[out.index < cut]
    out = pd.concat([old[old.index.dayofweek == 4], out[out.index >= cut]])
    # weekly EMA table by industry (like chartink's "sectors above key weekly EMA")
    wk = close.apply(weekly)
    sector_rows = []
    spans = {"4w": 4, "20w": 20, "30w": 30, "40w": 40, "52w": 52}
    above = {k: (wk > wk.ewm(span=v, adjust=False).mean()).iloc[-1] for k, v in spans.items()}
    ret4 = (wk.iloc[-1] / wk.iloc[-5] - 1) * 100
    ret13 = (wk.iloc[-1] / wk.iloc[-14] - 1) * 100
    for sec, cols in pd.Series(ind).groupby(lambda t: ind[t]).groups.items():
        cols = [c for c in cols if c in wk.columns]
        if len(cols) < 3:
            continue
        row = {"sector": sec, "n": len(cols)}
        for k in spans:
            row[k] = round(float(above[k][cols].mean() * 100), 1)
        row["ret4w"] = round(float(ret4[cols].median()), 1)
        row["ret13w"] = round(float(ret13[cols].median()), 1)
        sector_rows.append(row)
    sector_rows.sort(key=lambda x: -x["40w"])
    # weekly % above 40w EMA history for the whole universe
    a40 = ((wk > wk.ewm(span=40, adjust=False).mean()).sum(axis=1) / wk.notna().sum(axis=1) * 100)
    a40 = a40[a40.index >= "2006-01-01"]
    log(f"  breadth: {close.shape[1]} stocks, {len(out)} days")
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in out.index],
        **{c: [None if pd.isna(v) else round(float(v), 2) for v in out[c]] for c in out.columns},
        "sectors": sector_rows,
        "above40w": to_points(a40, 1),
        "universe": int(close.shape[1]),
        "stocks": stock_stats(close, hi52, lo52, ind),
    }


def stock_stats(close, hi52, lo52, ind):
    """Stock-level breadth for the Nifty 500: % positive and median return by horizon, the 1M return
    distribution, distance from 52-week highs, size buckets, concentration and sector risk-return."""
    close = close.iloc[-300:]
    last = close.iloc[-1]
    hz = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}
    ret = {k: (last / close.iloc[-1 - n] - 1) * 100 for k, n in hz.items() if len(close) > n}
    horizons = [{"h": k, "pos": round(float((r > 0).mean() * 100), 1), "median": round(float(r.median()), 1)}
                for k, r in ret.items()]
    # rolling % positive, last 6 months
    roll = {}
    for k in ["1D", "1W", "1M", "3M"]:
        n = hz[k]
        pos = ((close / close.shift(n) - 1) > 0).sum(axis=1) / close.shift(n).notna().sum(axis=1) * 100
        roll[k] = to_points(pos.iloc[-126:], 1)
    r1m = ret["1M"].dropna()
    edges = list(range(-30, 35, 5))
    hist_ = np.histogram(r1m.clip(-32.5, 32.5), bins=[-35] + edges + [35])[0]
    dist_hi = ((last / hi52.iloc[-1] - 1) * 100).dropna()
    buckets = [("< -50%", -999, -50), ("-50 to -30%", -50, -30), ("-30 to -20%", -30, -20), ("-20 to -10%", -20, -10),
               ("-10 to -5%", -10, -5), ("-5 to -1%", -5, -1), ("Within 1%", -1, 1)]
    near_hi = ((close / hi52.reindex(close.index) - 1) >= -0.02).sum(axis=1).iloc[-126:]
    near_lo = ((close / lo52.reindex(close.index) - 1) <= 0.02).sum(axis=1).iloc[-126:]
    mcap = pd.Series(EXTRA.get("n500_mcap", {})).reindex(close.columns).dropna().sort_values(ascending=False)
    size = []
    for nm, a, b in [("Large (1-100)", 0, 100), ("Mid (101-250)", 100, 250), ("Small (251-500)", 250, 500)]:
        r = r1m.reindex(mcap.index[a:b]).dropna()
        if len(r):
            size.append({"bucket": nm, "mean": round(float(r.mean()), 1), "median": round(float(r.median()), 1), "n": int(len(r))})
    conc = None
    if len(mcap) and EXTRA.get("nse_mcap_cr"):
        tot = EXTRA["nse_mcap_cr"] * 1e7
        conc = [{"n": n, "share": round(float(mcap.iloc[:n].sum() / tot * 100), 1)} for n in (10, 20, 50, 100, 200, 500) if n <= len(mcap)]
    tb = r1m.sort_values()
    top_bottom = {"top": [{"s": s.replace(".NS", ""), "r": round(float(v), 1)} for s, v in tb.iloc[::-1][:10].items()],
                  "bottom": [{"s": s.replace(".NS", ""), "r": round(float(v), 1)} for s, v in tb[:10].items()]}
    risk = []
    daily = close.pct_change().iloc[-63:]
    for sec, cols in pd.Series(ind).groupby(lambda t: ind[t]).groups.items():
        cols = [c for c in cols if c in daily.columns and c in mcap.index]
        if len(cols) < 3:
            continue
        w = mcap[cols] / mcap[cols].sum()
        idx = (daily[cols].fillna(0) * w).sum(axis=1)
        risk.append({"sector": sec, "ret3m": round(float(((1 + idx).prod() - 1) * 100), 1),
                     "vol": round(float(idx.std() * np.sqrt(252) * 100), 1), "mcap": round(float(mcap[cols].sum() / 1e12), 2), "n": len(cols)})
    return {"asof": close.index[-1].strftime("%d %b %Y"), "horizons": horizons, "rolling": roll,
            "hist": {"edges": edges, "counts": [int(x) for x in hist_], "median": round(float(r1m.median()), 1),
                     "mean": round(float(r1m.mean()), 1), "pos": round(float((r1m > 0).mean() * 100), 1)},
            "dist_hi": [{"b": nm, "n": int(((dist_hi > a) & (dist_hi <= b)).sum())} for nm, a, b in buckets],
            "near": {"dates": [d.strftime("%Y-%m-%d") for d in near_hi.index], "hi": [int(x) for x in near_hi], "lo": [int(x) for x in near_lo]},
            "size": size, "concentration": conc, "top_bottom": top_bottom, "risk": risk}


# --------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    steps = {}
    fetch_fred()
    fetch_eia()
    daily = fetch_yahoo()
    gold_px = float(daily["GC=F"].dropna().iloc[-1]) if "GC=F" in daily else None
    import fetch_india as FI
    nse = None
    try:
        nse = fetch_nse_indices()
    except Exception as e:  # noqa: BLE001
        log(f"  ! NSE indices failed: {e}")
    for name, fn in [("gold", lambda: fetch_imf(gold_px)), ("mospi", fetch_mospi), ("flows", fetch_flows),
                     ("category", fetch_nse_category), ("fpi", fetch_nsdl_fpi), ("ratios", lambda: fetch_ratios(daily, nse)),
                     ("banking", fetch_banking), ("mcap", lambda: fetch_mcap_gdp(daily)), ("sectors", fetch_sectors),
                     ("repo", fetch_repo), ("breadth", fetch_breadth),
                     ("credit", FI.fetch_rbi_credit), ("cpi", FI.fetch_cpi_detail), ("indices", FI.fetch_nse_tri),
                     ("bop", FI.fetch_bop)]:
        try:
            steps[name] = fn()
        except Exception as e:  # noqa: BLE001
            log(f"  ! {name} failed: {e}")
            steps[name] = None
    # a source that is down today should not blank the page: reuse yesterday's copy of slow-moving series
    cache_path = DATA / "series_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    for sid, s in cache.items():
        if sid not in SERIES:
            SERIES[sid] = {**s, "note": (s.get("note") or "") + " (from the previous run; source unavailable today)"}
            log(f"  ! {sid}: source failed, reused cached copy")
    slow = {sid: s for sid, s in SERIES.items() if s["freq"] in ("monthly", "quarterly", "annual", "periodic")
            and not s.get("note", "").endswith("source unavailable today)")}
    cache_path.write_text(json.dumps({**cache, **slow}, separators=(",", ":")), encoding="utf-8")
    out = {
        "generated": (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M IST"),
        "series": SERIES,
        "gold": steps["gold"],
        "flows": steps["flows"],
        "repo": steps["repo"],
        "breadth": steps["breadth"],
        "category": steps["category"],
        "sectors": steps["sectors"],
        "credit": steps["credit"],
        "cpi": steps["cpi"],
        "indices": steps["indices"],
        "log": LOG,
    }
    (DATA / "macro.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log(f"Done: {len(SERIES)} series in {time.time() - t0:.0f}s -> data/macro.json")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import fetch  # run through the importable module so fetch_india shares SERIES / EXTRA with it
    fetch.main()
