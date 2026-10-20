/* Credit structure dashboard. Reads the embedded Dashboard JSON; every data string is written as text. */
(function () {
  "use strict";

  const D = JSON.parse(document.getElementById("dashboard-data").textContent);
  const CLASSES = ["asset_level_secured", "parent_secured", "senior_unsecured", "convertible", "other"];
  const LABELS = {
    asset_level_secured: "Asset-level secured", parent_secured: "Parent secured",
    senior_unsecured: "Senior unsecured", convertible: "Convertible", other: "Other", equity: "Equity",
  };
  const SCENARIO_LABELS = { consensus: "Consensus", ttm: "TTM flat", "run-rate": "Run-rate", stress: "Stress" };
  const SKIPPED = D.extraction_skipped;
  const state = { scenario: null, period: null };

  // --- helpers ---------------------------------------------------------------------------------
  const isNum = (v) => typeof v === "number" && isFinite(v);
  function money(m, digits) {
    if (!isNum(m)) return "—";
    const sign = m < 0 ? "−" : "";
    const a = Math.abs(m);
    if (a >= 1000) return sign + "$" + (a / 1000).toFixed(digits == null ? 1 : digits) + "B";
    return sign + "$" + a.toFixed(0) + "M";
  }
  const times = (v) => (isNum(v) ? v.toFixed(1) + "x" : "—");
  const pct = (v, d) => (isNum(v) ? v.toFixed(d == null ? 1 : d) + "%" : "—");
  const color = (cls) => getComputedStyle(document.documentElement).getPropertyValue("--c-" + cls).trim() || "#999";
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function el(tag, attrs, text) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v;
      else if (k === "style") n.setAttribute("style", v);
      else n.setAttribute(k, v);
    }
    if (text != null) n.textContent = String(text);
    return n;
  }
  function swatch(cls) {
    const s = el("span", { class: "cw-sw" });
    s.style.background = color(cls);
    return s;
  }
  const balance = (i) => (i.principal || 0) + (i.post_period_change || 0);
  const sofr = D.market.sofr_pct.value;
  function allInRate(i) {
    if (i.rate_type === "fixed" && isNum(i.fixed_rate_pct)) return i.fixed_rate_pct;
    if (i.rate_type === "floating" && isNum(i.spread_pct) && isNum(sofr)) return sofr + i.spread_pct;
    if (isNum(i.effective_rate_pct)) return i.effective_rate_pct;
    return isNum(i.fixed_rate_pct) ? i.fixed_rate_pct : null;
  }
  const realInstruments = () => D.instruments.filter((i) => i.name !== "Unallocated" || i.source !== "reconcile");
  function unavailable(container, reason) {
    container.replaceChildren(el("div", { class: "cw-unavailable" }, reason));
  }

  // tooltip that stays inside the page
  const tip = document.getElementById("tip");
  function showTip(event, title, rows) {
    tip.replaceChildren(el("div", { class: "t-h" }, title));
    for (const [cls, label, value] of rows) {
      const r = el("div", { class: "t-r" });
      if (cls) r.append(cls === "line" ? el("span", { class: "cw-line-sw" }) : swatch(cls));
      r.append(el("span", {}, label), el("span", { class: "t-v" }, value));
      tip.append(r);
    }
    tip.style.display = "block";
    const pad = 12, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = event.pageX + pad, y = event.pageY + pad;
    if (x + w > window.scrollX + document.documentElement.clientWidth - 4) x = event.pageX - w - pad;
    if (y + h > window.scrollY + window.innerHeight - 4) y = event.pageY - h - pad;
    tip.style.left = Math.max(window.scrollX + 4, x) + "px";
    tip.style.top = Math.max(window.scrollY + 4, y) + "px";
  }
  const hideTip = () => { tip.style.display = "none"; };

  // --- header ----------------------------------------------------------------------------------
  function drawHeader() {
    const r = D.reconciliation;
    const parts = [];
    if (isNum(r.reported_total)) parts.push("reported " + money(r.reported_total));
    if (isNum(r.extracted_total)) parts.push("extracted " + money(r.extracted_total));
    if (isNum(r.gap_pct)) parts.push("gap " + pct(r.gap_pct * 100, 2));
    if (r.unverified.length) parts.push(r.unverified.length + " unverified");
    document.getElementById("recon-detail").textContent = parts.join(" · ");

    const names = [...new Set(D.model.scenarios.map((s) => s.scenario))];
    state.scenario = names.includes("consensus") ? "consensus" : names[0] || null;
    const ctl = document.getElementById("scenario-ctl");
    ctl.replaceChildren();
    for (const n of names) {
      const b = el("button", { type: "button", "aria-pressed": String(n === state.scenario) }, SCENARIO_LABELS[n] || n);
      b.addEventListener("click", () => { state.scenario = n; drawHeaderPressed(); drawScenarioCharts(); });
      ctl.append(b);
    }
    if (!names.length) ctl.append(el("span", { class: "cw-note" }, "no EBITDA available"));
  }
  function drawHeaderPressed() {
    for (const b of document.querySelectorAll("#scenario-ctl button")) {
      b.setAttribute("aria-pressed", String((SCENARIO_LABELS[state.scenario] || state.scenario) === b.textContent));
    }
  }

  // --- KPIs ------------------------------------------------------------------------------------
  function drawKpis() {
    const f = D.financials, label = D.market.ebitda_label.startsWith("EBITDA (") ? "EBITDA" : D.market.ebitda_label;
    const debt = f.total_debt.value, cash = f.cash.value;
    const net = isNum(debt) && isNum(cash) ? debt - cash : null;
    const ttmE = f.ttm.ebitda, ttmI = f.ttm.interest_expense;
    const pf = D.instruments.reduce((s, i) => s + balance(i), 0);
    const insts = realInstruments().filter((i) => balance(i) > 0);
    const eff = insts.filter((i) => isNum(i.effective_rate_pct));
    let wavg = null, wavgSub = "";
    if (eff.length) {
      wavg = eff.reduce((s, i) => s + balance(i) * i.effective_rate_pct, 0) / eff.reduce((s, i) => s + balance(i), 0);
      wavgSub = "disclosed effective rates, " + eff.length + " instruments";
    } else {
      const c = insts.filter((i) => isNum(allInRate(i)));
      if (c.length) {
        wavg = c.reduce((s, i) => s + balance(i) * allInRate(i), 0) / c.reduce((s, i) => s + balance(i), 0);
        wavgSub = "all-in coupons";
      }
    }
    const kpis = [
      ["Total debt", money(debt), isNum(pf) && Math.abs(pf - debt) > 1 ? "pro forma " + money(pf) : "principal at period end"],
      ["Cash", money(cash), "incl. restricted where reported"],
      ["Net debt", money(net), ""],
      ["Net debt ÷ TTM " + label, isNum(net) && ttmE > 0 ? times(net / ttmE) : "—", "TTM " + money(ttmE) + " · " + D.market.ebitda_label],
      ["TTM " + label + " ÷ interest", ttmI > 0 && isNum(ttmE) ? times(ttmE / ttmI) : "—", "TTM interest " + money(ttmI)],
      ["Operating lease liabilities", money(f.op_lease.value), "not in the debt stack"],
      ["Backlog (RPO)", money(f.rpo.value), ""],
      ["Weighted average rate", pct(wavg), wavgSub],
    ];
    const box = document.getElementById("kpis");
    box.replaceChildren();
    for (const [l, v, s] of kpis) {
      const k = el("div", { class: "cw-kpi" });
      k.append(el("span", { class: "cw-kpi-label" }, l), el("span", { class: "cw-kpi-value" }, v),
               el("span", { class: "cw-kpi-sub" }, s));
      box.append(k);
    }
  }

  // --- instrument table ------------------------------------------------------------------------
  const COLUMNS = [
    { key: "name", label: "Instrument", get: (i) => i.name },
    { key: "class", label: "Class", get: (i) => CLASSES.indexOf(i.seniority_class), show: (i) => LABELS[i.seniority_class] },
    { key: "borrower", label: "Borrower", get: (i) => i.borrower || "", narrow: true },
    { key: "security", label: "Security", get: (i) => i.security || "", narrow: true },
    { key: "balance", label: "Balance", num: true, get: balance, show: (i) => money(balance(i), 2) },
    { key: "commitment", label: "Commitment", num: true, get: (i) => i.commitment, show: (i) => money(i.commitment, 2), narrow: true },
    { key: "coupon", label: "Coupon", num: true, get: allInRate, show: couponText },
    { key: "maturity", label: "Maturity", get: (i) => i.maturity || "" },
    { key: "rating", label: "Rating", get: (i) => D.market.ratings[i.name] || "", narrow: true },
  ];
  function couponText(i) {
    if (i.rate_type === "fixed" && isNum(i.fixed_rate_pct)) return pct(i.fixed_rate_pct, 3).replace(/0+%$/, "%").replace(/\.%$/, "%");
    if (i.rate_type === "floating" && isNum(i.spread_pct)) return (i.benchmark || "float") + " + " + pct(i.spread_pct, 2);
    if (isNum(i.effective_rate_pct)) return pct(i.effective_rate_pct, 1) + " eff.";
    return "—";
  }
  const tableState = { key: "class", dir: 1 };
  function drawTable() {
    const table = document.getElementById("instrument-table");
    if (SKIPPED) return unavailable(table.parentElement, "instrument detail unavailable: " + SKIPPED);
    const col = COLUMNS.find((c) => c.key === tableState.key);
    const rows = D.instruments.slice().sort((a, b) => {
      const x = col.get(a), y = col.get(b);
      const nx = x == null || x === "", ny = y == null || y === "";
      if (nx || ny) return nx === ny ? 0 : nx ? 1 : -1;
      const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return (c || balance(b) - balance(a)) * (c ? tableState.dir : 1);
    });
    const thead = el("thead"), tr = el("tr");
    for (const c of COLUMNS) {
      const th = el("th", { class: (c.num ? "num " : "") + (c.narrow ? "cw-hide-narrow" : "") });
      if (c.key === tableState.key) th.setAttribute("aria-sort", tableState.dir > 0 ? "ascending" : "descending");
      const b = el("button", { type: "button", class: "cw-sort" }, c.label);
      b.addEventListener("click", () => {
        tableState.dir = tableState.key === c.key ? -tableState.dir : 1;
        tableState.key = c.key;
        drawTable();
      });
      th.append(b);
      tr.append(th);
    }
    thead.append(tr);
    const tbody = el("tbody");
    for (const i of rows) {
      const r = el("tr", { class: i.verified ? "" : "is-unverified" });
      for (const c of COLUMNS) {
        const td = el("td", { class: (c.num ? "num " : "") + (c.narrow ? "cw-hide-narrow " : "") + (c.key === "class" ? "cls" : "") });
        if (c.key === "class") td.append(swatch(i.seniority_class));
        td.append(document.createTextNode(c.show ? c.show(i) : c.get(i) || "—"));
        r.append(td);
      }
      tbody.append(r);
    }
    table.replaceChildren(thead, tbody);
  }

  // --- method ----------------------------------------------------------------------------------
  function drawMethod() {
    const body = document.getElementById("method-body");
    body.replaceChildren();
    const f = D.financials, r = D.reconciliation, m = D.market;
    const acc = String(D.filing.accession).replace(/[^0-9]/g, "");
    const doc = String(D.filing.primary_doc).replace(/[^A-Za-z0-9._-]/g, "");
    const link = el("a", { href: "https://www.sec.gov/Archives/edgar/data/" + Number(D.company.cik) + "/" + acc + "/" + doc,
                          target: "_blank", rel: "noopener" }, D.filing.form + " " + D.filing.accession);
    const p = el("p");
    p.append(document.createTextNode("Filing: "), link, document.createTextNode(", filed " + D.filing.filed +
             ". Money in $ millions in the data, shown in $M/$B."));
    body.append(el("h4", {}, "Sources"), p);

    const facts = [["Total debt", f.total_debt], ["Cash", f.cash], ["Operating lease liabilities", f.op_lease],
                   ["Finance lease liabilities", f.fin_lease], ["Backlog (RPO)", f.rpo],
                   ["Shares outstanding", f.shares_outstanding], ["SOFR", m.sofr_pct], ["Share price", m.price],
                   ["Market cap", m.market_cap]];
    const ul = el("ul");
    for (const [label, fact] of facts) {
      const v = label === "Shares outstanding" ? (isNum(fact.value) ? (fact.value / 1e6).toFixed(1) + "M" : "—")
        : label === "SOFR" ? pct(fact.value, 2) : label === "Share price" ? (isNum(fact.value) ? "$" + fact.value.toFixed(2) : "—")
        : money(fact.value, 3);
      const li = el("li");
      li.append(el("b", {}, label + ": " + v));
      const src = [fact.tag, fact.period, fact.note].filter(Boolean).join(" · ");
      if (src) li.append(document.createTextNode(" — " + src));
      ul.append(li);
    }
    body.append(ul);

    body.append(el("h4", {}, "Instrument list"));
    const ext = D.extraction;
    body.append(el("p", {}, SKIPPED ? "Not extracted: " + SKIPPED + ". Charts use SEC totals only; the gap is shown as Unallocated."
      : "Read from the filing's debt note" + (D.settings.llm_model ? " by " + D.settings.llm_model : "") +
        (D.settings.prompt_version ? " (prompt " + D.settings.prompt_version + ")" : "") +
        "; " + (ext ? ext.instruments.length : 0) + " instruments. Unit: " + (ext ? ext.unit_note : "—") + "."));
    const rl = el("ul");
    rl.append(el("li", {}, "Reconciliation " + r.status + ": reported " + money(r.reported_total, 3) + ", extracted " +
      money(r.extracted_total, 3) + (isNum(r.gap_pct) ? ", gap " + pct(r.gap_pct * 100, 2) : "") +
      (r.scale_applied !== 1 ? ", rescaled ×" + r.scale_applied : "") + "."));
    if (r.unverified.length) rl.append(el("li", {}, "Quotes not found in the filing: " + r.unverified.join(", ") + "."));
    for (const n of r.notes) rl.append(el("li", {}, n));
    if (r.maturity_diff.length) {
      rl.append(el("li", {}, "Bullet maturities vs reported schedule: " + r.maturity_diff.map((x) =>
        x.label + " " + money(x.bullets) + " of " + money(x.reported)).join("; ") + "."));
    }
    body.append(rl);

    body.append(el("h4", {}, "Model"));
    const ml = el("ul");
    if (f.maturities_as_of && f.maturities_as_of !== f.period_end) {
      ml.append(el("li", {}, "This filing has no maturity schedule; the schedule is the 10-K's as of " + f.maturities_as_of +
        ", with years already past dropped and the current year's amount placed in the remaining months."));
    }
    ml.append(el("li", {}, "Bullets repay at maturity; amortizing loans are fitted to the reported maturity schedule" +
      (isNum(D.model.fit_error) ? " (largest bucket error " + money(D.model.fit_error, 2) + ")" : "") +
      "; post-period draws are scheduled on top. Interest is charged monthly on the opening balance; floating rates use SOFR " +
      pct(sofr, 2) + " held flat."));
    ml.append(el("li", {}, "Scenarios: consensus when given in overrides (current year less reported quarters); TTM held flat; run-rate = latest quarter × 4; stress = consensus or TTM less " +
      Math.round((D.settings.stress_haircut || 0) * 100) + "% from the fiscal year after the period end."));
    const undated = D.instruments.filter((i) => balance(i) > 0 && (i.source === "reconcile" || !i.maturity));
    if (undated.length) ml.append(el("li", {}, "No maturity date: " + undated.map((i) => i.name + " " + money(balance(i))).join(", ") +
      ". These share whatever the reported maturity schedule leaves after the dated instruments, in proportion to balance, " +
      "with the Thereafter share spread over the five years after the last dated year. Unallocated is reported debt no " +
      "instrument accounts for; it uses the known instruments' average rate (or TTM interest ÷ debt)."));
    const noRate = D.model.excluded.filter((e) => e.includes("(no rate"));
    const unscheduled = D.model.excluded.filter((e) => !e.includes("(no rate"));
    if (unscheduled.length) ml.append(el("li", {}, "Not in the schedule: " + unscheduled.join(", ") + "."));
    if (noRate.length) ml.append(el("li", {}, "Scheduled without interest (no coupon, spread or SOFR): " +
      noRate.map((e) => e.split(" (no rate")[0]).join(", ") + "."));
    body.append(ml);
  }

  // --- chart helpers ---------------------------------------------------------------------------
  const PERIODS = D.model.periods.map((p) => p.label);
  const SCENARIO_COLORS = { consensus: "--c-parent_secured", ttm: "--c-line", "run-rate": "--c-equity", stress: "--bad" };
  const scenarioColor = (s) => css(SCENARIO_COLORS[s] || "--fg-muted");

  function prep(id) {
    const node = document.getElementById(id);
    node.replaceChildren();
    return node;
  }
  function makeSvg(node, H) {
    const W = Math.max(280, node.clientWidth || 600);
    const svg = d3.select(node).append("svg").attr("width", "100%").attr("height", H).attr("viewBox", `0 0 ${W} ${H}`);
    return { svg, W };
  }
  function textWidth(svg, s) {
    const t = svg.append("text").style("font-size", "11px").text(s);
    const w = t.node().getComputedTextLength();
    t.remove();
    return w;
  }
  const axisMoney = (max) => (v) => (max >= 1000 ? "$" + d3.format(",.3~")(v / 1000) + "B" : "$" + d3.format(",.0f")(v) + "M");
  function gridY(svg, y, ticks, x0, x1) {
    svg.append("g").selectAll("line").data(ticks).join("line").attr("x1", x0).attr("x2", x1)
      .attr("y1", (d) => y(d)).attr("y2", (d) => y(d)).style("stroke", css("--line"));
  }
  function legend(node, items) {
    node.replaceChildren();
    for (const it of items) {
      const s = el("span");
      if (it.line) {
        const l = el("span", { class: "cw-line-sw" });
        l.style.borderTopColor = it.color;
        if (it.dash) l.style.borderTopStyle = "dashed";
        s.append(l);
      } else {
        const sw = el("span", { class: "cw-sw" });
        sw.style.background = it.color;
        if (it.opacity) sw.style.opacity = it.opacity;
        s.append(sw);
      }
      s.append(document.createTextNode(it.label));
      node.append(s);
    }
  }
  const classesPresent = () => CLASSES.filter((c) => D.instruments.some((i) => i.seniority_class === c && balance(i) > 0));
  function serviceByPeriod() {
    return PERIODS.map((p) => {
      const o = { period: p, total: 0 };
      for (const c of CLASSES) { o[c] = 0; o[c + "_p"] = 0; o[c + "_i"] = 0; }
      for (const r of D.model.debt_service) {
        if (r.period !== p) continue;
        o[r.seniority_class] += r.principal + r.interest;
        o[r.seniority_class + "_p"] += r.principal;
        o[r.seniority_class + "_i"] += r.interest;
        o.total += r.principal + r.interest;
      }
      return o;
    });
  }
  const scenarioValue = (s, p) => (D.model.scenarios.find((r) => r.scenario === s && r.period === p) || {}).ebitda;
  const instrumentRate = (i) => (isNum(D.market.bond_yields[i.name]) ? D.market.bond_yields[i.name] : allInRate(i));

  // --- 2. capital structure ---------------------------------------------------------------------
  function drawStack() {
    const node = prep("stack-chart");
    if (SKIPPED) return unavailable(node, "instrument detail unavailable: " + SKIPPED);
    const rows = D.model.stack;
    if (!rows.length) return unavailable(node, "no debt to show");
    const rowH = 54, H = Math.max(220, rows.length * rowH);
    const { svg, W } = makeSvg(node, H);
    const towerW = Math.max(48, Math.min(110, W * 0.2)), gapX = Math.max(24, Math.min(56, W * 0.07));
    const tx = towerW + gapX + 8, avail = W - tx - 4;
    const y = d3.scaleLinear([0, 1], [2, H - 2]);
    const step = H / rows.length;
    rows.forEach((r, i) => {
      const detach = r.attachment + r.share;
      const y0 = y(1 - detach), y1 = y(1 - r.attachment), ym = (y0 + y1) / 2, yl = i * step + step / 2;
      const c = color(r.seniority_class);
      const g = svg.append("g").attr("class", "cw-hit");
      g.append("rect").attr("x", 0).attr("y", y0).attr("width", towerW).attr("height", Math.max(1.5, y1 - y0 - 1.5))
        .attr("rx", 3).style("fill", c);
      g.append("path").attr("d", `M${towerW + 2},${ym} C${towerW + gapX / 2},${ym} ${towerW + gapX / 2},${yl} ${towerW + gapX},${yl}`)
        .style("fill", "none").style("stroke", c).style("stroke-width", 1.5);
      g.append("circle").attr("cx", towerW + gapX).attr("cy", yl).attr("r", 3).style("fill", c);
      g.append("text").attr("x", tx).attr("y", yl - 4).style("fill", css("--fg")).style("font-size", "12.5px")
        .style("font-weight", 600).text(r.label);
      const variants = r.seniority_class === "equity"
        ? [`${money(r.amount)} · ${pct(r.share * 100, 0)} of capital · first loss`, money(r.amount)]
        : [`${money(r.amount)} · ${pct(r.share * 100, 0)} of capital · ${pct(r.attachment * 100, 0)} cushion`,
           `${money(r.amount)} · ${pct(r.attachment * 100, 0)} cushion`, money(r.amount)];
      const t2 = g.append("text").attr("x", tx).attr("y", yl + 12);
      for (const v of variants) { t2.text(v); if (t2.node().getComputedTextLength() <= avail) break; }
      g.on("pointermove", (event) => showTip(event, r.label, [
        [null, "Amount", money(r.amount, 2)], [null, "Share of capital", pct(r.share * 100)],
        [null, "Attachment – detachment", pct(r.attachment * 100) + " – " + pct(detach * 100)],
      ])).on("pointerleave", hideTip);
    });
    if (!rows.some((r) => r.seniority_class === "equity")) {
      node.append(el("p", { class: "cw-note" }, "Debt only: no market capitalisation available."));
    }
  }

  // --- 3. yield by tranche and maturity ----------------------------------------------------------
  function drawYield() {
    const node = prep("yield-chart");
    const legendNode = document.getElementById("yield-legend");
    legend(legendNode, classesPresent().map((c) => ({ color: color(c), label: LABELS[c] })));
    if (SKIPPED) return unavailable(node, "instrument detail unavailable: " + SKIPPED);
    const rows = realInstruments().map((i) => ({ i, date: i.maturity ? new Date(i.maturity + "-15T00:00:00Z") : null,
                                                 p: balance(i), yv: instrumentRate(i) }))
      .filter((r) => r.date && r.p > 0 && isNum(r.yv));
    const anyMarket = rows.some((r) => isNum(D.market.bond_yields[r.i.name]));
    document.getElementById("yield-note").textContent = "Bubble size is the balance. Yield is " +
      (anyMarket ? "the yield-to-worst from overrides where given, else " : "") + "the all-in coupon" +
      (isNum(sofr) ? " (floating loans at SOFR " + pct(sofr, 2) + " + spread)." : ".");
    if (!rows.length) return unavailable(node, "no instruments with a maturity and rate");
    const H = 320;
    const { svg, W } = makeSvg(node, H);
    const y = d3.scaleLinear([0, d3.max(rows, (r) => r.yv) * 1.15], [H - 26, 14]).nice();
    const ticks = y.ticks(5);
    const ml = d3.max(ticks, (t) => textWidth(svg, t + "%")) + 10;
    const rmax = Math.max(8, Math.min(22, W / 26));
    const start = new Date(D.financials.period_end + "T00:00:00Z");
    const end = d3.max(rows, (r) => r.date);
    const x = d3.scaleUtc([d3.utcMonth.offset(start, -2), d3.utcMonth.offset(end, 6)], [ml + rmax, W - rmax - 4]);
    const r = d3.scaleSqrt([0, d3.max(rows, (d) => d.p)], [0, rmax]);
    gridY(svg, y, ticks, ml, W);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`)
      .call(d3.axisLeft(y).tickValues(ticks).tickSize(0).tickPadding(8).tickFormat((d) => d + "%")).select(".domain").remove();
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - 26})`)
      .call(d3.axisBottom(x).ticks(W < 460 ? 4 : 8).tickSize(4).tickFormat(d3.utcFormat("%Y")));
    if (isNum(sofr)) {
      svg.append("line").attr("x1", ml).attr("x2", W).attr("y1", y(sofr)).attr("y2", y(sofr))
        .style("stroke", css("--fg-muted")).style("stroke-dasharray", "4 3");
      svg.append("text").attr("x", W - 4).attr("y", y(sofr) - 5).attr("text-anchor", "end").text("SOFR " + pct(sofr, 2));
    }
    svg.append("g").selectAll("circle").data(rows.sort((a, b) => b.p - a.p)).join("circle")
      .attr("cx", (d) => x(d.date)).attr("cy", (d) => y(d.yv)).attr("r", (d) => Math.max(3, r(d.p)))
      .style("fill", (d) => color(d.i.seniority_class)).style("fill-opacity", 0.72)
      .style("stroke", (d) => color(d.i.seniority_class)).style("stroke-width", 1.5)
      .on("pointermove", (event, d) => showTip(event, d.i.name, [
        [d.i.seniority_class, isNum(D.market.bond_yields[d.i.name]) ? "Yield to worst" : "All-in coupon", pct(d.yv, 2)],
        [null, "Balance", money(d.p, 2)], [null, "Maturity", d3.utcFormat("%b %Y")(d.date)],
        [null, "Rating", D.market.ratings[d.i.name] || "—"],
      ])).on("pointerleave", hideTip);
  }

  // --- 4. debt by what it pays ---------------------------------------------------------------------
  function drawPaying() {
    const node = prep("paying-chart");
    if (SKIPPED) return unavailable(node, "instrument detail unavailable: " + SKIPPED);
    const rows = realInstruments().map((i) => ({ i, p: balance(i),
      rate: isNum(i.effective_rate_pct) ? i.effective_rate_pct : allInRate(i),
      basis: isNum(i.effective_rate_pct) ? "effective" : "coupon" }))
      .filter((r) => r.p > 0 && isNum(r.rate)).sort((a, b) => b.rate - a.rate || b.p - a.p);
    if (!rows.length) return unavailable(node, "no instruments with a rate");
    const rowH = 30, H = rows.length * rowH + 4;
    const { svg, W } = makeSvg(node, H);
    const rateW = 46, nameW = Math.min(240, Math.max(120, W * 0.34));
    const x = d3.scaleLinear([0, d3.max(rows, (r) => r.p)], [0, Math.max(40, W - rateW - nameW - 70)]);
    const top = d3.max(rows, (r) => r.rate);
    rows.forEach((r, k) => {
      const yy = k * rowH + rowH / 2;
      const hot = r.rate >= top - 1e-9;
      const g = svg.append("g").attr("class", "cw-hit");
      g.append("text").attr("x", rateW - 6).attr("y", yy + 4).attr("text-anchor", "end")
        .style("font-size", "13px").style("fill", hot ? css("--bad") : css("--fg-muted")).text(pct(r.rate, 1));
      const name = g.append("text").attr("x", rateW).attr("y", yy + 4).style("fill", css("--fg")).style("font-size", "12px").text(r.i.name);
      while (name.node().getComputedTextLength() > nameW - 8 && name.text().length > 4) name.text(name.text().slice(0, -2) + "…");
      g.append("rect").attr("x", rateW + nameW).attr("y", yy - 6).attr("height", 12).attr("rx", 3)
        .attr("width", Math.max(2, x(r.p))).style("fill", hot ? css("--bad-fill") : color(r.i.seniority_class));
      g.append("text").attr("x", rateW + nameW + Math.max(2, x(r.p)) + 6).attr("y", yy + 4).text(money(r.p, 2));
      g.on("pointermove", (event) => showTip(event, r.i.name, [
        [r.i.seniority_class, LABELS[r.i.seniority_class], money(r.p, 2)],
        [null, r.basis === "effective" ? "Effective rate" : "All-in coupon", pct(r.rate, 2)],
        [null, "Maturity", r.i.maturity || "—"],
      ])).on("pointerleave", hideTip);
    });
  }

  // --- 5. payout wall ------------------------------------------------------------------------------
  function drawWall() {
    const node = prep("wall-chart");
    const present = classesPresent();
    legend(document.getElementById("wall-legend"), [
      ...present.map((c) => ({ color: color(c), label: LABELS[c] })),
      { color: css("--c-line"), label: D.market.ebitda_label, line: true },
      { color: css("--bad-fill"), label: "Not covered by EBITDA", opacity: 0.35 },
    ]);
    const rows = serviceByPeriod();
    if (!rows.some((o) => o.total > 0)) return unavailable(node, "no scheduled debt service");
    const H = 310;
    const { svg, W } = makeSvg(node, H);
    const ebitda = rows.map((o) => scenarioValue(state.scenario, o.period));
    const ymax = d3.max([...rows.map((o) => o.total), ...ebitda.filter(isNum)]);
    const y = d3.scaleLinear([0, ymax * 1.1], [H - 26, 18]).nice();
    const ticks = y.ticks(5), fmt = axisMoney(ymax);
    const ml = d3.max(ticks, (t) => textWidth(svg, fmt(t))) + 10;
    const x = d3.scaleBand(PERIODS, [ml, W - 4]).paddingInner(0.3).paddingOuter(0.15);
    gridY(svg, y, ticks, ml, W);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`)
      .call(d3.axisLeft(y).tickValues(ticks).tickSize(0).tickPadding(8).tickFormat(fmt)).select(".domain").remove();
    const every = Math.max(1, Math.ceil((d3.max(PERIODS, (p) => textWidth(svg, p)) + 8) / x.step()));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - 26})`)
      .call(d3.axisBottom(x).tickValues(PERIODS.filter((p, i) => i % every === 0)).tickSize(0).tickPadding(8));
    const bw = x.bandwidth();
    for (const s of d3.stack().keys(CLASSES)(rows)) {
      svg.append("g").selectAll("rect").data(s.filter((d) => d[1] - d[0] > 1e-6)).join("rect")
        .attr("x", (d) => x(d.data.period)).attr("width", bw).attr("y", (d) => y(d[1]))
        .attr("height", (d) => Math.max(0.5, y(d[0]) - y(d[1]) - 1)).attr("rx", 2).style("fill", color(s.key));
    }
    rows.forEach((o, k) => {
      const e = ebitda[k];
      if (!isNum(e) || e >= o.total) return;
      const top = y(o.total), bot = y(Math.max(0, e));
      svg.append("rect").attr("x", x(o.period) - 3).attr("width", bw + 6).attr("y", top - 2).attr("height", bot - top + 2)
        .attr("rx", 3).style("fill", css("--bad-fill")).style("fill-opacity", 0.22).style("stroke", css("--bad-fill")).style("stroke-width", 1.5);
    });
    const sel = rows.find((o) => o.period === state.period);
    if (sel) svg.append("rect").attr("x", x(sel.period) - 5).attr("width", bw + 10).attr("y", y(sel.total) - 5)
      .attr("height", y(0) - y(sel.total) + 5).attr("rx", 4).style("fill", "none").style("stroke", css("--fg"))
      .style("stroke-dasharray", "3 2");
    const labels = rows.map((o) => money(o.total));
    if (x.step() >= d3.max(labels, (s) => textWidth(svg, s)) + 6) {
      rows.forEach((o, k) => svg.append("text").attr("x", x(o.period) + bw / 2).attr("y", y(o.total) - 6)
        .attr("text-anchor", "middle").text(labels[k]));
    }
    const pts = rows.map((o, k) => ({ x: x(o.period) + bw / 2, y: isNum(ebitda[k]) ? y(Math.max(0, ebitda[k])) : null }))
      .filter((p) => p.y != null);
    if (pts.length) {
      svg.append("path").attr("d", d3.line().x((d) => d.x).y((d) => d.y)(pts)).style("fill", "none")
        .style("stroke", css("--c-line")).style("stroke-width", 2);
      svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (d) => d.x).attr("cy", (d) => d.y).attr("r", 3.5)
        .style("fill", css("--panel")).style("stroke", css("--c-line")).style("stroke-width", 2);
    }
    svg.append("g").selectAll("rect").data(rows).join("rect").attr("class", "cw-hit")
      .attr("x", (o) => x(o.period) - (x.step() - bw) / 2).attr("width", x.step()).attr("y", 0).attr("height", H - 26)
      .style("fill", "transparent")
      .on("pointermove", (event, o) => {
        const e = scenarioValue(state.scenario, o.period);
        showTip(event, o.period + (state.scenario ? " · " + (SCENARIO_LABELS[state.scenario] || state.scenario) : ""), [
          ...CLASSES.slice().reverse().filter((c) => o[c] > 1e-6).map((c) =>
            [c, LABELS[c] + " · int " + money(o[c + "_i"]) + " + prin " + money(o[c + "_p"]), money(o[c])]),
          [null, "Total payout", money(o.total)],
          ...(isNum(e) ? [["line", D.market.ebitda_label, money(e)], [null, "Coverage", times(o.total ? e / o.total : null)]] : []),
        ]);
      })
      .on("pointerleave", hideTip)
      .on("click", (event, o) => { state.period = o.period; drawWall(); drawWaterfall(); });
  }

  // --- 6. coverage ---------------------------------------------------------------------------------
  function drawCoverage() {
    const node = prep("coverage-chart");
    const names = [...new Set(D.model.coverage.map((r) => r.scenario))];
    legend(document.getElementById("coverage-legend"), [
      ...names.map((s) => ({ color: scenarioColor(s), label: SCENARIO_LABELS[s] || s, line: true })),
      { color: css("--bad"), label: "1.0x", line: true, dash: true },
    ]);
    const rows = D.model.coverage.filter((r) => isNum(r.dscr));
    if (!rows.length) return unavailable(node, "no coverage: EBITDA or debt service missing");
    const H = 260, CAP = 10;
    const { svg, W } = makeSvg(node, H);
    const ymax = Math.min(CAP, Math.max(1.5, d3.max(rows, (r) => r.dscr) * 1.1));
    const y = d3.scaleLinear([Math.min(0, d3.min(rows, (r) => r.dscr)), ymax], [H - 26, 12]).nice();
    const ticks = y.ticks(5);
    const ml = d3.max(ticks, (t) => textWidth(svg, t + "x")) + 10;
    const x = d3.scalePoint(PERIODS, [ml + 12, W - 12]);
    gridY(svg, y, ticks, ml, W);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`)
      .call(d3.axisLeft(y).tickValues(ticks).tickSize(0).tickPadding(8).tickFormat((d) => d + "x")).select(".domain").remove();
    const every = Math.max(1, Math.ceil((d3.max(PERIODS, (p) => textWidth(svg, p)) + 8) / Math.max(1, x.step())));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - 26})`)
      .call(d3.axisBottom(x).tickValues(PERIODS.filter((p, i) => i % every === 0)).tickSize(0).tickPadding(8));
    svg.append("line").attr("x1", ml).attr("x2", W).attr("y1", y(1)).attr("y2", y(1))
      .style("stroke", css("--bad")).style("stroke-dasharray", "4 3");
    for (const s of names) {
      const pts = PERIODS.map((p) => rows.find((r) => r.scenario === s && r.period === p))
        .filter(Boolean).map((r) => ({ r, x: x(r.period), y: y(Math.min(r.dscr, ymax)) }));
      svg.append("path").attr("d", d3.line().x((d) => d.x).y((d) => d.y)(pts)).style("fill", "none")
        .style("stroke", scenarioColor(s)).style("stroke-width", 2);
      svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (d) => d.x).attr("cy", (d) => d.y).attr("r", 3)
        .style("fill", scenarioColor(s))
        .on("pointermove", (event, d) => showTip(event, d.r.period + " · " + (SCENARIO_LABELS[s] || s),
          [[null, "Coverage", times(d.r.dscr) + (d.r.dscr > ymax ? " (off scale)" : "")]]))
        .on("pointerleave", hideTip);
    }
  }

  // --- 7. cash to raise ------------------------------------------------------------------------------
  function drawRaise() {
    const node = prep("raise-chart");
    legend(document.getElementById("raise-legend"), [
      { color: css("--c-equity"), label: "Covered by cash from operations" },
      { color: css("--bad-fill"), label: "To raise" },
    ]);
    const rows = D.model.raise_rows;
    if (!rows.length) return unavailable(node, "no capex reported, so cash to raise cannot be estimated");
    const hasConsensus = D.market.consensus.some((c) => isNum(c.capex) || isNum(c.cfo));
    const total = d3.sum(rows, (r) => r.to_raise);
    document.getElementById("raise-note").textContent = "Capex plus scheduled principal, less cash from operations (" +
      (hasConsensus ? "consensus" : "TTM held flat") + "). " + money(total) + " to raise across the period shown; " +
      "the floor leaves out repaying new debt.";
    const H = 260;
    const { svg, W } = makeSvg(node, H);
    const need = rows.map((r) => r.capex + r.principal);
    const ymax = d3.max(need);
    const y = d3.scaleLinear([0, ymax * 1.1 || 1], [H - 26, 12]).nice();
    const ticks = y.ticks(5), fmt = axisMoney(ymax);
    const ml = d3.max(ticks, (t) => textWidth(svg, fmt(t))) + 10;
    const x = d3.scaleBand(rows.map((r) => r.period), [ml, W - 4]).paddingInner(0.3).paddingOuter(0.15);
    gridY(svg, y, ticks, ml, W);
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`)
      .call(d3.axisLeft(y).tickValues(ticks).tickSize(0).tickPadding(8).tickFormat(fmt)).select(".domain").remove();
    const every = Math.max(1, Math.ceil((d3.max(rows, (r) => textWidth(svg, r.period)) + 8) / x.step()));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - 26})`)
      .call(d3.axisBottom(x).tickValues(rows.map((r) => r.period).filter((p, i) => i % every === 0)).tickSize(0).tickPadding(8));
    rows.forEach((r, k) => {
      const covered = need[k] - r.to_raise;
      const g = svg.append("g").attr("class", "cw-hit");
      g.append("rect").attr("x", x(r.period)).attr("width", x.bandwidth()).attr("y", y(covered))
        .attr("height", Math.max(0, y(0) - y(covered))).attr("rx", 2).style("fill", css("--c-equity"));
      if (r.to_raise > 0) g.append("rect").attr("x", x(r.period)).attr("width", x.bandwidth()).attr("y", y(need[k]))
        .attr("height", Math.max(0.5, y(covered) - y(need[k]) - 1)).attr("rx", 2).style("fill", css("--bad-fill"));
      g.append("rect").attr("x", x(r.period)).attr("width", x.bandwidth()).attr("y", 0).attr("height", H - 26).style("fill", "transparent")
        .on("pointermove", (event) => showTip(event, r.period, [
          [null, "Capex", money(r.capex)], [null, "Scheduled principal", money(r.principal)],
          [null, "Cash from operations", money(r.cfo)], [null, "To raise", money(r.to_raise)],
        ])).on("pointerleave", hideTip);
    });
  }

  // --- 8. cash waterfall --------------------------------------------------------------------------------
  function drawWaterfall() {
    const node = prep("waterfall-chart");
    const chips = document.getElementById("waterfall-years");
    chips.replaceChildren();
    if (SKIPPED) return unavailable(node, "instrument detail unavailable: " + SKIPPED);
    if (!PERIODS.length || !state.scenario) return unavailable(node, "no EBITDA scenario to allocate");
    if (!state.period) state.period = PERIODS.length > 1 ? PERIODS[1] : PERIODS[0];
    for (const p of PERIODS) {
      const b = el("button", { type: "button", "aria-pressed": String(p === state.period) }, p);
      b.addEventListener("click", () => { state.period = p; drawWall(); drawWaterfall(); });
      chips.append(b);
    }
    const rows = D.model.waterfall.filter((r) => r.period === state.period && r.scenario === state.scenario);
    const e = scenarioValue(state.scenario, state.period);
    const due = d3.sum(rows, (r) => r.due), paid = d3.sum(rows, (r) => r.paid);
    node.append(el("p", { class: "cw-note" }, (SCENARIO_LABELS[state.scenario] || state.scenario) + " " +
      D.market.ebitda_label + " " + money(e) + " against " + money(due) + " of interest and principal; " +
      (isNum(e) && e >= due ? money(e - paid) + " left after debt service." : money(due - paid) + " not covered.")));
    if (!rows.length) return;
    const rowH = 40, H = rows.length * rowH + 8;
    const { svg, W } = makeSvg(node, H);
    const labelW = Math.min(170, Math.max(110, W * 0.28));
    const x = d3.scaleLinear([0, d3.max(rows, (r) => r.due)], [0, Math.max(40, W - labelW - 90)]);
    rows.forEach((r, k) => {
      const yy = k * rowH + 8;
      const g = svg.append("g").attr("class", "cw-hit");
      g.append("text").attr("x", 0).attr("y", yy + 13).style("fill", css("--fg")).style("font-size", "12px").text(LABELS[r.seniority_class]);
      g.append("rect").attr("x", labelW).attr("y", yy).attr("width", Math.max(1, x(r.due))).attr("height", 18).attr("rx", 3)
        .style("fill", "none").style("stroke", css("--line"));
      g.append("rect").attr("x", labelW).attr("y", yy).attr("width", x(r.paid)).attr("height", 18).attr("rx", 3)
        .style("fill", color(r.seniority_class));
      if (r.shortfall > 1e-6) g.append("rect").attr("x", labelW + x(r.paid)).attr("y", yy)
        .attr("width", Math.max(1, x(r.shortfall))).attr("height", 18).attr("rx", 3)
        .style("fill", css("--bad-fill")).style("fill-opacity", 0.35).style("stroke", css("--bad-fill"));
      g.append("text").attr("x", labelW + x(r.due) + 6).attr("y", yy + 13)
        .text(r.shortfall > 1e-6 ? money(r.paid) + " of " + money(r.due) : money(r.due));
      g.on("pointermove", (event) => showTip(event, LABELS[r.seniority_class] + " · " + r.period, [
        [null, "Due (interest + principal)", money(r.due, 2)], [r.seniority_class, "Paid", money(r.paid, 2)],
        [null, "Shortfall", money(r.shortfall, 2)],
      ])).on("pointerleave", hideTip);
    });
  }

  function drawScenarioCharts() {
    drawWall();
    drawWaterfall();
  }

  function drawCharts() {
    drawStack();
    drawYield();
    drawPaying();
    drawWall();
    drawCoverage();
    drawRaise();
    drawWaterfall();
  }

  function drawAll() {
    drawHeader();
    drawKpis();
    drawTable();
    drawMethod();
    drawCharts();
  }
  drawAll();
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
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawCharts);
})();
