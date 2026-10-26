// ---------- Commodities tab: TradingEconomics-style table, selected-commodity chart with history insights, small multiples
function commodityPanel(p) {
  const C = D.commodities;
  const head = `<div class="bar"><div><h2>Commodities</h2><p class="lede">Energy, metals, crops and livestock futures, plus rupee prices for gold, silver and Brent. Click a row to chart it and read what its own history says about today's level. These are observations from the data, not buy or sell calls.</p></div></div>`;
  if (!C || !C.items || !C.items.length) { p.innerHTML = head + `<p class="lede">Commodity data was not available in this run.</p>`; return; }
  const items = C.items, by = Object.fromEntries(items.map(x => [x.sym, x]));
  let sel = by["BZ=F"] ? "BZ=F" : items[0].sym, view = "w";
  const VIEWS = [["w", "Weekly", "daily", 1, "Daily closes, last 1 year"], ["m", "Monthly", "weekly", 5, "Weekly closes, last 5 years"], ["y", "Yearly", "monthly", 99, "Month-end closes, full history"]];
  const groups = (C.groups || [...new Set(items.map(x => x.group))]).filter(g => items.some(x => x.group === g));
  const T = pts => (pts || []).map(([d, v]) => [new Date(d).getTime(), v]);
  const dstr = d => new Date(d).toLocaleDateString("en-GB", {day: "2-digit", month: "short", year: "numeric"});
  p.innerHTML = head + `<div class="tw"><table id="cmT"></table></div><div class="grid" id="cmG" style="margin-top:14px"></div>
  <h4 class="sec">All commodities, last 5 years (weekly closes)</h4><p class="lede" style="margin:-4px 0 8px">Shaded band = 10-year median ±1σ. Click a chart to open it above.</p>
  <div id="cmM" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(min(230px,100%),1fr));gap:10px"></div>
  <p class="src" style="margin-top:8px">Source: ${C.source || "Yahoo Finance"}${C.generated ? ` · fetched ${C.generated}` : ""}</p>`;

  const LIM = {d1: 3, w1: 5, m1: 10, ytd: 25, y1: 30};
  const pc = (v, k) => `<td style="${heatStyle(v, 0, LIM[k], true)}">${v == null ? "n/a" : (v > 0 ? "+" : "") + fmt(v, 1) + "%"}</td>`;
  function table() {
    let h = `<thead><tr><th>Commodity</th><th>Price</th><th>Unit</th><th>Day</th><th>Week</th><th>Month</th><th>YTD</th><th>YoY</th><th>52W range</th><th>26W trend</th></tr></thead><tbody>`;
    for (const g of groups) {
      h += `<tr class="grp"><td colspan="10">${g}</td></tr>`;
      for (const x of items.filter(i => i.group === g)) {
        const c = x.changes || {}, on = x.sym === sel;
        h += `<tr data-sym="${x.sym}" style="cursor:pointer${on ? ";background:var(--tint)" : ""}" aria-selected="${on}"><td style="${on ? "color:var(--brand);font-weight:500" : ""}">${x.name}</td><td>${smart(x.last)}</td><td style="text-align:left;color:var(--muted);font-size:12px">${x.unit}</td>
        ${pc(c.d1, "d1")}${pc(c.w1, "w1")}${pc(c.m1, "m1")}${pc(c.ytd, "ytd")}${pc(c.y1, "y1")}
        <td><span class="rng" title="52W low ${smart(x.lo52)}, high ${smart(x.hi52)}: ${fmt(x.pos52, 0)}% of range"><i style="left:calc(${x.pos52 || 0}% - 2px)"></i></span></td><td>${spark((x.weekly || []).slice(-26).map(q => q[1]))}</td></tr>`;
      }
    }
    const t = $("#cmT", p); t.innerHTML = h + "</tbody>";
    t.querySelectorAll("tr[data-sym]").forEach(tr => tr.onclick = () => pick(tr.dataset.sym));
  }
  function pick(sym, scroll) {
    sel = sym; table(); main._card.custom(main._card.el);
    if (scroll) main.scrollIntoView({behavior: "smooth", block: "start"});
  }
  // band lines: 10-year median and ±1σ from the fetch step, the same benchmark the insight sentences use
  function band(o, s, x, vals) {
    if (x.med10 == null || !x.sd10) return;
    const f = css("--faint");
    s.markLine = {silent: true, symbol: "none", label: {show: true, position: "insideEndTop", color: f, fontSize: 10, formatter: q => q.name},
      data: [[x.med10, "10Y median", "solid"], [x.med10 + x.sd10, "+1σ", "dashed"], [x.med10 - x.sd10, "-1σ", "dashed"]].map(([y, name, type]) => ({yAxis: y, name, lineStyle: {type, color: f, width: 1}}))};
    s.markArea = {silent: true, itemStyle: {color: css("--tint"), opacity: .55}, data: [[{yAxis: x.med10 - x.sd10}, {yAxis: x.med10 + x.sd10}]]};
    if (vals.length) { o.yAxis.min = +Math.min(...vals, x.med10 - x.sd10).toPrecision(3); o.yAxis.max = +Math.max(...vals, x.med10 + x.sd10).toPrecision(3); }
  }

  // ---- selected commodity: wide chart card with view toggle and insights
  const main = customCard("", " ", C.source || "Yahoo Finance", 340, (ch, div) => {
    const x = by[sel], v = VIEWS.find(q => q[0] === view), c = x.changes || {}, st = x.stats || {};
    $("h3", div).textContent = x.name + (x.proxy ? " (ETF price used as a proxy)" : "");
    const ctl = $(".ctl", div);
    if (!ctl.firstChild) {
      ctl.innerHTML = `<div class="ranges" role="group" aria-label="Chart view" style="display:inline-flex;margin:4px 0 6px">${VIEWS.map(([k, l]) => `<button type="button" data-v="${k}">${l}</button>`).join("")}</div>`;
      ctl.querySelectorAll("button").forEach(b => b.onclick = () => { view = b.dataset.v; main._card.custom(main._card.el); });
    }
    ctl.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", b.dataset.v === view));
    const zc = st.z10 == null ? "" : `<span class="chip ${Math.abs(st.z10) >= 2 ? "hot" : Math.abs(st.z10) >= 1 ? "warm" : ""}">${st.z10 > 0 ? "+" : ""}${fmt(st.z10, 1)}σ vs 10Y median · ${fmt(st.pct10, 0)}th percentile (10Y)</span>`;
    $(".why", div).innerHTML = `<span class="kv" style="margin:0"><span class="v">${smart(x.last)}</span><span class="u">${x.unit} · ${dstr(x.last_date)}</span>${c.y1 == null ? "" : `<span class="chip ${c.y1 > 0 ? "up" : c.y1 < 0 ? "down" : ""}">${c.y1 > 0 ? "+" : ""}${fmt(c.y1, 1)}% vs 1Y ago</span>`}${zc}</span>`;
    let pts = T(x[v[2]]);
    if (v[3] < 99) pts = clip(pts, v[3]);
    const o = baseOpt(); o.grid.top = 10;
    const s = {name: x.name, type: "line", data: pts, showSymbol: false, lineStyle: {width: 2, color: css("--s1")}, itemStyle: {color: css("--s1")}};
    band(o, s, x, pts.map(q => q[1]));
    o.series = [s]; ch.setOption(o, true);
    $(".src", div).textContent = `Source: ${C.source || "Yahoo Finance"} · ${v[4]} · shaded band = 10-year median ±1σ`;
    const ins = (x.insights || []).map(t => `<li style="margin:2px 0">${t}</li>`).join("");
    $(".cmIns", div).innerHTML = `<div class="ins"><span class="tag">What the history says</span>${ins ? `<ul style="margin:2px 0 0;padding-left:18px">${ins}</ul>` : `<div>Not enough history for insights.</div>`}</div>
      <div class="ins"><span class="tag">Why it matters for India</span><div>${x.india || "No direct India link noted."}</div>
      <div style="margin-top:8px;color:var(--muted);font-size:12px">52-week range ${smart(x.lo52)} to ${smart(x.hi52)} · 5Y change ${c.y5 == null ? "n/a" : (c.y5 > 0 ? "+" : "") + fmt(c.y5, 1) + "%"} · ${st.drawdown == null ? "" : `${fmt(st.drawdown, 1)}% from all-time high · `}data since ${(x.since || "").slice(0, 4)}</div></div>`;
    table();
  }, true);
  const box = document.createElement("div"); box.className = "cmIns";
  box.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:10px;margin:10px 0 6px";
  main.insertBefore(box, $(".src", main));
  $("#cmG", p).appendChild(main);

  // ---- small multiples
  const mg = $("#cmM", p);
  for (const x of items) {
    const div = document.createElement("div"); div.className = "card"; div.style.cssText = "padding:10px 12px 6px;cursor:pointer";
    const y1 = (x.changes || {}).y1;
    div.innerHTML = `<h3 style="font-size:13px">${x.name}</h3><div style="font-size:12px;color:var(--muted)">${smart(x.last)} ${x.unit}${y1 == null ? "" : ` · <span style="color:var(${y1 >= 0 ? "--up" : "--down"})">${y1 > 0 ? "+" : ""}${fmt(y1, 1)}% YoY</span>`}</div><div class="chart" style="height:110px"></div>`;
    div.onclick = () => pick(x.sym, true);
    div._card = {el: $(".chart", div), cfg: {}, custom: el => {
      const ch = echarts.getInstanceByDom(el) || echarts.init(el); if (!charts.includes(ch)) charts.push(ch);
      const pts = clip(T(x.weekly), 5), o = baseOpt();
      o.grid = {left: 2, right: 4, top: 6, bottom: 2, containLabel: true};
      o.xAxis.axisLabel = {color: css("--muted"), fontSize: 9, hideOverlap: true};
      o.yAxis.axisLabel = {color: css("--muted"), fontSize: 9, formatter: v => smart(v)};
      o.yAxis.splitNumber = 2;
      const s = {name: x.name, type: "line", data: pts, showSymbol: false, lineStyle: {width: 1.5, color: css("--s1")}, itemStyle: {color: css("--s1")}};
      band(o, s, x, pts.map(q => q[1]));
      if (s.markLine) s.markLine.label.show = false;
      o.series = [s]; ch.setOption(o, true);
    }};
    mg.appendChild(div);
  }
  table();
}
