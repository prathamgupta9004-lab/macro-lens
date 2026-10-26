"""India detail fetchers: RBI sector-wise bank credit, CPI components and core CPI,
NSE Indices total-return history, and the quarterly balance of payments.

Used by fetch.py. Each fetcher keeps a CSV history in data/ so a daily run only
downloads what is new.
"""
import base64
import io
import json
import re
import time

import numpy as np
import pandas as pd
import requests
from scipy.optimize import nnls

from fetch import BROWSER, DATA, EXTRA, MONTHS, SERIES, TODAY, _LegacyTLS, add, fred, log, mospi_pages, retry, weekly

# --------------------------------------------------------------------------- RBI sector-wise bank credit
RBI_LIST = "https://rbi.org.in/Scripts/Data_Sectoral_Deployment.aspx"
CREDIT_SECTORS = [  # (name, regex on the row label); order matters, first match wins
    ("Non-food credit", r"^(iii\.?\s*)?non-?food credit"),
    ("Agriculture", r"^(1\.?\s*)?agriculture\s*(and|&)\s*allied"),
    ("Industry", r"^(2\.?\s*)?industry\b"),
    ("Micro and small", r"^(2\.1\.?\s*)?micro\s*(and|&)\s*small"),
    ("Medium", r"^(2\.2\.?\s*)?medium$"),
    ("Large", r"^(2\.3\.?\s*)?large$"),
    ("Services", r"^(3\.?\s*)?services\b"),
    ("Transport operators", r"transport operators"),
    ("Computer software", r"computer software"),
    ("Tourism, hotels and restaurants", r"tourism"),
    ("Shipping", r"^(3\.\d+\.?\s*)?shipping"),
    ("Aviation", r"^(3\.\d+\.?\s*)?aviation"),
    ("Professional services", r"professional services"),
    ("Trade", r"^(3\.\d+\.?\s*)?trade$"),
    ("Wholesale trade", r"wholesale trade"),
    ("Retail trade", r"retail trade"),
    ("Commercial real estate", r"commercial real estate"),
    ("NBFCs", r"non-?banking financial compan|^(3\.\d+\.?\s*)?nbfcs?\b"),
    ("Other services", r"^(3\.\d+\.?\s*)?other services"),
    ("Personal loans", r"^(4\.?\s*)?personal loans"),
    ("Consumer durables", r"consumer durables"),
    ("Housing", r"^(4\.\d+\.?\s*)?housing\b"),
    ("Loans against FD", r"fixed deposits"),
    ("Loans against shares", r"against share"),
    ("Credit cards", r"credit card"),
    ("Education", r"^(4\.\d+\.?\s*)?education"),
    ("Vehicle loans", r"vehicle loans"),
    ("Gold loans", r"gold jewellery|against gold"),
    ("Other personal loans", r"other personal loans"),
]
MON3 = {m[:3].lower(): i for m, i in MONTHS.items()}


def _as_date(v):
    if hasattr(v, "year") and not isinstance(v, str):
        try:
            return pd.Timestamp(v).normalize()
        except Exception:  # noqa: BLE001
            return None
    s = str(v).strip()
    m = re.search(r"(\d{1,2})[\s.\-]*([A-Za-z]{3})[a-z]*[\s.,\-]*(\d{4})", s)
    if m and m.group(2).lower() in MON3:
        return pd.Timestamp(int(m.group(3)), MON3[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"([A-Za-z]{3})[a-z]*[\s.,\-]*(\d{1,2})[\s,]+(\d{4})", s)
    if m and m.group(1).lower() in MON3:
        return pd.Timestamp(int(m.group(3)), MON3[m.group(1).lower()], int(m.group(2)))
    return None


def _label(s):
    s = re.sub(r"\s+", " ", str(s)).strip().lower()
    s = re.sub(r"\d+$", "", s).strip()           # footnote digits glued to the label
    return re.sub(r"^\(?[ivx]+\)?\.?\s+", "", s)  # roman numerals


def _parse_credit_sheet(df):
    out, date_cols = [], {}
    for _, row in df.iterrows():
        dates = {j: _as_date(v) for j, v in row.items() if j > 0 and _as_date(v) is not None and "/" not in str(v)}
        if len(dates) >= 2:
            date_cols = {}
            for j, d in dates.items():  # outstanding columns first; stop where the variation block repeats dates
                if d in date_cols.values():
                    break
                date_cols[j] = d
            continue
        cells = [str(v) for v in row.iloc[:3] if pd.notna(v) and re.search(r"[a-z]{3}", str(v))]
        label = _label(cells[0]) if cells else ""
        if not label or not date_cols:
            continue
        for name, rx in CREDIT_SECTORS:
            if re.search(rx, label):
                if name not in {o["sector"] for o in out}:
                    for j, d in date_cols.items():
                        v = pd.to_numeric(row.iloc[j], errors="coerce")
                        if pd.notna(v):
                            out.append({"sector": name, "date": d, "value": float(v)})
                break
    out = pd.DataFrame(out)
    if not out.empty:
        nf = out[out.sector.isin(["Non-food credit", "Industry"])]["value"]
        if len(nf) and nf.max() < 2e5:  # older releases report INR billion
            out["value"] *= 100
    return out


def parse_credit_release(content):
    """One RBI release workbook -> rows for its latest date, with YoY growth from the same file."""
    xl = pd.ExcelFile(io.BytesIO(content))
    best, key = None, None
    for sh in xl.sheet_names:
        try:
            d = _parse_credit_sheet(xl.parse(sh, header=None))
        except Exception:  # noqa: BLE001
            continue
        if d.empty or d["sector"].nunique() < 10:
            continue
        k = (d["date"].max(), d["sector"].nunique())
        if key is None or k > key:
            best, key = d, k
    if best is None:
        return []
    latest = best["date"].max()
    cur = best[best.date == latest].set_index("sector")["value"]
    # year-ago value per sector (files carry separate date rows for headline credit and for sectors)
    win = best[(best.date <= latest - pd.Timedelta(days=345)) & (best.date >= latest - pd.Timedelta(days=385))]
    ago = win.sort_values("date").groupby("sector")["value"].last() if len(win) else pd.Series(dtype=float)
    return [{"date": latest.strftime("%Y-%m-%d"), "sector": s, "value": round(v, 2),
             "yoy": round((v / ago[s] - 1) * 100, 2) if s in ago and ago[s] else None} for s, v in cur.items()]


def rbi_credit_releases(years):
    s = requests.Session()
    s.headers.update(BROWSER)
    t = s.get(RBI_LIST, timeout=60).text
    form = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]+" value="([^"]*)"', t))
    found = {}
    for y in years:
        r = s.post(RBI_LIST, data={**form, "hdnYear": str(y), "UsrFontCntr$btn": ""}, timeout=60).text
        vs = re.search(r'id="__VIEWSTATE" value="([^"]+)"', r)
        raw = base64.b64decode(vs.group(1) + "==").decode("utf-8", "ignore") if vs else ""
        found.update(dict(re.findall(r"prid=(\d+)[^>]*>([^<]*ectoral[^<]*)<", raw + r)))
    return s, found


def fetch_rbi_credit():
    log("RBI sector-wise bank credit")
    path, done_path = DATA / "rbi_sectoral_credit.csv", DATA / "rbi_sectoral_releases.json"
    hist = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "sector", "value", "yoy"])
    done = json.loads(done_path.read_text()) if done_path.exists() else []
    years = range(2010, TODAY.year + 1) if hist.empty else [TODAY.year - 1, TODAY.year]
    try:
        s, rel = rbi_credit_releases(years)
        new = [p for p in rel if p not in done]
        rows = []
        for prid in new:
            page = s.get(f"https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={prid}", timeout=60).text
            xl = [l for l in re.findall(r'href="([^"]+\.(?:xlsx?|XLSX?))"', page)]
            if not xl:
                continue
            rows += parse_credit_release(s.get(xl[0], timeout=60).content)
            done.append(prid)
        if rows:
            hist = pd.concat([hist, pd.DataFrame(rows)]).drop_duplicates(["date", "sector"], keep="last").sort_values(["date", "sector"])
            hist.to_csv(path, index=False)
        done_path.write_text(json.dumps(sorted(set(done))))
        log(f"  releases: {len(rel)} listed, {len(new)} new; history {hist['date'].nunique()} months")
    except Exception as e:  # noqa: BLE001
        log(f"  ! RBI credit: {e}")
    if hist.empty:
        return None
    hist["date"] = pd.to_datetime(hist["date"])
    dates = sorted(hist["date"].unique())
    piv_y = hist.pivot_table(index="date", columns="sector", values="yoy").reindex(dates)
    piv_v = hist.pivot_table(index="date", columns="sector", values="value").reindex(dates)
    for s_, sid in [("Non-food credit", "RBI_NONFOOD_YOY"), ("Personal loans", "RBI_PERSONAL_YOY"),
                    ("Micro and small", "RBI_MSE_YOY"), ("Gold loans", "RBI_GOLD_YOY")]:
        if s_ in piv_y:
            add(sid, piv_y[s_], f"Bank credit growth: {s_.lower()}", "% YoY", "RBI", "banking", "monthly", 1)
    return {"dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates],
            "sectors": [s_ for s_, _ in CREDIT_SECTORS if s_ in piv_y],
            "yoy": {c: [None if pd.isna(v) else round(float(v), 1) for v in piv_y[c]] for c in piv_y},
            "value": {c: [None if pd.isna(v) else round(float(v)) for v in piv_v[c]] for c in piv_v}}


# --------------------------------------------------------------------------- CPI detail and core CPI
CPI24_WEIGHTS = {  # MOSPI 2024-base division weights (%), as published with the new series in Feb 2026
    "Food and beverages": 36.75, "Paan, tobacco and intoxicants": 3.0, "Clothing and footwear": 6.4,
    "Housing, water, electricity, gas and other fuels": 17.67,
    "Furnishings, household equipment and routine household maintenance": 4.5, "Health": 6.1, "Transport": 8.8,
    "Information and communication": 3.61, "Recreation, sport and culture": 1.5, "Education services": 3.3,
    "Restaurants and accommodation services": 3.3,
    "Personal care, social protection and miscellaneous goods and services": 5.0,
}
CPI12_WEIGHTS = {"Food and Beverages": 45.86, "Fuel and Light": 6.84}  # 2012-base combined weights (%)


def fetch_cpi_detail():
    log("CPI detail")
    s = requests.Session()
    s.mount("https://", _LegacyTLS())
    month = lambda r: pd.Timestamp(int(r["year"]), MONTHS[r["month"]], 1)
    out = {}
    # 2024 base: every division, group and class (All India, combined)
    rows = []
    for div in range(0, 14):
        rows += mospi_pages(s, "/api/cpi/getCPIData", {"base_year": "2024", "level": "Group", "series": "Current",
                                                       "state_code": "1", "sector_code": "3", "division_code": str(div)})
    c24 = pd.DataFrame(rows)
    c24["d"] = [month(r) for r in rows]
    c24["index"] = pd.to_numeric(c24["index"], errors="coerce")
    c24["inflation"] = pd.to_numeric(c24["inflation"], errors="coerce")
    gen24 = c24[(c24.division == "CPI (General)") & c24.group.isna()].groupby("d")["index"].last().sort_index()
    div24 = c24[(c24.division != "CPI (General)") & c24.group.isna() & c24["class"].isna()].pivot_table(index="d", columns="division", values="index")
    grp24 = c24[c24.group.notna() & c24["class"].isna()]
    # fuel's share inside housing is not published; recover it from the group indices (fits to 0.01)
    housing = "Housing, water, electricity, gas and other fuels"
    hg = grp24[grp24.division == housing].pivot_table(index="d", columns="group", values="index").sort_index()
    w, _ = nnls(hg.loc[div24.index].values, div24[housing].values) if len(hg) else (np.array([]), 0)
    fuel_share = dict(zip(hg.columns, w)).get("Electricity, gas and other fuels", 0.304)
    fuel = hg["Electricity, gas and other fuels"]
    wf, wu = CPI24_WEIGHTS["Food and beverages"], CPI24_WEIGHTS[housing] * fuel_share
    core24 = (gen24 * 100 - wf * div24["Food and beverages"] - wu * fuel) / (100 - wf - wu)
    log(f"  2024 base: {gen24.index.min():%b %Y} to {gen24.index.max():%b %Y}; fuel weight {wu:.2f}%")

    # 2012 base: groups and sub-groups back to 2013
    rows = mospi_pages(s, "/api/cpi/getCPIIndex", {"base_year": "2012", "series": "Current", "sector_code": "3",
                                                   "state_code": "99"}, max_pages=80)
    c12 = pd.DataFrame(rows)
    c12["d"] = [month(r) for r in rows]
    c12["index"] = pd.to_numeric(c12["index"], errors="coerce")
    c12["inflation"] = pd.to_numeric(c12["inflation"], errors="coerce")
    ov = lambda g: c12[c12.subgroup == f"{g}-Overall"].groupby("d")["index"].last().sort_index()
    gen12, food12, fuel12 = ov("General"), ov("Food and Beverages"), ov("Fuel and Light")
    w1, w2 = CPI12_WEIGHTS["Food and Beverages"], CPI12_WEIGHTS["Fuel and Light"]
    core12 = (gen12 * 100 - w1 * food12 - w2 * fuel12) / (100 - w1 - w2)

    core_yoy = (core24.pct_change(12) * 100).dropna()
    core_yoy = core_yoy.combine_first((core12.pct_change(12) * 100)[lambda x: x.index < core_yoy.index.min()] if len(core_yoy) else core12.pct_change(12) * 100)
    add("IN_CORE_CPI", core_yoy, "India core CPI (excluding food, beverages and fuel)", "% YoY", "MOSPI (computed)",
        "india", "monthly", 2, note="Computed from official weights: 2024 base from 2026, 2012 base before.")
    both = gen24.index.intersection(gen12.index)
    scale = (gen24[both] / gen12[both]).mean() if len(both) else np.nan
    level = gen24.combine_first(gen12[gen12.index < gen24.index.min()] * scale) if pd.notna(scale) else gen24
    add("IN_CPI_INDEX", level, "India CPI index (2024 = 100, older base rescaled)", "index", "MOSPI", "india", "monthly", 2)

    # heat tables: 2024 base divisions and groups; 2012 base groups and sub-groups (the series RBI used to Dec 2025)
    def table(df, keyfun, order, n):
        ms = sorted(df["d"].unique())[-n:]
        rows_ = []
        for key, label, depth in order:
            sub = df[keyfun(df, key)].groupby("d")["inflation"].last()
            rows_.append({"label": label, "depth": depth, "values": [None if m not in sub.index or pd.isna(sub.get(m)) else round(float(sub[m]), 1) for m in ms]})
        return {"months": [pd.Timestamp(m).strftime("%b %y") for m in ms][::-1], "rows": [{**r, "values": r["values"][::-1]} for r in rows_]}

    order24 = [("CPI (General)", "CPI (General)", 0)]
    for dv in CPI24_WEIGHTS:
        order24.append((("div", dv), dv, 0))
        for g in sorted(grp24[grp24.division == dv]["group"].unique()):
            order24.append((("grp", dv, g), g, 1))
    def key24(df, k):
        if k == "CPI (General)":
            return (df.division == k) & df.group.isna()
        if k[0] == "div":
            return (df.division == k[1]) & df.group.isna() & df["class"].isna()
        return (df.division == k[1]) & (df.group == k[2]) & df["class"].isna()
    yo = c24[c24["inflation"].notna()]
    out["cpi24"] = table(yo, key24, order24, 15)
    out["cpi24"]["rows"].append({"label": "Core CPI (computed)", "depth": 0,
                                 "values": [None if pd.isna(v) else round(float(v), 1) for v in (core24.pct_change(12) * 100).reindex(sorted(yo["d"].unique())[-15:])][::-1]})
    groups12 = ["General", "Food and Beverages", "Pan, Tobacco and Intoxicants", "Clothing and Footwear", "Housing",
                "Fuel and Light", "Miscellaneous"]
    order12 = []
    for g in groups12:
        order12.append((g + "-Overall", "CPI (General)" if g == "General" else g, 0))
        for sg in sorted(c12[(c12.group == g) & ~c12.subgroup.str.endswith("Overall")]["subgroup"].unique()):
            order12.append((sg, sg, 1))
    out["cpi12"] = table(c12, lambda df, k: df.subgroup == k, order12, 24)
    # division snapshot for the components chart: latest, 1, 3 and 6 months ago
    ms = sorted(yo["d"].unique())
    pick = [m for m in [ms[-1], ms[-2] if len(ms) > 1 else None, ms[-4] if len(ms) > 3 else None, ms[-7] if len(ms) > 6 else None] if m is not None]
    out["components"] = {"months": [pd.Timestamp(m).strftime("%b %y") for m in pick],
                         "rows": [{"label": dv, "weight": w_, "values": [None if pd.isna(v) else float(v) for v in
                                   [yo[(yo.division == dv) & yo.group.isna() & (yo.d == m)]["inflation"].mean() for m in pick]]}
                                  for dv, w_ in CPI24_WEIGHTS.items()]}
    return out


# --------------------------------------------------------------------------- NSE Indices total-return history
NI_URL = "https://www.niftyindices.com/BackPage/"
TRI_INDICES = ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 500", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250",
               "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "NIFTY MICROCAP 250", "NIFTY500 EQUAL WEIGHT",
               "NIFTY500 MOMENTUM 50", "NIFTY500 QUALITY 50", "NIFTY500 VALUE 50", "NIFTY500 LOW VOLATILITY 50"]


def _ni_session():
    s = requests.Session()
    s.headers.update({**BROWSER, "Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest",
                      "Origin": "https://www.niftyindices.com", "Referer": "https://www.niftyindices.com/reports/historical-data"})
    s.get("https://www.niftyindices.com/reports/historical-data", timeout=30)
    return s


def _ni_get(s, endpoint, name, start, end):
    cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (name, start.strftime("%d-%b-%Y"), end.strftime("%d-%b-%Y"), name)
    r = retry(lambda: s.post(NI_URL + endpoint, data=json.dumps({"cinfo": cinfo}), timeout=90))
    try:
        return r.json() or []
    except ValueError:
        return []


def fetch_nse_tri():
    """Daily total-return index for the main NSE indices (from 2005) and Nifty 50 price (from 1990)."""
    log("NSE Indices history")
    path = DATA / "nse_tri.csv"
    hist = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame(columns=["date", "index", "value"])
    s = _ni_session()
    rows = []
    jobs = [(nm, "getTotalReturnIndexString", "TotalReturnsIndex", "Date", pd.Timestamp("2005-04-01")) for nm in TRI_INDICES]
    jobs.append(("NIFTY 50 PRICE", "getHistoricaldatatabletoString", "CLOSE", "HistoricalDate", pd.Timestamp("1990-07-01")))
    for key, ep, vcol, dcol, first in jobs:
        have = hist[hist["index"] == key]["date"]
        start = (have.max() - pd.Timedelta(days=7)) if len(have) else first
        name = "NIFTY 50" if key == "NIFTY 50 PRICE" else key
        a = start
        while a <= TODAY:
            b = min(a + pd.DateOffset(years=1) - pd.Timedelta(days=1), TODAY)
            for x in _ni_get(s, ep, name, a, b):
                try:
                    rows.append({"date": pd.to_datetime(x[dcol], format="%d %b %Y"), "index": key, "value": float(str(x[vcol]).replace(",", ""))})
                except (KeyError, ValueError):
                    pass
            a = b + pd.Timedelta(days=1)
            time.sleep(0.3)
    if rows:
        hist = pd.concat([hist, pd.DataFrame(rows)]).drop_duplicates(["date", "index"], keep="last").sort_values(["index", "date"])
        hist.to_csv(path, index=False, date_format="%Y-%m-%d")
    log(f"  NSE Indices: {len(rows)} new rows, {hist['index'].nunique()} indices")
    piv = hist.pivot_table(index="date", columns="index", values="value").sort_index()
    for nm in ["NIFTY 50", "NIFTY 500", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250"]:
        if nm in piv:
            add("TRI:" + nm, weekly(piv[nm].dropna()), nm.title().replace("Nifty", "Nifty") + " TRI", "index",
                "NSE Indices", "india_mkt", "weekly", 2)
    return index_stats(piv)


def index_stats(piv):
    """Monthly-return heatmaps and the multi-horizon index table."""
    def heat(series):
        m = series.dropna().resample("ME").last()
        r = m.pct_change() * 100
        years = sorted({d.year for d in r.dropna().index}, reverse=True)
        grid = {y: [None] * 12 for y in years}
        for d, v in r.dropna().items():
            grid[d.year][d.month - 1] = round(float(v), 1)
        annual = (m.groupby(m.index.year).last() / m.groupby(m.index.year).last().shift() - 1) * 100
        mat = pd.DataFrame(r.dropna()).assign(y=lambda x: x.index.year, mo=lambda x: x.index.month).pivot(index="y", columns="mo", values=r.name if r.name is not None else 0)
        stat = lambda f: [None if pd.isna(v) else round(float(v), 1) for v in [f(mat[c].dropna()) if c in mat else np.nan for c in range(1, 13)]]
        ann = annual.dropna()
        return {"years": years, "grid": [grid[y] for y in years],
                "annual": [None if y not in annual.index or pd.isna(annual[y]) else round(float(annual[y]), 1) for y in years],
                "median": stat(np.median), "sd": stat(lambda x: x.std()), "avg": stat(np.mean),
                "ann_stats": [round(float(ann.median()), 1), round(float(ann.std()), 1), round(float(ann.mean()), 1)]}
    heats = {}
    for key, label in [("NIFTY 50 PRICE", "Nifty 50 (price)"), ("NIFTY 500", "Nifty 500 TRI"),
                       ("NIFTY MIDCAP 150", "Midcap 150 TRI"), ("NIFTY SMALLCAP 250", "Smallcap 250 TRI")]:
        if key in piv:
            s_ = piv[key].dropna()
            s_.name = 0
            heats[label] = heat(s_)
    table = []
    for key in ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250", "NIFTY MICROCAP 250",
                "NIFTY 500", "NIFTY500 EQUAL WEIGHT", "NIFTY500 MOMENTUM 50", "NIFTY500 QUALITY 50", "NIFTY500 VALUE 50",
                "NIFTY500 LOW VOLATILITY 50"]:
        if key not in piv:
            continue
        s_ = piv[key].dropna()
        last_d = s_.index[-1]
        def back(**kw):
            p = s_[s_.index <= last_d - pd.DateOffset(**kw)]
            return p.iloc[-1] if len(p) else np.nan
        me = s_[s_.index < pd.Timestamp(last_d.year, last_d.month, 1)]
        row = {"index": key.title().replace("Nifty500", "Nifty500"), "asof": last_d.strftime("%d %b %Y"),
               "mtd": (s_.iloc[-1] / me.iloc[-1] - 1) * 100 if len(me) else None}
        for lbl, kw, yrs in [("3M", {"months": 3}, None), ("6M", {"months": 6}, None), ("1Y", {"years": 1}, None),
                             ("3Y", {"years": 3}, 3), ("5Y", {"years": 5}, 5), ("7Y", {"years": 7}, 7), ("10Y", {"years": 10}, 10)]:
            b = back(**kw)
            v = (s_.iloc[-1] / b) if b and not pd.isna(b) else np.nan
            row[lbl] = None if pd.isna(v) else round(float((v ** (1 / yrs) - 1) * 100 if yrs else (v - 1) * 100), 1)
        row["dd"] = round(float((s_.iloc[-1] / s_.max() - 1) * 100), 1)
        row["mtd"] = None if row["mtd"] is None else round(float(row["mtd"]), 1)
        table.append(row)
    return {"heat": heats, "table": table}


# --------------------------------------------------------------------------- balance of payments (IMF, quarterly)
def fetch_bop():
    log("Balance of payments (IMF)")
    want = {"NETCD_T": ["CAB", "G", "G3", "S", "GS", "IN2", "D_F_D4P", "P_F_D4P"], "CD_T": ["SI", "G", "S"], "DB_T": ["G3", "G"]}
    ser = {}
    for entry, inds in want.items():
        url = f"https://api.imf.org/external/sdmx/2.1/data/IMF.STA,BOP/IND.{entry}.{'+'.join(inds)}.USD.Q?startPeriod=2004"
        try:
            r = retry(lambda: requests.get(url, timeout=180))
        except Exception as e:  # noqa: BLE001
            log(f"  ! BoP {entry}: {e}")
            continue
        for m in re.finditer(r"<Series ([^>]+)>(.*?)</Series>", r.text, re.S):
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            pts = {pd.Period(p.replace("-Q", "Q"), "Q").end_time.normalize(): float(v) / 1e9
                   for p, v in re.findall(r'TIME_PERIOD="([^"]+)" OBS_VALUE="([^"]+)"', m.group(2))}
            ser[(entry, a["INDICATOR"])] = pd.Series(pts).sort_index()
    g = lambda e, i: ser.get((e, i))
    src = "RBI data via IMF (BPM6)"
    if g("NETCD_T", "CAB") is not None:
        cab = g("NETCD_T", "CAB")
        add("BOP_CAB", cab, "Current account balance", "USD bn", src, "bop", "quarterly", 2)
        if g("NETCD_T", "G3") is not None:
            add("BOP_CAB_XGOLD", cab - g("NETCD_T", "G3"), "Current account balance excluding gold", "USD bn", src, "bop", "quarterly", 2,
                note="Adds back net non-monetary gold imports.")
            add("BOP_GOLD", -g("NETCD_T", "G3"), "Net gold imports", "USD bn", src, "bop", "quarterly", 2)
        gdp_q, inr = EXTRA.get("in_gdp_q"), SERIES.get("DEXINUS")
        if gdp_q is not None and inr is not None:
            fx = pd.Series({pd.Timestamp(d): v for d, v in inr["data"]}).resample("QE").mean()
            usd_gdp = (gdp_q * 1e7 / fx.reindex(gdp_q.index, method="nearest") / 1e9).rolling(4).sum()
            add("BOP_CAB_GDP", (cab.rolling(4).sum() / usd_gdp.reindex(cab.index) * 100).dropna(),
                "Current account balance (% of GDP, 4 quarters)", "% of GDP", "IMF, MOSPI (computed)", "bop", "quarterly", 2)
    for e, i, sid, nm in [("NETCD_T", "G", "BOP_GOODS", "Goods trade balance"), ("NETCD_T", "S", "BOP_SERVICES", "Services trade balance"),
                          ("NETCD_T", "GS", "BOP_GS", "Goods and services balance (trade deficit)"),
                          ("CD_T", "SI", "BOP_SOFTWARE", "Telecom, computer and information services exports"),
                          ("CD_T", "G", "BOP_GOODS_X", "Goods exports"), ("DB_T", "G", "BOP_GOODS_M", "Goods imports"),
                          ("CD_T", "S", "BOP_SERVICES_X", "Services exports"),
                          ("NETCD_T", "IN2", "BOP_REMIT", "Remittances and other transfers (net)")]:
        if g(e, i) is not None:
            add(sid, g(e, i), nm, "USD bn", src, "bop", "quarterly", 2)
    if g("CD_T", "SI") is not None:
        add("BOP_SOFTWARE_YOY", g("CD_T", "SI").pct_change(4) * 100, "Computer services exports growth", "% YoY", src, "bop", "quarterly", 1)
    for i, sid, nm in [("D_F_D4P", "BOP_FDI", "Net direct investment inflow"), ("P_F_D4P", "BOP_FPI", "Net portfolio investment inflow")]:
        if g("NETCD_T", i) is not None:
            add(sid, -g("NETCD_T", i), nm, "USD bn", src, "bop", "quarterly", 2, note="Positive = money coming into India.")
    log(f"  BoP series: {len(ser)}")
