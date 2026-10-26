// ---------- chart insights and company impact (the two toggles under every chart)
// Everything in "What history says" is computed live from the chart's own data; the written context comes from data/notes.json.
const DAY = 864e5;
const MONTHS3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const mon = t => { const d = new Date(t); return `${MONTHS3[d.getMonth()]} ${d.getFullYear()}`; };
const isRate = unit => /%|pts|bp|ratio|of GDP|σ/.test(unit || "") && !/^price|index/.test(unit || "");
function unitOf(spec) { return typeof spec === "string" ? (D.series[spec] ? D.series[spec].unit : "") : (spec.unit || ""); }
function monthly(pts) { // last value per calendar month -> [[t, v], ...]
  const m = new Map();
  for (const [t, v] of pts || []) if (v != null && isFinite(v)) { const d = new Date(t); m.set(d.getFullYear() * 12 + d.getMonth(), [t, v]); }
  return [...m.values()].sort((a, b) => a[0] - b[0]);
}
function valueAt(pts, t) { let lo = 0, hi = pts.length - 1, best = null; while (lo <= hi) { const mid = (lo + hi) >> 1; if (pts[mid][0] <= t) { best = pts[mid]; lo = mid + 1; } else hi = mid - 1; } return best && t - best[0] < 45 * DAY ? best[1] : null; }
function chgOf(a, b, rate) { return a == null || b == null ? null : rate ? a - b : (b ? (a / b - 1) * 100 : null); }
const fmtChg = (v, rate) => v == null ? "n/a" : `${v > 0 ? "+" : ""}${fmt(v, rate ? 2 : 1)}${rate ? " pts" : "%"}`;
const ord = n => { const k = Math.round(n), s = ["th", "st", "nd", "rd"], v = k % 100; return k + (s[(v - 20) % 10] || s[v] || s[0]); };
const median = a => { const s = a.filter(x => x != null && isFinite(x)).sort((x, y) => x - y); return s.length ? (s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2) : null; };
function corr(a, b) {
  const n = a.length; if (n < 24) return null;
  const ma = a.reduce((x, y) => x + y, 0) / n, mb = b.reduce((x, y) => x + y, 0) / n;
  let sab = 0, saa = 0, sbb = 0; for (let i = 0; i < n; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) ** 2; sbb += (b[i] - mb) ** 2; }
  return saa && sbb ? sab / Math.sqrt(saa * sbb) : null;
}
const PANEL = [["Y:^BSESN", "Sensex", false], ["Y:^GSPC", "S&P 500", false], ["Y:USDINR=X", "USD/INR", false], ["DCOILBRENTEU", "Brent", false],
  ["Y:GC=F", "Gold", false], ["DGS10", "US 10Y yield", true], ["Y:DX-Y.NYB", "Dollar index", false], ["IN_CPI", "India CPI", true]];
function changesMonthly(pts, rate) { const m = monthly(pts); const out = new Map(); for (let i = 1; i < m.length; i++) { const c = chgOf(m[i][1], m[i - 1][1], rate); if (c != null && isFinite(c)) { const d = new Date(m[i][0]); out.set(d.getFullYear() * 12 + d.getMonth(), c); } } return out; }

function analyse(spec) {
  const pts = (get(spec) || []).filter(p => p[1] != null && isFinite(p[1]) && p[0] <= Date.now());
  if (pts.length < 24) return null;
  const unit = unitOf(spec), rate = isRate(unit), name = nameOf(spec);
  const last = pts[pts.length - 1], now = last[1], tNow = last[0];
  const vals = pts.map(p => p[1]), sorted = [...vals].sort((a, b) => a - b);
  const pct = sorted.filter(v => v < now).length / vals.length * 100, from = new Date(pts[0][0]).getFullYear();
  const med = median(vals), mean = vals.reduce((a, b) => a + b, 0) / vals.length, sd = Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length);
  const z = sd ? (now - med) / sd : 0, out = [];
  const lvl = v => `${smart(v)}${/%/.test(unit) && !/YoY/.test(unit) ? "%" : ""}`;
  out.push(`<b>${name}</b> is at <b>${lvl(now)}</b> (${unit}, ${mon(tNow)}): the <b>${ord(pct)} percentile</b> of all readings since ${from}, ${z >= 0 ? "+" : ""}${fmt(z, 1)}σ from the median of ${lvl(med)}.`);
  // last time at this level
  const high = pct >= 50, older = pts.filter(p => p[0] < tNow - 90 * DAY);
  const prior = [...older].reverse().find(p => high ? p[1] >= now : p[1] <= now);
  if (prior) out.push(`The last time it was ${high ? "this high or higher" : "this low or lower"} was <b>${mon(prior[0])}</b>${prior[0] < tNow - 3 * 365 * DAY ? ", more than three years ago" : ""}.`);
  else if (older.length) out.push(`This is the <b>${high ? "highest" : "lowest"} reading in the whole history</b> since ${from}.`);
  // what happened next from similar levels
  const band = rate ? Math.max(sd * 0.25, Math.abs(now) * 0.03) : Math.abs(now) * 0.05;
  const eps = []; let lastEp = -Infinity;
  for (const [t, v] of pts) if (t < tNow - 365 * DAY && Math.abs(v - now) <= band && t - lastEp > 180 * DAY) { eps.push(t); lastEp = t; }
  if (eps.length >= 3) {
    const f = h => eps.map(t => chgOf(valueAt(pts, t + h * 30.4 * DAY), valueAt(pts, t), rate)).filter(x => x != null);
    const f12 = f(12), f6 = f(6), sx = get("Y:^BSESN") || [];
    const sxR = eps.map(t => chgOf(valueAt(sx, t + 365 * DAY), valueAt(sx, t), false)).filter(x => x != null);
    const down = f12.filter(x => x < 0).length;
    out.push(`It has been near today's level (±${rate ? fmt(band, 2) : "5%"}) in <b>${eps.length} earlier episodes</b> (${eps.slice(-4).map(mon).join(", ")}${eps.length > 4 ? ", …" : ""}). Six months later it had moved a median <b>${fmtChg(median(f6), rate)}</b>; twelve months later <b>${fmtChg(median(f12), rate)}</b>, ${down > f12.length / 2 ? `lower in <b>${down} of ${f12.length}</b> cases` : `higher in <b>${f12.length - down} of ${f12.length}</b> cases`}.`
      + (sxR.length >= 3 ? ` The Sensex returned a median <b>${fmtChg(median(sxR), false)}</b> over the following year (${sxR.filter(x => x > 0).length} of ${sxR.length} positive).` : ""));
  }
  // this year's change ranked against every past year over the same calendar window
  const yr = new Date(tNow).getFullYear(), doy = tNow - new Date(yr, 0, 1).getTime();
  if (doy > 25 * DAY) {
    const ytd = y => chgOf(valueAt(pts, new Date(y, 0, 1).getTime() + doy), valueAt(pts, new Date(y - 1, 11, 31).getTime()), rate);
    const cur = ytd(yr), past = [];
    for (let y = from + 1; y < yr; y++) { const c = ytd(y); if (c != null) past.push([y, c]); }
    if (cur != null && past.length >= 5) {
      const worse = past.filter(([, c]) => c <= cur), better = past.filter(([, c]) => c >= cur);
      if (cur < 0 && worse.length <= past.length * 0.25) {
        const lastWorse = worse.length ? Math.max(...worse.map(([y]) => y)) : null;
        out.push(`So far this year it is <b>${fmtChg(cur, rate)}</b>: ${lastWorse ? `the weakest start to a year since <b>${lastWorse}</b>` : `the weakest start to any year since ${from + 1}`} (ranked ${past.length + 1 - worse.length} of ${past.length + 1}).`);
      } else if (cur > 0 && better.length <= past.length * 0.25) {
        const lastBetter = better.length ? Math.max(...better.map(([y]) => y)) : null;
        out.push(`So far this year it is <b>${fmtChg(cur, rate)}</b>: ${lastBetter ? `the strongest start to a year since <b>${lastBetter}</b>` : `the strongest start to any year since ${from + 1}`}.`);
      } else out.push(`So far this year it is ${fmtChg(cur, rate)}, a middling move against the same window in past years (median ${fmtChg(median(past.map(([, c]) => c)), rate)}).`);
    }
  }
  // correlations of monthly changes over the last 10 years
  const mine = changesMonthly(pts.filter(p => p[0] > tNow - 10 * 365 * DAY), rate), cs = [];
  for (const [id, label, r] of PANEL) {
    if (id === spec || !D.series[id] || name === nameOf(id)) continue;
    const other = changesMonthly(get(id), r), a = [], b = [];
    for (const [k, v] of mine) if (other.has(k)) { a.push(v); b.push(other.get(k)); }
    const c = corr(a, b); if (c != null && Math.abs(c) < 0.97) cs.push([label, c]);  // skip near-duplicates (Nifty vs Sensex)
  }
  cs.sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]));
  const strong = cs.filter(([, c]) => Math.abs(c) >= 0.25).slice(0, 3);
  if (strong.length) out.push(`Over the last 10 years its monthly moves have gone ${strong.map(([l, c]) => `<b>${c > 0 ? "with" : "against"} ${l}</b> (${fmt(c, 2)})`).join(", ")}.`);
  else if (cs.length) out.push(`Its monthly moves have had little link to the Sensex, rupee, oil, gold or US yields over the last 10 years (all correlations under 0.25).`);
  // does a 3-month rise in this indicator lead the Sensex?
  const sx = monthly(get("Y:^BSESN") || []), m = monthly(pts);
  if (sx.length > 60 && m.length > 40 && !/Sensex|Nifty/.test(name)) {
    const up = [], dn = [];
    for (let i = 3; i < m.length; i++) {
      const c = chgOf(m[i][1], m[i - 3][1], rate), t = m[i][0];
      const fwd = chgOf(valueAt(sx, t + 91 * DAY), valueAt(sx, t), false);
      if (c == null || fwd == null) continue; (c > 0 ? up : dn).push(fwd);
    }
    if (up.length > 12 && dn.length > 12) {
      const mu = median(up), md = median(dn);
      if (Math.abs(mu - md) >= 1.5) out.push(`When it rose over three months, the Sensex returned a median ${fmtChg(mu, false)} in the next three months, against ${fmtChg(md, false)} after it fell: historically a <b>${mu > md ? "supportive" : "negative"}</b> signal for Indian equities.`);
    }
  }
  return out;
}

function noteFor(cfg, title) {
  const N = D.notes || {}, C = N.charts || {}, S = N.series || {};
  if (C[title]) return C[title];
  const pre = Object.keys(C).find(k => title && title.startsWith(k));  // titles with live numbers in them
  if (pre) return C[pre];
  for (const s of cfg && cfg.s ? cfg.s : []) {
    const id = typeof s === "string" ? s : s.a; if (id && S[id]) return S[id];
  }
  return null;
}

function insightsHTML(cfg, title) {
  const note = noteFor(cfg, title), parts = [];
  const specs = cfg && cfg.s ? cfg.s.filter(s => !(typeof s !== "string" && s.d === "pts" && (!s.pts || s.pts.length < 24))) : [];
  const facts = specs.length ? analyse(specs[0]) : null;
  if (facts && facts.length) parts.push(`<div class="ix-h">What history says</div><ul>${facts.map(f => `<li>${f}</li>`).join("")}</ul>`);
  if (specs.length > 1) {
    const extra = specs.slice(1, 3).map(s => { const a = analyse(s); return a ? a[0] : null; }).filter(Boolean);
    if (extra.length) parts.push(`<ul>${extra.map(f => `<li>${f}</li>`).join("")}</ul>`);
  }
  if (note) {
    if (note.means) parts.push(`<div class="ix-h">What it means</div><p>${note.means}</p>`);
    if (note.watch) parts.push(`<div class="ix-h">What to watch</div><p>${note.watch}</p>`);
    if (note.links) parts.push(`<div class="ix-h">How it links to other indicators</div><p>${note.links}</p>`);
  }
  return parts.join("") || `<p>No history-based insight available for this chart.</p>`;
}

function companiesHTML(cfg, title) {
  const note = noteFor(cfg, title), list = note && note.companies ? note.companies : [];
  if (!list.length) return `<p>No direct listed-company link for this indicator.</p>`;
  const R = (D.breadth && D.breadth.stocks && D.breadth.stocks.returns) || {};
  const pill = d => d === "+" ? `<span class="chip ix-pos">&#9650; Positive</span>` : d === "-" ? `<span class="chip ix-neg">&#9660; Negative</span>` : `<span class="chip">&#9670; Mixed</span>`;
  const r = v => v == null ? "<td>n/a</td>" : `<td class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${fmt(v, 1)}%</td>`;
  return `<p class="ix-note">How a rise in this indicator tends to affect these Indian listed companies. Sensitivities, not buy or sell calls. Returns are live from the latest refresh.</p>
  <div class="tw"><table class="ix-t"><thead><tr><th>Company</th><th>Impact if it rises</th><th>Why</th><th>1M</th><th>3M</th><th>1Y</th></tr></thead><tbody>${list.map(c => {
    const x = R[c.sym] || [];
    return `<tr><td><b>${c.sym}</b>${x[3] ? `<br><span class="u">${x[3]}</span>` : ""}</td><td>${pill(c.dir)}</td><td style="text-align:left;white-space:normal;min-width:200px">${c.why}</td>${r(x[0])}${r(x[1])}${r(x[2])}</tr>`;
  }).join("")}</tbody></table></div>`;
}

// adds the two toggles to a chart card (called from card() and customCard())
function addToggles(div, cfg, title) {
  const bar = document.createElement("div");
  bar.className = "ix-bar";
  bar.innerHTML = `<button type="button" class="ix-btn" data-k="ins" aria-expanded="false">Insights</button><button type="button" class="ix-btn" data-k="co" aria-expanded="false">Indian companies</button><div class="ix-body" hidden></div>`;
  div.appendChild(bar);
  const body = bar.querySelector(".ix-body");
  bar.querySelectorAll(".ix-btn").forEach(b => b.onclick = () => {
    const open = b.getAttribute("aria-expanded") === "true";
    bar.querySelectorAll(".ix-btn").forEach(x => x.setAttribute("aria-expanded", "false"));
    if (open) { body.hidden = true; return; }
    b.setAttribute("aria-expanded", "true");
    body.innerHTML = b.dataset.k === "ins" ? insightsHTML(cfg, title) : companiesHTML(cfg, title);
    body.hidden = false;
  });
}
