/* Two-company comparison. Reads the embedded Comparison JSON (compare.py computes every number); every data
   string is written as text. */
(function () {
  "use strict";

  const C = JSON.parse(document.getElementById("comparison-data").textContent);
  const { isNum, money, times, pct, css, el, unavailable, prep, makeSvg, textWidth, gridY, legend, barPath, GAP,
          hideTip } = window.CW;
  const SIDES = ["a", "b"];
  const T = { a: C.a.ticker, b: C.b.ticker };
  const SCENARIO_LABELS = { consensus: "Consensus", ttm: "TTM flat", "run-rate": "Run-rate", stress: "Stress" };
  const SECTION_LABELS = { repayment: "Repayment capacity", adequacy: "Cash-flow adequacy",
                           tightness: "Cash-flow tightness", context: "Size", market: "Market" };
  const state = {
    scenario: C.scenarios.includes("ttm") ? "ttm" : C.scenarios[0] || null,
    cover: "icr_refi", quarter: "cash_interest_cover", runway: "paid",
  };
  const color = (k) => css("--c-" + k) || "#999";
  function swatch(k) {
    const s = el("span", { class: "cw-sw" });
    s.style.background = color(k);
    return s;
  }
  const showTip = (event, title, rows) => window.CW.showTip(event, title, rows, swatch);
  const day = (s) => new Date(s + "T00:00:00Z");
  const monthYear = d3.utcFormat("%b %Y");
  const measure = (key) => C.measures.find((m) => m.key === key);

  const minus = (s) => s.replace(/^-/, "−");
  function fmt(v, unit) {
    if (!isNum(v)) return "—";
    if (unit === "x") return minus(Math.abs(v) >= 100 ? v.toFixed(0) + "x" : times(v));
    if (unit === "pct") return minus(pct(v * 100, Math.abs(v) < 0.1 && v !== 0 ? 1 : 0));
    if (unit === "months") return (v < 10 ? v.toFixed(1) : v.toFixed(0)) + " months";
    if (unit === "years") return v.toFixed(1) + " years";
    if (unit === "usd") return money(v);
    if (unit === "bps") return v.toFixed(0) + " bp";
    return String(v);
  }
  const tickX = (v) => minus(d3.format("~g")(v) + "x");
  const tickPct = (v) => minus(d3.format("~g")(Math.round(v * 1000) / 10) + "%");
  function segmented(id, options, current, onPick) {
    const seg = document.getElementById(id);
    seg.replaceChildren();
    for (const [k, label] of options) {
      const b = el("button", { type: "button", "aria-pressed": String(k === current) }, label);
      b.addEventListener("click", () => onPick(k));
      seg.append(b);
    }
  }
  const sideLegend = (extra) => SIDES.map((s) => ({ color: color(s), label: T[s] + (extra ? extra(s) : "") }));

  // --- header: links to each company's own dashboard work from disk and from `creditdash serve` ---------------
  function drawHeader() {
    if (!location.protocol.startsWith("http")) return;
    for (const a of document.querySelectorAll("a[data-web]")) a.setAttribute("href", a.getAttribute("data-web"));
  }

  // --- one calendar axis, two companies ------------------------------------------------------------------
  // Years as bands: a short tick at each year boundary and the year written in the middle of its band, so a point
  // dated 31 December sits at the end of its own year rather than on the next year's label.
  function yearAxis(svg, x, y0) {
    const [d0, d1] = x.domain(), [r0, r1] = x.range();
    const years = d3.utcYear.range(d3.utcYear.floor(d0), d3.utcYear.offset(d3.utcYear.floor(d1), 1));
    const step = years.length ? x(d3.utcYear.offset(years[0], 1)) - x(years[0]) : r1 - r0;
    const every = Math.max(1, Math.ceil(40 / Math.max(1, step)));
    const g = svg.append("g").attr("class", "axis");
    g.append("line").attr("x1", r0).attr("x2", r1).attr("y1", y0).attr("y2", y0).style("stroke", css("--axis"));
    for (const yr of years) {
      const b = x(yr), year = yr.getUTCFullYear();
      if (b >= r0 && b <= r1) g.append("line").attr("x1", b).attr("x2", b).attr("y1", y0).attr("y2", y0 + 5).style("stroke", css("--axis"));
      const mid = x(new Date(Date.UTC(year, 6, 1)));
      if (mid < r0 || mid > r1 || year % every) continue;
      g.append("text").attr("x", mid).attr("y", y0 + 17).attr("text-anchor", "middle").text(year);
    }
  }

  // series: {a: [{d, v, label}], b: [...]}. Values outside `cap` leave through the plot's edge; the tooltip still
  // gives them. refs: horizontal reference lines [{v, label, color, dash}]. marks: extra points [{side, d, v, label}].
  // until: leave out points after this date.
  function drawTimeLines(id, { series, cap, refs = [], fmtV, tickV, tipTitle, marks = [], H = 260, minTop = null,
                               until = null }) {
    const node = prep(id);
    if (until) series = Object.fromEntries(SIDES.map((s) => [s, series[s].filter((p) => p.d <= until)]));
    const all = SIDES.flatMap((s) => series[s]);
    const vals = all.map((p) => p.v).filter(isNum);
    if (!vals.length) return unavailable(node, "no figures for either company");
    const { svg, W } = makeSvg(node, H);
    let lo = Math.min(0, d3.min(vals), ...refs.map((r) => r.v));
    let hi = Math.max(d3.max(vals), ...refs.map((r) => r.v), minTop == null ? -Infinity : minTop);
    lo = Math.max(lo, cap[0]);
    hi = Math.min(hi, cap[1]);
    const pad = (hi - lo) * 0.08 || 1;
    const y = d3.scaleLinear([lo < 0 ? lo - pad : lo, hi + pad], [H - 26, 14]).nice();
    const ticks = y.ticks(5);
    const ml = d3.max(ticks, (t) => textWidth(svg, (tickV || fmtV)(t))) + 10;
    const endRoom = 58;
    const dates = all.map((p) => p.d);
    const x = d3.scaleUtc([d3.utcMonth.offset(d3.min(dates), -4), d3.utcMonth.offset(d3.max(dates), 1)], [ml + 4, W - endRoom]);
    gridY(svg, y, ticks, ml, W - endRoom + 8);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`)
      .call(d3.axisLeft(y).tickValues(ticks).tickSize(0).tickPadding(8).tickFormat(tickV || fmtV)).select(".domain").remove();
    yearAxis(svg, x, H - 26);
    for (const r of refs) {
      if (r.v < y.domain()[0] || r.v > y.domain()[1]) continue;
      svg.append("line").attr("x1", ml).attr("x2", W - endRoom + 8).attr("y1", y(r.v)).attr("y2", y(r.v))
        .style("stroke", r.color).style("stroke-width", 1.5).style("stroke-dasharray", r.dash || null);
      svg.append("text").attr("x", W - endRoom + 12).attr("y", y(r.v) + 4).text(r.label);
    }
    const clipId = id + "-clip";
    svg.append("clipPath").attr("id", clipId).append("rect").attr("x", 0).attr("y", y.range()[1] - 6)
      .attr("width", W).attr("height", y.range()[0] - y.range()[1] + 12);
    const inside = (v) => v >= y.domain()[0] && v <= y.domain()[1];
    const labels = [];
    for (const s of SIDES) {
      const pts = series[s].filter((p) => isNum(p.v));
      if (!pts.length) continue;
      svg.append("path").attr("d", d3.line().x((p) => x(p.d)).y((p) => y(p.v))(pts)).attr("clip-path", `url(#${clipId})`)
        .style("fill", "none").style("stroke", color(s)).style("stroke-width", 2).style("stroke-linejoin", "round")
        .style("stroke-linecap", "round");
      svg.append("g").selectAll("circle").data(pts.filter((p) => inside(p.v))).join("circle")
        .attr("cx", (p) => x(p.d)).attr("cy", (p) => y(p.v)).attr("r", 4).style("fill", color(s))
        .style("stroke", css("--panel")).style("stroke-width", 2);
      const last = pts[pts.length - 1];  // an end label only where the line ends inside the plot
      if (inside(last.v)) labels.push({ s, x: x(last.d) + 8, y: y(last.v) + 4, text: T[s] + " " + fmtV(last.v) });
    }
    if (labels.length === 2 && Math.abs(labels[0].y - labels[1].y) < 14) {  // keep the two end labels apart
      const [up, down] = labels[0].y <= labels[1].y ? labels : [labels[1], labels[0]];
      const mid = (up.y + down.y) / 2;
      up.y = mid - 7;
      down.y = mid + 7;
    }
    for (const l of labels) {
      const t = svg.append("text").attr("x", l.x).attr("y", l.y).style("fill", css("--fg")).style("font-weight", 600).text(l.text);
      if (l.x + t.node().getComputedTextLength() > W) t.attr("text-anchor", "end").attr("x", W).attr("y", l.y - 12);
    }
    for (const m of marks) {
      if (!inside(m.v)) continue;
      const cx = x(m.d), cy = y(m.v);
      svg.append("path").attr("d", `M${cx - 5},${cy - 5}L${cx + 5},${cy + 5}M${cx - 5},${cy + 5}L${cx + 5},${cy - 5}`)
        .style("stroke", color(m.side)).style("stroke-width", 2.5).style("stroke-linecap", "round");
      const t = svg.append("text").attr("x", cx).attr("y", cy + (m.side === "a" ? -10 : 18)).attr("text-anchor", "middle")
        .style("fill", css("--fg")).text(m.label);
      const w = t.node().getComputedTextLength();
      if (cx - w / 2 < ml) t.attr("text-anchor", "start").attr("x", ml + 2);
      else if (cx + w / 2 > W) t.attr("text-anchor", "end").attr("x", W);
    }
    // crosshair: the guide follows the pointer; the tooltip names each company's nearest point
    const guide = svg.append("line").attr("y1", 14).attr("y2", H - 26).style("stroke", css("--axis"))
      .style("pointer-events", "none").style("display", "none");
    svg.append("rect").attr("x", ml).attr("width", W - ml).attr("y", 0).attr("height", H - 26).style("fill", "transparent")
      .on("pointermove", (event) => {
        const [mx] = d3.pointer(event);
        const at = x.invert(mx);
        guide.attr("x1", mx).attr("x2", mx).style("display", null);
        const rows = [];
        for (const s of SIDES) {
          const p = d3.least(series[s], (q) => Math.abs(q.d - at));
          if (p) rows.push([s, T[s] + " · " + p.label, isNum(p.v) ? fmtV(p.v) + (inside(p.v) ? "" : " (off scale)") : p.note || "—"]);
        }
        showTip(event, tipTitle, rows);
      })
      .on("pointerleave", () => { guide.style("display", "none"); hideTip(); });
  }

  // --- 1a. the four tests, a dumbbell per test ------------------------------------------------------------
  function drawTests() {
    const node = prep("tests-chart");
    legend(document.getElementById("tests-legend"), [
      ...sideLegend(),
      { color: css("--zone-not_covered"), label: "Not covered" }, { color: css("--zone-thin"), label: "Thin" },
      { color: css("--zone-covered"), label: "Covered" },
    ]);
    const keys = (C.a.tests.length ? C.a.tests : C.b.tests).map((t) => t.key);
    if (!keys.length) return unavailable(node, "no coverage tests in these builds");
    const test = (s, k) => C[s].tests.find((t) => t.key === k) || null;
    const vals = keys.flatMap((k) => SIDES.map((s) => (test(s, k) || {}).value)).filter(isNum);
    // Multiples run from deeply negative to over 100x; a symmetric log axis keeps the 1x and 2x thresholds apart
    // and still places the outliers. Beyond ±50x a value sits at the edge with an arrow.
    const STOPS = [-50, -20, -10, -5, -2, -1, 0, 1, 2, 5, 10, 20, 50];
    const lo = Math.min(-1, d3.min(vals) ?? 0), hi = Math.max(3, d3.max(vals) ?? 3);
    const d0 = [...STOPS].reverse().find((s) => s <= lo) ?? STOPS[0], d1 = STOPS.find((s) => s >= hi) ?? STOPS[STOPS.length - 1];
    const rowH = 64, top = 18, H = top + keys.length * rowH;
    const { svg, W } = makeSvg(node, H);
    const labelW = Math.min(150, Math.max(104, W * 0.26));
    const x = d3.scaleSymlog([d0, d1], [labelW + 12, W - 16]).constant(1);
    const clampX = (v) => x(Math.max(d0, Math.min(d1, v)));
    const ticks = [];  // the thresholds and the ends first, then whatever fits between them
    for (const s of [0, 1, 2, d0, d1, 10, -10, 5, -5, 20, -20, -1, -2, 50, -50]) {
      if (s < d0 || s > d1 || ticks.includes(s)) continue;
      const half = textWidth(svg, tickX(s)) / 2 + 4;
      if (ticks.every((t) => Math.abs(x(t) - x(s)) >= half + textWidth(svg, tickX(t)) / 2)) ticks.push(s);
    }
    ticks.sort((p, q) => p - q);
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${top - 4})`)
      .call(d3.axisTop(x).tickValues(ticks).tickSize(0).tickPadding(4).tickFormat(tickX)).select(".domain").remove();
    const zones = svg.append("g");  // under the gridlines, which sit under the marks
    svg.append("g").selectAll("line").data(ticks).join("line").attr("x1", (v) => x(v)).attr("x2", (v) => x(v))
      .attr("y1", top).attr("y2", H).style("stroke", css("--line"));
    keys.forEach((k, i) => {
      const yy = top + i * rowH + rowH / 2;
      const ta = test("a", k), tb = test("b", k);
      const label = (ta || tb).label;
      const covered = k.startsWith("liquidity") ? 1.5 : 2.0;
      const g = svg.append("g");
      for (const [z0, z1, zone] of [[d0, 1, "not_covered"], [1, covered, "thin"], [covered, d1, "covered"]]) {
        const a0 = Math.max(z0, d0), a1 = Math.min(z1, d1);
        if (a1 > a0) zones.append("rect").attr("x", x(a0)).attr("width", x(a1) - x(a0)).attr("y", yy - 15).attr("height", 30)
          .style("fill", css("--zone-" + zone));
      }
      g.append("text").attr("x", 0).attr("y", yy - 2).style("fill", css("--fg")).style("font-size", "12.5px")
        .style("font-weight", 600).text(label);
      g.append("text").attr("x", 0).attr("y", yy + 13).text("covered at " + covered.toFixed(1) + "x");
      const pts = [["a", ta], ["b", tb]].filter(([, t]) => t && isNum(t.value));
      if (pts.length === 2) {
        g.append("line").attr("x1", clampX(pts[0][1].value)).attr("x2", clampX(pts[1][1].value)).attr("y1", yy).attr("y2", yy)
          .style("stroke", css("--fg-muted")).style("stroke-width", 2);
      }
      for (const [s, t] of pts) {
        const off = t.value < d0 ? -1 : t.value > d1 ? 1 : 0;
        const cx = clampX(t.value);
        if (off) {  // beyond the axis: an arrow at the edge
          g.append("path").attr("d", off > 0 ? `M${cx - 7},${yy - 7}L${cx + 3},${yy}L${cx - 7},${yy + 7}Z`
                                             : `M${cx + 7},${yy - 7}L${cx - 3},${yy}L${cx + 7},${yy + 7}Z`)
            .style("fill", color(s)).style("stroke", css("--panel")).style("stroke-width", 1.5);
        } else {
          g.append("circle").attr("cx", cx).attr("cy", yy).attr("r", 6).style("fill", color(s))
            .style("stroke", css("--panel")).style("stroke-width", 2);
        }
        const lt = g.append("text").attr("x", cx).attr("y", s === "a" ? yy - 19 : yy + 27).attr("text-anchor", "middle")
          .style("fill", css("--fg")).text(T[s] + " " + fmt(t.value, "x"));
        const w = lt.node().getComputedTextLength();
        if (cx + w / 2 > W) lt.attr("text-anchor", "end").attr("x", W);
        else if (cx - w / 2 < labelW + 4) lt.attr("text-anchor", "start").attr("x", labelW + 4);
      }
      for (const s of SIDES) {
        if (!(s === "a" ? ta : tb) || !isNum((s === "a" ? ta : tb).value)) {
          g.append("text").attr("x", labelW + 12).attr("y", s === "a" ? yy - 19 : yy + 27).style("fill", css("--fg-muted"))
            .text(T[s] + ": no data");
        }
      }
      g.append("rect").attr("x", 0).attr("width", W).attr("y", yy - rowH / 2).attr("height", rowH)
        .style("fill", "transparent").attr("class", "cw-hit")
        .on("pointermove", (event) => showTip(event, label, SIDES.map((s) => {
          const t = s === "a" ? ta : tb;
          return [s, T[s] + (t ? " · " + t.status.replace("_", " ") : ""), t && isNum(t.value) ? fmt(t.value, "x") : "—"];
        }).concat(SIDES.map((s) => { const t = s === "a" ? ta : tb; return [null, T[s] + ": " + (t ? t.detail : "no data"), ""]; }))))
        .on("pointerleave", hideTip);
    });
  }

  // --- 1b. coverage over the modeled years --------------------------------------------------------------
  function drawCoverage() {
    if (!C.scenarios.length) {
      document.getElementById("coverage-note").textContent = "";
      legend(document.getElementById("coverage-legend"), []);
      return unavailable(prep("coverage-chart"), "the two builds have no EBITDA scenario in common");
    }
    segmented("scenario-ctl", C.scenarios.map((s) => [s, SCENARIO_LABELS[s] || s]), state.scenario,
              (k) => { state.scenario = k; drawCoverage(); });
    segmented("cover-ctl", [["icr_refi", "Interest"], ["dscr", "Interest + principal"]], state.cover,
              (k) => { state.cover = k; drawCoverage(); });
    document.getElementById("coverage-note").textContent = (state.cover === "icr_refi"
      ? "EBITDA ÷ each year's interest, with maturities refinanced at each company's own average rate so their interest keeps running."
      : "EBITDA ÷ each year's interest plus scheduled principal on today's debt: what it takes if maturities are paid off, not refinanced.") +
      " The scenario applies to both. Below 1.0x the gap has to be refinanced or raised.";
    const series = {};
    for (const s of SIDES) {
      series[s] = C[s].path.map((p) => ({ d: day(p.end), v: (p[state.cover] || {})[state.scenario], label: p.period }));
    }
    legend(document.getElementById("coverage-legend"), [
      ...sideLegend(() => " · " + (SCENARIO_LABELS[state.scenario] || state.scenario) + " EBITDA"),
      { kind: "dash", color: css("--bad"), label: "1.0x: just covered" },
    ]);
    drawTimeLines("coverage-chart", { series, cap: [-3, 10], fmtV: (v) => fmt(v, "x"), tickV: tickX,
      refs: [{ v: 1, label: "1.0x", color: css("--bad"), dash: "5 4" }],
      tipTitle: (state.cover === "icr_refi" ? "EBITDA ÷ interest" : "EBITDA ÷ interest + principal") + " · " +
                (SCENARIO_LABELS[state.scenario] || state.scenario) });
  }

  // --- 2a. cash-flow adequacy ----------------------------------------------------------------------------
  function drawAdequacy() {
    const node = prep("adequacy-chart");
    legend(document.getElementById("adequacy-legend"), [
      { color: css("--c-capex"), label: "Capex, TTM" }, { color: css("--c-principal"), label: "Principal due, next 12 months" },
      ...sideLegend(() => " operating cash flow, TTM"),
    ]);
    const rows = SIDES.map((s) => {
      const c = C[s], needs = isNum(c.capex) && isNum(c.next12m_principal) ? c.capex + c.next12m_principal : null;
      return { s, c, needs, share: isNum(c.cfo) && needs > 0 ? c.cfo / needs : null };
    });
    if (!rows.some((r) => r.needs > 0)) return unavailable(node, "no capex reported for either company");
    const groupH = 92, H = rows.length * groupH;
    const { svg, W } = makeSvg(node, H);
    const labelW = 128;
    const shares = rows.map((r) => r.share).filter(isNum);
    const lo = Math.max(-1, Math.min(0, d3.min(shares) ?? 0)), hi = Math.min(3, Math.max(1, d3.max(shares) ?? 1));
    const x = d3.scaleLinear([lo, hi], [labelW, W - 70]);
    const barH = 14;
    rows.forEach((r, i) => {
      const y0 = i * groupH + 8;
      const g = svg.append("g").attr("class", "cw-hit");
      g.append("circle").attr("cx", 6).attr("cy", y0 + 6).attr("r", 5).style("fill", color(r.s));
      g.append("text").attr("x", 16).attr("y", y0 + 10).style("fill", css("--fg")).style("font-weight", 600)
        .style("font-size", "12.5px").text(T[r.s]);
      if (!(r.needs > 0)) {
        g.append("text").attr("x", labelW).attr("y", y0 + 10).style("fill", css("--fg-muted"))
          .text(isNum(r.c.capex) ? "nothing to fund" : "no capex reported");
        return;
      }
      // needs: capex then principal, together 100%
      const yN = y0 + 18, yC = y0 + 18 + barH + 8;
      g.append("text").attr("x", 16).attr("y", yN + barH - 3).text("Needs");
      g.append("text").attr("x", 16).attr("y", yC + barH - 3).text("Operating cash flow");
      const capexShare = r.c.capex / r.needs;
      const x0 = x(0), xCap = x(capexShare), x1 = x(1);
      if (xCap - x0 > 0.5) g.append("path").attr("d", barPath(x0, yN, Math.max(1, xCap - x0 - (capexShare < 1 ? GAP : 0)), barH, capexShare >= 1))
        .style("fill", css("--c-capex"));
      if (x1 - xCap > 0.5) g.append("path").attr("d", barPath(xCap, yN, x1 - xCap, barH, true)).style("fill", css("--c-principal"));
      g.append("text").attr("x", x1 + 6).attr("y", yN + barH - 3).text(money(r.needs));
      if (isNum(r.share)) {
        const v = Math.max(lo, Math.min(hi, r.share));
        if (v >= 0) {
          g.append("path").attr("d", barPath(x0, yC, Math.max(2, x(v) - x0), barH, true)).style("fill", color(r.s));
        } else {  // negative operating cash flow runs left of zero
          const len = Math.max(2, x0 - x(v));
          g.append("path").attr("d", `M${x0},${yC}H${x0 - len + 4}Q${x0 - len},${yC} ${x0 - len},${yC + 4}V${yC + barH - 4}` +
            `Q${x0 - len},${yC + barH} ${x0 - len + 4},${yC + barH}H${x0}Z`).style("fill", color(r.s));
        }
        const lt = g.append("text").attr("x", Math.max(x(v), x0) + 6).attr("y", yC + barH - 3).style("fill", css("--fg"))
          .style("font-weight", 600).text((r.share >= 0 ? "covers " + pct(r.share * 100, 0) : "negative: " + money(r.c.cfo)) +
            (r.share > hi ? " ▸" : ""));
        if (Math.max(x(v), x0) + 6 + lt.node().getComputedTextLength() > W) lt.attr("text-anchor", "end").attr("x", W);
      }
      g.on("pointermove", (event) => showTip(event, T[r.s] + " · cash-flow adequacy", [
        [null, "Capex, TTM", money(r.c.capex)], [null, "Principal due, next 12 months", money(r.c.next12m_principal)],
        [r.s, "Operating cash flow, TTM", money(r.c.cfo)], [null, "Covers", isNum(r.share) ? pct(r.share * 100, 0) : "—"],
        [null, "Left to fund", money(Math.max(0, r.needs - (r.c.cfo || 0)))],
      ])).on("pointerleave", hideTip);
    });
    // 100% guide
    svg.append("line").attr("x1", x(1)).attr("x2", x(1)).attr("y1", 18).attr("y2", H - 6)
      .style("stroke", css("--fg-muted")).style("stroke-dasharray", "3 3");
    svg.append("text").attr("x", x(1)).attr("y", 10).attr("text-anchor", "middle").text("100% of needs");
  }

  // --- 2b. quarterly trend -------------------------------------------------------------------------------
  function drawQuarters() {
    const opts = [["cash_interest_cover", "Cash interest cover"], ["fcf_margin", "Free-cash-flow margin"]];
    segmented("quarter-ctl", opts, state.quarter, (k) => { state.quarter = k; drawQuarters(); });
    const cover = state.quarter === "cash_interest_cover";
    const none = SIDES.filter((s) => !C[s].quarters.length).map((s) => T[s]);
    document.getElementById("quarters-note").textContent = (cover
      ? "Each quarter's operating cash flow with interest paid added back, ÷ interest paid (interest expense where the filing gives no cash figure). Quarterly cash flow is lumpy: read the direction."
      : "Each quarter's operating cash flow less capex, ÷ revenue.") +
      (none.length ? " No quarterly figures for " + none.join(" and ") + " (a 20-F filer reports quarters on 6-K, without XBRL)." : "");
    legend(document.getElementById("quarters-legend"), [
      ...sideLegend(),
      ...(cover ? [{ kind: "dash", color: css("--bad"), label: "1.0x" }] : [{ kind: "dash", color: css("--fg-muted"), label: "0%" }]),
    ]);
    const series = {};
    for (const s of SIDES) series[s] = C[s].quarters.map((q) => ({ d: day(q.end), v: q[state.quarter], label: q.label }));
    drawTimeLines("quarters-chart", cover
      ? { series, cap: [-5, 30], fmtV: (v) => fmt(v, "x"), tickV: tickX, refs: [{ v: 1, label: "1.0x", color: css("--bad"), dash: "5 4" }],
          tipTitle: "Cash interest cover" }
      : { series, cap: [-3, 1], fmtV: (v) => fmt(v, "pct"), tickV: tickPct, refs: [{ v: 0, label: "0%", color: css("--fg-muted"), dash: "3 3" }],
          tipTitle: "Free-cash-flow margin" });
  }

  // --- 3a. runway ----------------------------------------------------------------------------------------
  function drawRunway() {
    segmented("runway-ctl", [["paid", "Maturities paid"], ["refinanced", "Maturities refinanced"]], state.runway,
              (k) => { state.runway = k; drawRunway(); });
    const key = state.runway === "paid" ? "liquidity_paid" : "liquidity_refinanced";
    const m = measure("runway_" + state.runway);
    const say = (s) => {
      const v = m[s], note = m[s + "_note"];
      if (isNum(v)) return T[s] + " runs out in about " + fmt(v, "months");
      return T[s] + ": " + (note === "not within the modeled years" ? "lasts beyond the modeled years" : note || "no figure");
    };
    document.getElementById("runway-note").textContent = "Cash plus undrawn revolvers at each period end, with no new " +
      "debt or equity: forward operating cash flow less capex and " + (state.runway === "paid"
        ? "scheduled principal." : "interest on refinanced maturities (principal rolled over at the assumed rate).") +
      " 100% is today's liquidity; below 0% the company has run out. " + SIDES.map(say).join("; ") + ".";
    legend(document.getElementById("runway-legend"), [
      ...sideLegend(), { kind: "dash", color: css("--bad"), label: "0%: liquidity used up" },
    ]);
    const series = {}, marks = [];
    for (const s of SIDES) {
      const pts = C[s].path.filter((p) => isNum(p[key]));
      series[s] = pts.length ? [{ d: day(C[s].period_end), v: 1, label: "today" },
        ...pts.map((p) => ({ d: day(p.end), v: p[key], label: p.period }))] : [];
      if (isNum(m[s])) {
        const d = d3.utcDay.offset(day(C[s].period_end), Math.round(m[s] * 30.44));
        marks.push({ side: s, d, v: 0, label: T[s] + " ~" + monthYear(d) });
      }
    }
    const start = d3.min(SIDES.map((s) => day(C[s].period_end)));
    drawTimeLines("runway-chart", { series, cap: [-1, 3], minTop: 1.1, fmtV: (v) => fmt(v, "pct"), tickV: tickPct, marks,
      until: d3.utcYear.offset(start, 6),
      refs: [{ v: 0, label: "0%", color: css("--bad"), dash: "5 4" }], tipTitle: "Liquidity left, share of today's" });
  }

  // --- 3b. maturity profile ------------------------------------------------------------------------------
  function drawMaturity() {
    legend(document.getElementById("maturity-legend"), sideLegend((s) => {
      const m = measure("due_12m");
      return isNum(m[s]) ? " · " + pct(m[s] * 100, 0) + " due in 12 months" : "";
    }));
    const series = {};
    for (const s of SIDES) {
      const pts = C[s].path.filter((p) => isNum(p.due_cumulative));
      series[s] = pts.length ? [{ d: day(C[s].period_end), v: 0, label: "today" },
        ...pts.map((p) => ({ d: day(p.end), v: p.due_cumulative, label: p.period }))] : [];
    }
    drawTimeLines("maturity-chart", { series, cap: [0, 1.5], minTop: 1, fmtV: (v) => fmt(v, "pct"), tickV: tickPct,
      tipTitle: "Share of today's debt due by period end" });
  }

  // --- measures table ------------------------------------------------------------------------------------
  function drawTable() {
    const table = document.getElementById("measure-table");
    const thead = el("thead"), hr = el("tr");
    hr.append(el("th", {}, "Measure"));
    for (const s of SIDES) {
      const th = el("th", { class: "num" });
      th.append(swatch(s), document.createTextNode(T[s]));
      hr.append(th);
    }
    hr.append(el("th", {}, "Stronger"), el("th", { class: "cw-hide-narrow" }, "How it is computed"));
    thead.append(hr);
    const tbody = el("tbody");
    for (const section of ["repayment", "adequacy", "tightness", "market", "context"]) {
      const rows = C.measures.filter((m) => m.section === section);
      if (!rows.length) continue;
      const tr = el("tr"), td = el("td", { class: "sec", colspan: "5" }, SECTION_LABELS[section]);
      const t = C.tally[section];
      if (t) td.append(el("span", { class: "cw-note" }, T.a + " " + t.a + " · " + T.b + " " + t.b + " · even " + t.even));
      else if (section === "context") td.append(el("span", { class: "cw-note" }, "context, not compared"));
      tr.append(td);
      tbody.append(tr);
      for (const m of rows) {
        const r = el("tr");
        r.append(el("td", {}, m.label + (m.better ? (m.better === "higher" ? " ↑" : " ↓") : "")));
        for (const s of SIDES) {
          const v = m[s], note = m[s + "_note"];
          const cell = el("td", { class: "num" + (m.stronger === s ? " is-stronger" : "") });
          if (isNum(v)) {
            cell.textContent = fmt(v, m.unit);
            if (note) cell.title = note;
          } else {
            cell.append(el("span", { class: "miss" }, note || "—"));
            if (m.stronger === s) cell.firstChild.classList.remove("miss");
          }
          r.append(cell);
        }
        const who = el("td", { class: "who" });
        if (m.stronger === "a" || m.stronger === "b") who.append(swatch(m.stronger), document.createTextNode(T[m.stronger]));
        else who.textContent = m.stronger === "even" ? "even" : m.better ? "—" : "";
        r.append(who, el("td", { class: "how cw-hide-narrow" }, m.detail));
        tbody.append(r);
      }
    }
    table.replaceChildren(thead, tbody);
  }

  // --- method --------------------------------------------------------------------------------------------
  function drawMethod() {
    const body = document.getElementById("method-body");
    body.replaceChildren(el("h4", {}, "Sources"));
    const ul = el("ul");
    for (const s of SIDES) {
      const c = C[s];
      const acc = String(c.accession).replace(/[^0-9]/g, "");
      const doc = String(c.primary_doc).replace(/[^A-Za-z0-9._-]/g, "");
      const li = el("li");
      li.append(el("b", {}, T[s] + ": "), el("a", {
        href: "https://www.sec.gov/Archives/edgar/data/" + Number(c.cik) + "/" + acc + "/" + doc, target: "_blank", rel: "noopener",
      }, c.form + " " + c.accession), document.createTextNode(", filed " + c.filed + ", period ended " + c.period_end +
        "; dashboard built " + String(c.built_at).replace("T", " ").slice(0, 16) + "."));
      ul.append(li);
    }
    body.append(ul, el("h4", {}, "Method"));
    const ml = el("ul");
    for (const text of [
      "Every number comes from the two companies' own dashboards (dashboard.json), read the same way: the same coverage tests, the same debt-service model and the same refinancing assumption, each at its own rate. Nothing is re-read from the filings.",
      "Size is taken out: tests and leverage are multiples, cash measures are shares of needs, debt, revenue or today's liquidity. Dollar amounts appear only as context.",
      "Time series sit on one calendar axis at each company's own period ends, so different fiscal years line up by date. A first period shorter than a year is prorated, which leaves its ratios comparable.",
      "Coverage scenarios are the ones both builds have; consensus appears only when both have consensus in overrides.",
      "Runway: liquidity (cash plus committed, undrawn revolvers) plus each period's operating cash flow less capex and scheduled principal (or, refinanced, less interest on refinanced maturities), linear within a period. Forward cash flow and capex are consensus when given, else trailing twelve months held flat: the same rows as each dashboard's cash to raise.",
      "Stronger: higher or lower by the measure's direction; values within 5% of each other are even; a measure missing on either side is not counted. Net cash counts as the strongest leverage value, and a runway that outlasts the modeled years as the longest.",
    ]) ml.append(el("li", {}, text));
    body.append(ml);
  }

  function drawCharts() {
    drawTests();
    drawCoverage();
    drawAdequacy();
    drawQuarters();
    drawRunway();
    drawMaturity();
  }

  drawHeader();
  drawTable();
  drawMethod();
  drawCharts();
  let resizeTimer = null;
  let lastWidth = document.documentElement.clientWidth;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (document.documentElement.clientWidth === lastWidth) return;
      lastWidth = document.documentElement.clientWidth;
      drawCharts();
    }, 150);
  });
})();
