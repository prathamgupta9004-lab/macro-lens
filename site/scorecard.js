// ---------- sector scorecard (data from scorecard.py, D.scorecard)
function scStyle() {
  if (document.getElementById("sc-style")) return;
  const st = document.createElement("style");
  st.id = "sc-style";
  st.textContent = `
.sc-list{display:grid;gap:8px}
.sc-row{background:var(--card);border:1px solid var(--line);border-radius:12px}
.sc-head{display:grid;grid-template-columns:28px minmax(160px,1.3fr) minmax(140px,2fr) auto auto;gap:6px 14px;align-items:center;width:100%;background:none;border:0;padding:10px 14px;font:inherit;color:var(--ink);text-align:left;cursor:pointer;border-radius:12px}
.sc-head:hover{background:var(--bg)}
.sc-rank{color:var(--faint);font-size:12px;font-variant-numeric:tabular-nums}
.sc-name{font-weight:500;color:var(--head)}
.sc-count{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.sc-bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--grid);min-width:80px}
.sc-bar i{display:block;height:100%}
.sc-bar i.p{background:var(--s1)}.sc-bar i.z{background:var(--grid)}.sc-bar i.n{background:var(--s3)}
.sc-bar.sm{height:6px;border-radius:3px;min-width:60px}
.sc-car{color:var(--faint);font-size:11px;transition:transform .15s}
.sc-row.open .sc-car{transform:rotate(90deg)}
.sc-body{display:none;padding:0 14px 12px}
.sc-row.open .sc-body{display:block}
.sc-ind{display:grid;grid-template-columns:86px minmax(170px,1fr) minmax(200px,1.4fr);gap:4px 14px;padding:8px 0;border-top:1px solid var(--line);font-size:13px;align-items:baseline}
.sc-ind .nm{font-weight:500}.sc-ind .vl{color:var(--muted);font-variant-numeric:tabular-nums}.sc-ind .wy{grid-column:2/-1;color:var(--muted);font-size:12px}
.sc-pill{display:inline-block;font-size:11px;font-weight:500;border-radius:999px;padding:1px 8px;white-space:nowrap}
.sc-legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin:0 0 10px}
.sc-legend span{display:inline-flex;align-items:center;gap:6px}
.sc-legend b{display:inline-block;width:12px;height:8px;border-radius:2px}
.sc-mini{display:grid;gap:6px}
.sc-mrow{display:grid;grid-template-columns:minmax(120px,1.2fr) minmax(60px,1fr) auto;gap:10px;align-items:center;font-size:12px}
.sc-mrow .t{color:var(--ink)}.sc-mrow .c{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
@media (max-width:640px){
  .sc-head{grid-template-columns:22px 1fr auto;}
  .sc-head .sc-bar{grid-column:2/-1;grid-row:2}
  .sc-head .sc-count{grid-column:2/-1;grid-row:3}
  .sc-ind{grid-template-columns:1fr}.sc-ind .wy{grid-column:1}
}`;
  document.head.appendChild(st);
}

function scEsc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])); }

function scBar(r, small) {
  const t = r.total || 1, w = v => (v / t * 100).toFixed(1) + "%";
  return `<span class="sc-bar${small ? " sm" : ""}" role="img" aria-label="${r.positives} positive, ${r.neutral} neutral, ${r.negatives} negative of ${r.total}">` +
    `<i class="p" style="width:${w(r.positives)}"></i><i class="z" style="width:${w(r.neutral)}"></i><i class="n" style="width:${w(r.negatives)}"></i></span>`;
}

function scVerdict(v) {
  const cls = v === "Mostly supportive" ? "good" : v === "Mostly negative" ? "bad" : "warn";
  return `<span class="sc-pill state ${cls}">${scEsc(v)}</span>`;
}

function scSignal(s) {
  return s > 0 ? `<span class="sc-pill state good">&#9650; Positive</span>`
    : s < 0 ? `<span class="sc-pill state bad">&#9660; Negative</span>`
    : `<span class="sc-pill chip">&#9679; Neutral</span>`;
}

function scorecardPanel(p) {
  if (!p) return;
  scStyle();
  const S = (typeof D !== "undefined" && D && Array.isArray(D.scorecard)) ? D.scorecard : null;
  const head = `<div class="bar"><div><h2>Sector scorecard</h2><p class="lede">For each sector we check 7 to 10 macro indicators that matter to it and mark each one positive, negative or neutral using fixed rules. Growth counts as positive only when it is both rising over 3 months and above its 5-year median. Input costs count against a sector when up more than 10% in 3 months or more than 1 standard deviation above their 10-year median. Yields count against borrowers when up more than 25 bp in 3 months. Flows count by the sign of the last 3 months. Sectors are ranked by positives minus negatives. These are observations from the data, not buy or sell calls.</p></div></div>`;
  if (!S || !S.length) { p.innerHTML = head + `<p class="lede">The sector scorecard was not available in this run.</p>`; return; }
  const legend = `<div class="sc-legend"><span><b style="background:var(--s1)"></b>Positive</span><span><b style="background:var(--grid);border:1px solid var(--line)"></b>Neutral</span><span><b style="background:var(--s3)"></b>Negative</span><span>Click a sector to see each indicator.</span></div>`;
  const rows = S.map((r, i) => {
    const inds = (r.indicators || []).slice().sort((a, b) => a.signal - b.signal).map(x =>
      `<div class="sc-ind"><div>${scSignal(x.signal)}</div><div class="nm">${scEsc(x.name)}</div><div class="vl">${scEsc(x.value)}</div><div class="wy">${scEsc(x.why)}</div></div>`).join("");
    return `<div class="sc-row"><button type="button" class="sc-head" aria-expanded="false">
      <span class="sc-rank">${i + 1}</span><span class="sc-name">${scEsc(r.sector)}</span>${scBar(r)}
      <span class="sc-count">${r.positives} of ${r.total} positive, ${r.negatives} negative</span>
      <span>${scVerdict(r.verdict)} <span class="sc-car" aria-hidden="true">&#9654;</span></span></button>
      <div class="sc-body">${inds}</div></div>`;
  }).join("");
  p.innerHTML = head + legend + `<div class="sc-list">${rows}</div>` +
    `<p class="foot">Sources as listed on the other tabs (RBI, MOSPI, NSE, NSDL, FRED, Yahoo Finance). Indicators with missing data are left out, so totals differ by sector. Sector breadth uses NSE industry groups within the Nifty 500.</p>`;
  p.querySelectorAll(".sc-head").forEach(b => b.addEventListener("click", () => {
    const row = b.parentElement, open = !row.classList.contains("open");
    row.classList.toggle("open", open); b.setAttribute("aria-expanded", open);
  }));
}

// compact summary for the snapshot tab: sector, small stacked bar, "7/10 negative"
function scorecardMini() {
  scStyle();
  const S = (typeof D !== "undefined" && D && Array.isArray(D.scorecard)) ? D.scorecard : null;
  if (!S || !S.length) return `<p class="lede">Sector scorecard not available.</p>`;
  return `<div class="sc-mini">` + S.map(r => {
    const txt = r.negatives > r.positives ? `${r.negatives}/${r.total} negative`
      : r.positives > r.negatives ? `${r.positives}/${r.total} positive`
      : `${r.positives}/${r.total} positive, ${r.negatives} negative`;
    return `<div class="sc-mrow" title="${scEsc(r.verdict)}"><span class="t">${scEsc(r.sector)}</span>${scBar(r, true)}<span class="c">${txt}</span></div>`;
  }).join("") + `</div>`;
}
