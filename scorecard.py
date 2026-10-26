"""Sector scorecard for Macro Lens.

compute_scorecard(D) turns the macro data dict into a per-sector tally of how many
macro indicators currently point FOR (+1), AGAINST (-1) or neither (0) each sector.
These are observations from the data, not buy or sell calls.

Every indicator uses one of the deterministic rules below. "3M" means the latest
reading against the reading roughly three months earlier (the latest point on or
before 88 days back, so monthly and quarterly series compare with the print one
quarter ago). Medians and z-scores use the stated look-back window, or the whole
history when a series is shorter.

  growth   (activity, credit, GDP, IIP; YoY growth rates)
           +1 if the 3M change is up AND the latest reading is above its 5-year median
           -1 if the 3M change is down AND the latest reading is below its 5-year median
            0 otherwise (trend and level disagree)
           For "lower is better" series (US jobless claims) the signs flip.
  cost     (inputs a sector buys: crude, gas, copper, wheat, CNY/INR)
           -1 if up more than 10% in 3M OR more than +1 sigma above its 10-year median
           +1 if down more than 10% in 3M OR more than 1 sigma below its 10-year median
            0 otherwise (the negative test is checked first)
  benefit  (a price a sector sells at, e.g. copper for metal producers)
           the cost rule with the signs flipped
  rate     (yields and policy rates, for borrowers and bond holders)
           -1 if up more than 25 bp in 3M, +1 if down more than 25 bp, else 0
  flow     (FPI and DII net equity buying, monthly INR cr)
           +1 if the sum of the last 3 months is above zero, -1 if below
  trend    (equity indices and ratios)
           +1 if up more than 3% in 3M AND above its 52-week average
           -1 if down more than 3% in 3M AND below its 52-week average, else 0
  fx       (USD/INR for exporters or importers, DXY for EM and metals)
           exporters: +1 if USD/INR is up more than 1.5% in 3M (weaker rupee),
           -1 if down more than 1.5%; importers the reverse. DXY uses 3%.
  band     (levels with fixed comfort zones: VIX, MOVE, CPI vs RBI comfort,
           breadth, yield curve, credit-deposit gap, real policy rate, etc.)
           thresholds are written next to each indicator below
  spread   (credit spreads)
           -1 if wider by more than 0.5 pts in 3M OR above +1 sigma vs 10-year median
           +1 if not wider in 3M AND below its 10-year median, else 0
  sector breadth (NSE industry groups in the Nifty 500, from D.breadth.sectors)
           +1 if at least 50% of stocks are above the 40-week EMA AND the 13-week return > 0
           -1 if fewer than 40% are above the 40-week EMA AND the 13-week return < 0, else 0

Indicators whose data is missing are skipped silently, so a sector's total N can vary.
Verdict: "Mostly negative" if negatives are at least half of N and outnumber positives,
"Mostly supportive" if positives are at least half of N and outnumber negatives,
otherwise "Mixed". Sectors are sorted by net score (positives minus negatives),
then by share of positives.
"""
from __future__ import annotations

import math

import pandas as pd

LOOKBACK_DAYS = 88  # "3 months back": one monthly print x3, or one quarterly print


# ---------------------------------------------------------------- data access
def _clean(pairs):
    out = []
    for p in pairs or []:
        try:
            d, v = p[0], p[1]
        except (TypeError, IndexError):
            continue
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(v) or math.isinf(v):
            continue
        out.append((pd.Timestamp(d), v))
    if not out:
        return None
    s = pd.Series([v for _, v in out], index=[d for d, _ in out]).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s if len(s) else None


def _series(D, sid):
    s = (D.get("series") or {}).get(sid)
    return _clean(s.get("data")) if s else None


def _credit(D, sector):
    c = D.get("credit") or {}
    vals = (c.get("yoy") or {}).get(sector)
    if not vals or not c.get("dates"):
        return None
    return _clean(list(zip(c["dates"], vals)))


def _product(a, b):
    """a x b aligned on a's dates (latest b on or before each a date)."""
    if a is None or b is None:
        return None
    da = a.rename("a").to_frame().reset_index().rename(columns={"index": "d"})
    db = b.rename("b").to_frame().reset_index().rename(columns={"index": "d"})
    m = pd.merge_asof(da.sort_values("d"), db.sort_values("d"), on="d", direction="backward").dropna()
    if m.empty:
        return None
    return pd.Series((m["a"] * m["b"]).values, index=m["d"].values)


def _ago(s, days=LOOKBACK_DAYS):
    t = s.index[-1] - pd.Timedelta(days=days)
    past = s[s.index <= t]
    return float(past.iloc[-1]) if len(past) else None


def _window(s, years):
    return s[s.index >= s.index[-1] - pd.Timedelta(days=int(365.25 * years))]


def _median(s, years):
    w = _window(s, years)
    return float(w.median()) if len(w) >= 3 else None


def _z(s, years):
    w = _window(s, years)
    if len(w) < 8:
        return None
    sd = float(w.std())
    if not sd or math.isnan(sd):
        return None
    return (float(s.iloc[-1]) - float(w.median())) / sd


def _num(v, d=1):
    return f"{v:,.{d}f}"


def _signed(v, d=1):
    return f"{'+' if v > 0 else ''}{v:,.{d}f}"


def _updown(chg, d=1, unit=" pts"):
    if chg is None:
        return "no 3M comparison"
    if abs(chg) < 0.5 * 10 ** (-d):
        return "flat in 3M"
    return f"{'up' if chg > 0 else 'down'} {abs(chg):,.{d}f}{unit} in 3M"


def _ind(name, value, signal, why, sid):
    return {"name": name, "value": value, "signal": int(signal), "why": why, "sid": sid}


def _pick(sig, pos, neg, neu):
    return pos if sig > 0 else neg if sig < 0 else neu


# ---------------------------------------------------------------- rules
def growth(s, name, sid, pos, neg, unit="% YoY", higher_good=True):
    """Growth rule: 3M direction AND level vs 5-year median must agree."""
    if s is None or len(s) < 3:
        return None
    last, prev, med = float(s.iloc[-1]), _ago(s), _median(s, 5)
    if prev is None or med is None:
        return None
    chg = last - prev
    eps = 0.05  # moves smaller than this count as flat (matches the 1-decimal display)
    up, above = chg > eps, last > med
    down, below = chg < -eps, last < med
    if higher_good:
        sig = 1 if (up and above) else -1 if (down and below) else 0
    else:
        sig = -1 if (up and above) else 1 if (down and below) else 0
    if unit.startswith("%"):
        lv, cu = f"{_num(last)}% {unit[1:].strip()}", " pts"
    else:
        lv, cu = f"{_num(last)} {unit.strip()}", " " + unit.strip()
    val = f"{lv.strip()}, {_updown(chg, unit=cu)}; 5Y median {_num(med)}"
    neu = (f"Trend and level disagree: {'rising' if up else 'falling' if down else 'flat'} over 3 months "
           f"but {'above' if above else 'below' if below else 'at'} its 5-year median.")
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), sid)


def price(s, name, sid, pos, neg, mode="cost", unit=""):
    """Cost rule (or benefit rule when mode='benefit'): >10% 3M move or beyond 1 sigma vs 10Y median."""
    if s is None or len(s) < 10:
        return None
    last, prev = float(s.iloc[-1]), _ago(s)
    if prev is None or not prev:
        return None
    pct = (last / prev - 1) * 100
    z = _z(s, 10)
    hot = pct > 10 or (z is not None and z > 1)
    cold = pct < -10 or (z is not None and z < -1)
    cost_sig = -1 if hot else 1 if cold else 0
    sig = cost_sig if mode == "cost" else -cost_sig
    zt = f", {_signed(z)} sigma vs 10Y median" if z is not None else ""
    val = f"{unit}{_num(last, 2 if last < 20 else 0 if last >= 1000 else 1)}, {_signed(pct)}% in 3M{zt}"
    neu = "Within its normal range: under a 10% move in 3 months and within 1 sigma of its 10-year median."
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), sid)


def rate(s, name, sid, pos, neg, inverse=False):
    """Rate rule: +/-25 bp in 3M. inverse=True when higher yields help the sector."""
    if s is None or len(s) < 3:
        return None
    last, prev = float(s.iloc[-1]), _ago(s)
    if prev is None:
        return None
    bp = (last - prev) * 100
    sig = -1 if bp > 25 else 1 if bp < -25 else 0
    if inverse:
        sig = -sig
    val = f"{_num(last, 2)}%, {'up' if bp > 0 else 'down'} {abs(bp):.0f} bp in 3M"
    neu = "Moved less than 25 bp in 3 months, so no strong push either way."
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), sid)


def flow(s, name, sid, pos, neg, months=3):
    """Flow rule: sum of the last 3 monthly prints above or below zero."""
    if s is None or len(s) < months:
        return None
    tot = float(s.iloc[-months:].sum())
    sig = 1 if tot > 0 else -1 if tot < 0 else 0
    last = float(s.iloc[-1])
    val = f"INR {_signed(tot / 1000, 1)}k cr over 3 months (latest month {_signed(last / 1000, 1)}k cr)"
    return _ind(name, val, sig, _pick(sig, pos, neg, "Flows net to zero over 3 months."), sid)


def trend(s, name, sid, pos, neg, inverse=False, d=0):
    """Trend rule: >3% 3M move confirmed by the 52-week average."""
    if s is None or len(s) < 10:
        return None
    last, prev = float(s.iloc[-1]), _ago(s, 91)
    if prev is None or not prev:
        return None
    pct = (last / prev - 1) * 100
    avg = float(_window(s, 1).mean())
    sig = 1 if (pct > 3 and last > avg) else -1 if (pct < -3 and last < avg) else 0
    if inverse:
        sig = -sig
    val = f"{_num(last, d)}, {_signed(pct)}% in 3M, {'above' if last > avg else 'below'} 52-week average"
    neu = "No clear trend: the 3-month move and the 52-week average do not line up."
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), sid)


def fx(s, name, sid, pos, neg, exporter=True, th=1.5, unit=""):
    """FX rule: 3M % move beyond +/-th. exporter=True: currency (USD/INR) up is good."""
    if s is None or len(s) < 10:
        return None
    last, prev = float(s.iloc[-1]), _ago(s, 91)
    if prev is None or not prev:
        return None
    pct = (last / prev - 1) * 100
    sig = 1 if pct > th else -1 if pct < -th else 0
    if not exporter:
        sig = -sig
    val = f"{unit}{_num(last, 2 if last < 1000 else 0)}, {_signed(pct)}% in 3M"
    neu = f"Moved less than {th}% in 3 months."
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), sid)


def band(last, name, sid, value, pos_if, neg_if, pos, neg, neu):
    """Band rule: fixed comfort zones. pos_if / neg_if are predicates on the latest value."""
    if last is None:
        return None
    sig = -1 if neg_if(last) else 1 if pos_if(last) else 0
    return _ind(name, value, sig, _pick(sig, pos, neg, neu), sid)


def spread(s, name, sid, pos, neg):
    """Spread rule: wider by >0.5 pts in 3M or >1 sigma vs 10Y median is bad."""
    if s is None or len(s) < 10:
        return None
    last, prev, med, z = float(s.iloc[-1]), _ago(s), _median(s, 10), _z(s, 10)
    if prev is None or med is None:
        return None
    chg = last - prev
    sig = -1 if (chg > 0.5 or (z is not None and z > 1)) else 1 if (chg <= 0 and last < med) else 0
    val = f"{_num(last * 100, 0)} bp, {_signed(chg * 100, 0)} bp in 3M; 10Y median {_num(med * 100, 0)} bp"
    neu = "Spreads are near normal but not tightening."
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), sid)


def sector_breadth(D, industry, name, pos, neg):
    """Sector breadth rule: % of stocks above 40-week EMA plus 13-week return."""
    b = D.get("breadth") or {}
    row = next((r for r in b.get("sectors") or [] if r.get("sector") == industry), None)
    if not row or row.get("40w") is None or row.get("ret13w") is None:
        return None
    a, r = float(row["40w"]), float(row["ret13w"])
    sig = 1 if (a >= 50 and r > 0) else -1 if (a < 40 and r < 0) else 0
    val = f"{_num(a, 0)}% of {row.get('n', '?')} stocks above 40-week EMA; 13-week return {_signed(r)}%"
    neu = "Participation and returns give mixed messages."
    return _ind(name, val, sig, _pick(sig, pos, neg, neu), "breadth:" + industry)


def _cpi_row(D, label):
    rows = ((D.get("cpi") or {}).get("cpi24") or {}).get("rows") or []
    r = next((x for x in rows if x.get("label") == label), None)
    if not r:
        return None
    v = [x for x in r.get("values") or []]
    return v if v and v[0] is not None else None


# ---------------------------------------------------------------- shared indicators
def _shared(D):
    S = lambda k: _series(D, k)  # noqa: E731
    brent = S("Y:BZ=F") if S("Y:BZ=F") is not None else S("DCOILBRENTEU")
    usdinr = S("Y:USDINR=X") if S("Y:USDINR=X") is not None else S("DEXINUS")
    brent_inr = _product(brent, usdinr)
    cpi = S("IN_CPI")
    cpi_last = float(cpi.iloc[-1]) if cpi is not None else None
    repo = D.get("repo")
    try:
        repo = float(repo) if repo is not None else None
    except (TypeError, ValueError):
        repo = None
    india10 = S("INDIRLTLT01STM")
    vix = S("Y:^INDIAVIX")
    return dict(S=S, brent=brent, usdinr=usdinr, brent_inr=brent_inr, cpi=cpi, cpi_last=cpi_last,
                repo=repo, india10=india10, vix=vix)


def _cpi_comfort(X, pos, neg):
    """Band: CPI <= 4% (RBI target) +1, CPI > 5% (above comfort, rate-hike risk) -1."""
    c = X["cpi_last"]
    if c is None:
        return None
    prev = _ago(X["cpi"])
    val = f"{_num(c, 2)}% YoY, {_updown(None if prev is None else c - prev)}; RBI target 4%"
    return band(c, "India CPI vs RBI comfort", "IN_CPI", val, lambda v: v <= 4, lambda v: v > 5,
                pos, neg, "Between the 4% target and 5% comfort line: no strong pressure on rates.")


def _india_vix(X, pos, neg):
    """Band: India VIX < 14 +1 (calm), > 20 -1 (stress)."""
    v = X["vix"]
    if v is None:
        return None
    last = float(v.iloc[-1])
    prev = _ago(v, 91)
    val = f"{_num(last)}" + (f", {_signed(last - prev)} in 3M" if prev is not None else "")
    return band(last, "India VIX", "Y:^INDIAVIX", val, lambda x: x < 14, lambda x: x > 20, pos, neg,
                "Between 14 and 20: normal volatility.")


def _brent_inr(X, pos, neg, mode="cost"):
    return price(X["brent_inr"], "Brent crude in rupees", "Y:BZ=F x Y:USDINR=X", pos, neg, mode, "INR ")


# ---------------------------------------------------------------- sectors
def _sectors(D, X):
    S, c = X["S"], lambda k: _credit(D, k)
    out = {}

    out["Banks and financials"] = [
        growth(S("IN_CREDIT_YOY"), "Bank credit growth", "IN_CREDIT_YOY",
               "Faster and above-normal loan growth lifts bank earnings.",
               "Slowing, below-normal loan growth caps balance-sheet expansion."),
        growth(S("IN_DEPOSITS_YOY"), "Bank deposit growth", "IN_DEPOSITS_YOY",
               "Strong deposit inflow funds loan growth cheaply and protects margins.",
               "Weak deposit growth forces banks to pay up for funds."),
        band(None if S("IN_CREDIT_DEP_GAP") is None else float(S("IN_CREDIT_DEP_GAP").iloc[-1]),
             "Credit growth minus deposit growth", "IN_CREDIT_DEP_GAP",
             None if S("IN_CREDIT_DEP_GAP") is None else f"{_signed(float(S('IN_CREDIT_DEP_GAP').iloc[-1]))} pts (band: within 2 pts good, beyond 4 pts bad)",
             lambda v: abs(v) <= 2, lambda v: abs(v) > 4,
             "Loans and deposits are growing in step, so no funding squeeze on margins.",
             "A wide gap means either a funding squeeze (credit far ahead) or weak loan demand.",
             "Gap of 2 to 4 pts: worth watching for funding pressure."),
        rate(X["india10"], "India 10Y government bond yield", "INDIRLTLT01STM",
             "Falling yields lift the value of banks' bond holdings and ease funding costs.",
             "Rising yields mean mark-to-market losses on bond books and costlier funding."),
        flow(S("FPI_EQUITY"), "FPI net equity flows", "FPI_EQUITY",
             "Financials are the largest FPI holding, so foreign buying supports them first.",
             "Financials are the largest FPI holding, so foreign selling hits them first."),
        growth(S("RBI_PERSONAL_YOY"), "Personal loan growth", "RBI_PERSONAL_YOY",
               "Retail lending, the highest-margin book, is accelerating.",
               "Retail lending, the highest-margin book, is slowing."),
        growth(S("RBI_MSE_YOY"), "Micro and small enterprise credit growth", "RBI_MSE_YOY",
               "MSME lending is accelerating, a high-yield segment for banks.",
               "MSME lending is slowing, a sign of weaker small-business demand."),
        sector_breadth(D, "Financial Services", "Financial services breadth (Nifty 500)",
                       "Most financial stocks are in uptrends.", "Most financial stocks are below long-term trend and falling."),
        _india_vix(X, "Calm markets support trading, broking and wealth businesses.",
                   "High volatility raises risk aversion and hurts market-linked financials."),
        _cpi_comfort(X, "Inflation at target leaves room for rate cuts that help credit demand.",
                     "Inflation above comfort raises the risk of RBI tightening and slower credit."),
    ]

    gap = S("IN_CREDIT_DEP_GAP")
    out["NBFCs and lending"] = [
        growth(c("NBFCs"), "Bank credit to NBFCs", "credit:NBFCs",
               "Banks are lending more to NBFCs, so funding is available.",
               "Bank funding to NBFCs is drying up."),
        growth(c("Gold loans"), "Gold loan growth", "credit:Gold loans",
               "Gold loan demand is booming, a core NBFC product.",
               "Gold loan demand is fading."),
        growth(c("Vehicle loans"), "Vehicle loan growth", "credit:Vehicle loans",
               "Vehicle finance, a large NBFC book, is accelerating.",
               "Vehicle finance demand is slowing."),
        growth(c("Other personal loans"), "Other personal loan growth", "credit:Other personal loans",
               "Unsecured retail demand is rising.", "Unsecured retail demand is slowing."),
        growth(c("Micro and small"), "Micro and small enterprise credit", "credit:Micro and small",
               "Small-business credit demand is rising.", "Small-business credit demand is fading."),
        rate(X["india10"], "India 10Y government bond yield", "INDIRLTLT01STM",
             "Falling yields cut NBFC borrowing costs in the bond market.",
             "Rising yields push up NBFC bond funding costs and squeeze spreads."),
        band(None if gap is None else float(gap.iloc[-1]), "Bank funding squeeze (credit minus deposit growth)",
             "IN_CREDIT_DEP_GAP", None if gap is None else f"{_signed(float(gap.iloc[-1]))} pts (above 3 pts bad, below 1 pt good)",
             lambda v: v < 1, lambda v: v > 3,
             "Banks have deposits to spare, so they keep lending to NBFCs.",
             "Banks short of deposits tend to cut wholesale lending to NBFCs first.",
             "Banks are stretched but not squeezed."),
        sector_breadth(D, "Financial Services", "Financial services breadth (Nifty 500)",
                       "Most financial stocks are in uptrends.", "Most financial stocks are below long-term trend and falling."),
        _cpi_comfort(X, "Inflation at target points to lower rates and cheaper funding.",
                     "Inflation above comfort points to higher rates and costlier NBFC funding."),
    ]

    cpi = X["cpi_last"]
    wpi = S("IN_WPI")
    wpi_last = float(wpi.iloc[-1]) if wpi is not None else None
    food = S("IN_CPI_FOOD")
    food_last = float(food.iloc[-1]) if food is not None else None
    food_prev = _ago(food) if food is not None else None
    out["Consumption and FMCG"] = [
        band(food_last, "CPI food inflation", "IN_CPI_FOOD",
             None if food_last is None else f"{_num(food_last)}% YoY, {_updown(None if food_prev is None else food_last - food_prev)} (below 4% good, above 6% bad)",
             lambda v: v < 4, lambda v: v > 6,
             "Mild food inflation leaves households, especially rural ones, more to spend on branded goods.",
             "High food inflation eats into household budgets and raises agri input costs.",
             "Food inflation of 4% to 6%: manageable."),
        growth(S("IN_PFCE"), "Real private consumption growth", "IN_PFCE",
               "Household spending is accelerating.", "Household spending is slowing."),
        growth(S("IN_IIP_CONSUMERNO"), "IIP consumer non-durables", "IN_IIP_CONSUMERNO",
               "Staples output is picking up, a sign of volume demand.",
               "Staples output is weak, a sign of soft volume demand."),
        growth(c("Agriculture"), "Agriculture credit growth (rural proxy)", "credit:Agriculture",
               "More farm credit supports rural incomes and spending.",
               "Slowing farm credit points to weaker rural demand."),
        growth(c("Personal loans"), "Personal loan growth", "credit:Personal loans",
               "Easier household credit supports discretionary spending.",
               "Slower household credit weighs on spending."),
        band(None if (wpi_last is None or cpi is None) else cpi - wpi_last, "Margin gap: CPI minus WPI", "IN_CPI - IN_WPI",
             None if (wpi_last is None or cpi is None) else f"CPI {_num(cpi)}% vs WPI {_num(wpi_last)}%, gap {_signed(cpi - wpi_last)} pts",
             lambda v: v > 1, lambda v: v < -2,
             "Selling prices are rising faster than input prices, a margin tailwind.",
             "Input (wholesale) prices are running well ahead of retail prices, squeezing margins.",
             "Input and selling prices are moving roughly together."),
        _brent_inr(X, "Cheaper crude lowers packaging and freight costs.",
                   "Costly crude raises packaging, palm-oil-linked and freight costs."),
        price(S("Y:ZW=F"), "Wheat price", "Y:ZW=F",
              "Cheaper wheat eases flour-based input costs (biscuits, staples).",
              "Costlier wheat raises flour-based input costs."),
        sector_breadth(D, "Fast Moving Consumer Goods", "FMCG breadth (Nifty 500)",
                       "Most FMCG stocks are in uptrends.", "Most FMCG stocks are below long-term trend and falling."),
    ]

    out["Autos"] = [
        growth(c("Vehicle loans"), "Vehicle loan growth", "credit:Vehicle loans",
               "Faster vehicle finance points to stronger vehicle sales.",
               "Slower vehicle finance points to weaker sales."),
        growth(S("IN_IIP_CONSUMERDU"), "IIP consumer durables", "IN_IIP_CONSUMERDU",
               "Durables output is strong, including two-wheelers and cars.",
               "Durables output is weak."),
        growth(S("IN_GDP_REAL"), "India real GDP growth", "IN_GDP_REAL",
               "Faster growth lifts incomes and vehicle demand.", "Slower growth weighs on vehicle demand."),
        growth(S("IN_PFCE"), "Real private consumption growth", "IN_PFCE",
               "Households are spending more.", "Household spending is slowing."),
        _brent_inr(X, "Cheaper fuel lowers running costs and supports demand.",
                   "Expensive fuel raises running costs, hurting demand, and lifts plastics and logistics costs."),
        price(S("Y:HG=F"), "Copper price", "Y:HG=F",
              "Cheaper copper lowers wiring and component costs.",
              "Costlier copper raises wiring, EV and component costs."),
        rate(X["india10"], "India 10Y government bond yield", "INDIRLTLT01STM",
             "Falling rates make vehicle loans cheaper.", "Rising rates make vehicle loans costlier."),
        sector_breadth(D, "Automobile and Auto Components", "Auto breadth (Nifty 500)",
                       "Most auto stocks are in uptrends.", "Most auto stocks are below long-term trend and falling."),
    ]

    out["Capital goods and infrastructure"] = [
        growth(S("IN_IIP_CAPITALGOO"), "IIP capital goods", "IN_IIP_CAPITALGOO",
               "Machinery output is rising, a direct read on order execution.",
               "Machinery output is slowing."),
        growth(S("IN_IIP_INFRASTRUC"), "IIP infrastructure and construction goods", "IN_IIP_INFRASTRUC",
               "Construction-material output is rising with project activity.",
               "Construction-material output is slowing."),
        growth(S("IN_GFCF"), "Real investment (GFCF) growth", "IN_GFCF",
               "Investment spending is accelerating, the core driver of order books.",
               "Investment spending is slowing."),
        growth(S("IN_GFCF_SHARE"), "Investment rate (GFCF / GDP)", "IN_GFCF_SHARE",
               "Investment is taking a rising share of the economy.",
               "Investment's share of the economy is falling.", unit="% of GDP"),
        growth(c("Industry"), "Bank credit to industry", "credit:Industry",
               "Companies are borrowing more, usually to fund capex.",
               "Corporate borrowing is slowing, a sign of weaker capex."),
        growth(S("IN_GFCE"), "Real government consumption growth", "IN_GFCE",
               "Government spending is accelerating.", "Government spending is slowing."),
        price(S("Y:HG=F"), "Copper price", "Y:HG=F",
              "Cheaper copper lowers costs for cables, transformers and equipment.",
              "Costlier copper raises costs for cables, transformers and equipment."),
        sector_breadth(D, "Capital Goods", "Capital goods breadth (Nifty 500)",
                       "Most capital goods stocks are in uptrends.",
                       "Most capital goods stocks are below long-term trend and falling."),
    ]

    pay = S("PAYEMS")
    pay3 = float(pay.iloc[-3:].mean()) if pay is not None and len(pay) >= 3 else None
    out["IT services and exports"] = [
        fx(X["usdinr"], "USD/INR", "Y:USDINR=X",
           "A weaker rupee lifts the rupee value of dollar revenue and margins.",
           "A stronger rupee cuts the rupee value of dollar revenue."),
        growth(S("GDPC1"), "US real GDP growth", "GDPC1",
               "A stronger US economy supports tech budgets of the largest client market.",
               "A slowing US economy usually leads clients to cut discretionary tech spend."),
        growth(S("ICSA"), "US initial jobless claims", "ICSA",
               "Falling, below-normal layoffs point to healthy US clients.",
               "Rising layoffs point to clients cutting costs.", unit=" thousand", higher_good=False),
        band(pay3, "US payrolls, 3-month average", "PAYEMS",
             None if pay3 is None else f"{_signed(pay3, 0)}k a month (above 100k good, below 50k bad)",
             lambda v: v > 100, lambda v: v < 50,
             "US hiring is solid, a sign of healthy client demand.",
             "US hiring has stalled, a warning for client spending.",
             "US hiring is moderate."),
        growth(S("BOP_SOFTWARE_YOY"), "Computer services exports growth (BoP)", "BOP_SOFTWARE_YOY",
               "India's software exports are accelerating.", "India's software exports are slowing."),
        trend(S("Y:^IXIC"), "Nasdaq Composite", "Y:^IXIC",
              "A rising Nasdaq signals confident tech spending.", "A falling Nasdaq signals tech budget caution."),
        rate(S("DFF"), "Fed funds rate", "DFF",
             "Fed easing supports US demand and tech budgets.", "Fed tightening squeezes US demand."),
        sector_breadth(D, "Information Technology", "IT breadth (Nifty 500)",
                       "Most IT stocks are in uptrends.", "Most IT stocks are below long-term trend and falling."),
    ]

    brent = X["brent"]
    inv = S("WCESTUS1")
    inv_last, inv_med = (float(inv.iloc[-1]), _median(inv, 5)) if inv is not None else (None, None)
    out["Oil, gas and energy"] = [
        price(brent, "Brent crude (3M move and vs 10Y)", "Y:BZ=F",
              "Lower crude cuts input costs for refiners, oil marketers and gas utilities, the bulk of listed Indian energy.",
              "High crude squeezes oil marketers and city gas firms, the bulk of listed Indian energy (upstream ONGC/Oil India gain).",
              unit="$"),
        price(S("Y:NG=F"), "US natural gas (Henry Hub)", "Y:NG=F",
              "Cheaper gas (and LNG linked to it) lowers costs for gas distributors and fertiliser users.",
              "Costlier gas raises LNG costs for gas distributors.", unit="$"),
        fx(X["usdinr"], "USD/INR (import bill)", "Y:USDINR=X",
           "A stronger rupee cuts the rupee cost of imported crude.",
           "A weaker rupee raises the rupee cost of imported crude.", exporter=False),
        band(None if inv_last is None or inv_med is None else inv_last / inv_med * 100 - 100,
             "US commercial crude stocks vs 5Y median", "WCESTUS1",
             None if inv_last is None or inv_med is None else f"{_num(inv_last)} mn bbl, {_signed(inv_last / inv_med * 100 - 100)}% vs 5Y median (above +3% good, below -3% bad)",
             lambda v: v > 3, lambda v: v < -3,
             "Ample US inventories cap crude prices.", "Tight US inventories keep crude prices firm.",
             "US inventories are near normal."),
        _brent_inr(X, "Crude in rupees is easing, helping marketing margins.",
                   "Crude in rupees is elevated, hurting marketing margins and raising under-recovery risk."),
        trend(S("Y:^CNXENERGY"), "Nifty Energy index", "Y:^CNXENERGY",
              "Energy stocks are in an uptrend.", "Energy stocks are in a downtrend."),
        sector_breadth(D, "Oil Gas & Consumable Fuels", "Oil, gas and fuels breadth (Nifty 500)",
                       "Most energy stocks are in uptrends.", "Most energy stocks are below long-term trend and falling."),
    ]

    out["Metals and mining"] = [
        price(S("Y:HG=F"), "Copper price", "Y:HG=F",
              "Rising or stretched metal prices lift producer realisations.",
              "Slumping metal prices cut producer realisations.", mode="benefit", unit="$"),
        trend(S("Y:^HSI"), "Hang Seng (China demand proxy)", "Y:^HSI",
              "Rising China-linked equities point to firmer metal demand.",
              "Falling China-linked equities point to softer metal demand."),
        trend(S("Y:000001.SS"), "Shanghai Composite", "Y:000001.SS",
              "Mainland equities are rising, a sign of better China sentiment.",
              "Mainland equities are falling, a sign of weaker China sentiment."),
        fx(S("Y:DX-Y.NYB"), "US Dollar Index (DXY)", "Y:DX-Y.NYB",
           "A weaker dollar tends to lift dollar metal prices.",
           "A stronger dollar tends to push dollar metal prices down.", exporter=False, th=3),
        growth(S("IN_IIP"), "India IIP growth", "IN_IIP",
               "Domestic industrial demand for steel and aluminium is rising.",
               "Domestic industrial demand is slowing."),
        growth(S("IN_IIP_INFRASTRUC"), "IIP infrastructure and construction goods", "IN_IIP_INFRASTRUC",
               "Construction demand for steel is rising.", "Construction demand for steel is slowing."),
        trend(S("Y:^CNXMETAL"), "Nifty Metal index", "Y:^CNXMETAL",
              "Metal stocks are in an uptrend.", "Metal stocks are in a downtrend."),
        sector_breadth(D, "Metals & Mining", "Metals and mining breadth (Nifty 500)",
                       "Most metal stocks are in uptrends.", "Most metal stocks are below long-term trend and falling."),
    ]

    repo, rr = X["repo"], None
    if repo is not None and cpi is not None:
        rr = repo - cpi
    out["Real estate and cement"] = [
        growth(c("Housing"), "Housing loan growth", "credit:Housing",
               "Mortgage demand is accelerating.", "Mortgage demand is slowing."),
        growth(c("Commercial real estate"), "Commercial real estate credit", "credit:Commercial real estate",
               "Developer and office financing is expanding.", "Developer financing is slowing."),
        rate(X["india10"], "India 10Y government bond yield", "INDIRLTLT01STM",
             "Falling yields usually lead home-loan rates lower.",
             "Rising yields usually lead home-loan rates higher."),
        band(rr, "Real policy rate (repo minus CPI)", "repo - IN_CPI",
             None if rr is None else f"Repo {_num(repo, 2)}% minus CPI {_num(cpi, 2)}% = {_signed(rr, 2)} pts (below 1 good, above 2 bad)",
             lambda v: v < 1, lambda v: v > 2,
             "Low real rates keep home loans affordable in real terms.",
             "High real rates make home loans expensive in real terms.",
             "Real rates are moderate."),
        _cpi_comfort(X, "Inflation at target opens the door to cheaper home loans.",
                     "Inflation above comfort points to costlier home loans and squeezed buyer budgets."),
        growth(S("IN_IIP_INFRASTRUC"), "IIP infrastructure and construction goods", "IN_IIP_INFRASTRUC",
               "Construction activity (cement demand) is rising.", "Construction activity is slowing."),
        _brent_inr(X, "Cheaper fuel lowers cement kiln and freight costs.",
                   "Costly fuel raises cement kiln, freight and construction costs."),
        sector_breadth(D, "Realty", "Realty breadth (Nifty 500)",
                       "Most realty stocks are in uptrends.", "Most realty stocks are below long-term trend and falling."),
        sector_breadth(D, "Construction Materials", "Cement and building materials breadth (Nifty 500)",
                       "Most cement stocks are in uptrends.", "Most cement stocks are below long-term trend and falling."),
    ]

    health = _cpi_row(D, "Health")
    head = _cpi_row(D, "CPI (General)")
    hgap = health[0] - head[0] if health and head and head[0] is not None else None
    out["Pharma and healthcare"] = [
        fx(X["usdinr"], "USD/INR", "Y:USDINR=X",
           "A weaker rupee lifts the rupee value of US generic exports.",
           "A stronger rupee cuts the rupee value of export revenue."),
        growth(S("CPILFESL"), "US core CPI inflation", "CPILFESL",
               "Firmer US inflation tends to ease the price erosion on US generics.",
               "Cooling US inflation goes with continued generic price erosion."),
        band(hgap, "India health CPI vs headline CPI", "cpi:Health",
             None if hgap is None else f"Health CPI {_num(health[0])}% vs headline {_num(head[0])}%, gap {_signed(hgap)} pts (above 0 good, below -2 bad)",
             lambda v: v > 0, lambda v: v < -2,
             "Healthcare prices are rising faster than the basket, a sign of pricing power.",
             "Healthcare prices are lagging the basket badly, a sign of weak pricing power (price controls).",
             "Healthcare prices are slightly behind the overall basket."),
        price(S("Y:CNYINR=X"), "CNY/INR (China API import costs)", "Y:CNYINR=X",
              "A cheaper yuan lowers the cost of APIs imported from China.",
              "A costlier yuan raises the cost of APIs imported from China."),
        _brent_inr(X, "Cheaper crude lowers solvent and packaging costs.",
                   "Costly crude raises solvent, chemical and packaging costs."),
        trend(S("Y:^CNXPHARMA"), "Nifty Pharma index", "Y:^CNXPHARMA",
              "Pharma stocks are in an uptrend.", "Pharma stocks are in a downtrend."),
        sector_breadth(D, "Healthcare", "Healthcare breadth (Nifty 500)",
                       "Most healthcare stocks are in uptrends.", "Most healthcare stocks are below long-term trend and falling."),
    ]

    # DII = mutual funds + insurance (NSE investor-category monthly flows)
    cat = (D.get("category") or {}).get("monthly") or {}
    dii = None
    if cat.get("dates") and cat.get("Mutual funds"):
        ins = cat.get("Insurance") or [0] * len(cat["dates"])
        dii = _clean([(d, (m or 0) + (i or 0)) for d, m, i in zip(cat["dates"], cat["Mutual funds"], ins)])
    b = D.get("breadth") or {}
    a200 = next((v for v in reversed(b.get("above200") or []) if v is not None), None)
    mc = S("IN_MCAP_GDP")
    mcz = _z(mc, 10) if mc is not None else None
    out["Market liquidity and flows"] = [
        flow(S("FPI_EQUITY"), "FPI net equity flows", "FPI_EQUITY",
             "Foreign investors are net buyers.", "Foreign investors are net sellers."),
        flow(dii, "DII flows (mutual funds and insurance)", "category:Mutual funds+Insurance",
             "Domestic institutions are absorbing supply.", "Domestic institutions are net sellers."),
        band(a200, "Nifty 500 stocks above 200-day average", "breadth:above200",
             None if a200 is None else f"{_num(a200, 0)}% of stocks (above 60% good, below 40% bad)",
             lambda v: v > 60, lambda v: v < 40,
             "Most stocks are in long-term uptrends.", "Most stocks are below their long-term trend.",
             "Participation is middling."),
        _india_vix(X, "Calm markets encourage risk-taking.", "High volatility signals stress."),
        band(mcz, "India market cap to GDP", "IN_MCAP_GDP",
             None if mcz is None else f"{_num(float(mc.iloc[-1]), 0)}% of GDP, {_signed(mcz)} sigma vs its median (below -1 good, above +1 bad)",
             lambda v: v < -1, lambda v: v > 1,
             "Valuations are cheap relative to the economy.", "Valuations are stretched relative to the economy.",
             "Valuations are near their usual range."),
        trend(S("R:NIFTY_GOLD"), "Nifty 50 / gold ratio", "R:NIFTY_GOLD",
              "Equities are beating gold.", "Gold is beating equities, a sign of risk aversion.", d=3),
        trend(S("Y:^NSEI"), "Nifty 50", "Y:^NSEI",
              "The market is in an uptrend.", "The market is in a downtrend."),
    ]

    curve = S("T10Y2Y")
    cv = float(curve.iloc[-1]) if curve is not None else None
    mv = S("Y:^MOVE")
    vx = S("Y:^VIX")
    nl = S("NETLIQ")
    out["Global risk"] = [
        spread(S("BAMLH0A0HYM2"), "US high-yield credit spread", "BAMLH0A0HYM2",
               "Tight and stable credit spreads mean calm global risk appetite.",
               "Widening spreads signal credit stress that spills into EM equities."),
        band(None if mv is None else float(mv.iloc[-1]), "MOVE (bond volatility)", "Y:^MOVE",
             None if mv is None else f"{_num(float(mv.iloc[-1]))} (below 90 good, above 120 bad)",
             lambda v: v < 90, lambda v: v > 120,
             "Calm bond markets support risk assets.", "Turbulent bond markets drain risk appetite.",
             "Bond volatility is elevated but not extreme."),
        band(None if vx is None else float(vx.iloc[-1]), "VIX (S&P 500 volatility)", "Y:^VIX",
             None if vx is None else f"{_num(float(vx.iloc[-1]))} (below 15 good, above 25 bad)",
             lambda v: v < 15, lambda v: v > 25,
             "Low equity volatility supports flows to EM.", "High equity volatility drives money out of EM.",
             "Volatility is in a normal range."),
        fx(S("Y:DX-Y.NYB"), "US Dollar Index (DXY)", "Y:DX-Y.NYB",
           "A weaker dollar tends to push money into emerging markets.",
           "A stronger dollar tends to pull money out of emerging markets.", exporter=False, th=3),
        rate(S("DGS10"), "US 10Y Treasury yield", "DGS10",
             "Falling US yields make EM assets relatively more attractive.",
             "Rising US yields pull capital back to the US."),
        band(cv, "US yield curve (10Y minus 2Y)", "T10Y2Y",
             None if cv is None else f"{_signed(cv * 100, 0)} bp (above 50 bp good, below 0 bad)",
             lambda v: v > 0.5, lambda v: v < 0,
             "A normal upward-sloping curve signals no recession fear.",
             "An inverted curve has historically preceded US recessions.",
             "The curve is flat, a late-cycle signal."),
        # fx rule reused as a 3M % move test: +/-3%
        fx(nl, "US net liquidity (Fed assets minus TGA and RRP)", "NETLIQ",
           "Dollar liquidity is expanding (up more than 3% in 3M), supportive for risk assets.",
           "Dollar liquidity is draining (down more than 3% in 3M), a headwind for risk assets.", th=3, unit="USD bn "),
        spread(S("BAMLEMCBPIOAS"), "Emerging market corporate spread", "BAMLEMCBPIOAS",
               "EM credit is calm.", "EM credit stress is rising."),
    ]
    return out


# ---------------------------------------------------------------- main entry
def _verdict(p, n, total):
    if total and n >= total / 2 and n > p:
        return "Mostly negative"
    if total and p >= total / 2 and p > n:
        return "Mostly supportive"
    return "Mixed"


def compute_scorecard(D) -> list:
    X = _shared(D)
    res = []
    for sector, inds in _sectors(D, X).items():
        inds = [i for i in inds if i and i.get("value")]
        if not inds:
            continue
        p = sum(1 for i in inds if i["signal"] > 0)
        n = sum(1 for i in inds if i["signal"] < 0)
        t = len(inds)
        res.append({"sector": sector, "positives": p, "negatives": n, "neutral": t - p - n, "total": t,
                    "net": p - n, "verdict": _verdict(p, n, t), "indicators": inds})
    res.sort(key=lambda r: (-r["net"], -r["positives"] / r["total"], r["sector"]))
    return res


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    sys.stdout.reconfigure(encoding="utf-8")
    D = json.loads((Path(__file__).parent / "data" / "macro.json").read_text(encoding="utf-8"))
    for r in compute_scorecard(D):
        print(f"\n{r['sector']}: {r['positives']}+ {r['negatives']}- {r['neutral']}= of {r['total']} -> {r['verdict']}")
        for i in r["indicators"]:
            print(f"   {i['signal']:+d}  {i['name']}: {i['value']}")
