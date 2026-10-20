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
    ml.append(el("li", {}, "Bullets repay at maturity; amortizing loans are fitted to the reported maturity schedule" +
      (isNum(D.model.fit_error) ? " (largest bucket error " + money(D.model.fit_error, 2) + ")" : "") +
      "; post-period draws are scheduled on top. Interest is charged monthly on the opening balance; floating rates use SOFR " +
      pct(sofr, 2) + " held flat."));
    ml.append(el("li", {}, "Scenarios: consensus when given in overrides (current year less reported quarters); TTM held flat; run-rate = latest quarter × 4; stress = consensus or TTM less " +
      Math.round((D.settings.stress_haircut || 0) * 100) + "% after the first period."));
    if (D.model.excluded.length) ml.append(el("li", {}, "Not in the schedule (no maturity, balance or rate): " + D.model.excluded.join(", ") + "."));
    body.append(ml);
  }

  // --- charts (sections 2-8) are drawn by the functions below ----------------------------------
  function drawScenarioCharts() {
    for (const fn of [window.CD_drawWall, window.CD_drawWaterfall]) if (fn) fn();
  }

  window.CD = { D, state, CLASSES, LABELS, SCENARIO_LABELS, SKIPPED, money, times, pct, color, css, el, swatch,
                balance, allInRate, realInstruments, unavailable, showTip, hideTip, isNum };

  function drawAll() {
    drawHeader();
    drawKpis();
    drawTable();
    drawMethod();
  }
  drawAll();
})();
