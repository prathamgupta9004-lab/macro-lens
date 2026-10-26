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
        j = retry(lambda: sess.get("https://api.mospi.gov.in" + path, params=p, timeout=60).json())
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
    }


# --------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    steps = {}
    fetch_fred()
    fetch_eia()
    daily = fetch_yahoo()
    gold_px = float(daily["GC=F"].dropna().iloc[-1]) if "GC=F" in daily else None
    for name, fn in [("gold", lambda: fetch_imf(gold_px)), ("mospi", fetch_mospi), ("flows", fetch_flows),
                     ("repo", fetch_repo), ("breadth", fetch_breadth)]:
        try:
            steps[name] = fn()
        except Exception as e:  # noqa: BLE001
            log(f"  ! {name} failed: {e}")
            steps[name] = None
    out = {
        "generated": (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M IST"),
        "series": SERIES,
        "gold": steps["gold"],
        "flows": steps["flows"],
        "repo": steps["repo"],
        "breadth": steps["breadth"],
        "log": LOG,
    }
    (DATA / "macro.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log(f"Done: {len(SERIES)} series in {time.time() - t0:.0f}s -> data/macro.json")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
