/* Helpers the dashboard and the comparison page share: number formats, DOM nodes, the tooltip, and the mark
   shapes. Every data string is written as text. */
window.CW = (function () {
  "use strict";

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
  function unavailable(container, reason) {
    container.replaceChildren(el("div", { class: "cw-unavailable" }, reason));
  }

  // Tooltip that stays inside the page. Each row is [mark, label, value]; `swatch(mark)` draws the mark, and
  // "line" draws a line swatch.
  function showTip(event, title, rows, swatch) {
    const tip = document.getElementById("tip");
    tip.replaceChildren(el("div", { class: "t-h" }, title));
    for (const [mark, label, value] of rows) {
      const r = el("div", { class: "t-r" });
      if (mark) r.append(mark === "line" ? el("span", { class: "cw-line-sw" }) : swatch(mark));
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
  const hideTip = () => { document.getElementById("tip").style.display = "none"; };

  // Mark specs (dataviz): columns ≤ 24px with the rest of the band left as air, 4px rounded data end and a
  // square baseline, a 2px surface gap between touching marks, 2px lines, markers r ≥ 4 with a surface ring.
  const BAR_MAX = 24;
  const GAP = 2;

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
    const t = svg.append("text").style("font-size", "11.5px").text(s);
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
      if (it.kind === "line" || it.kind === "dash") {
        const l = el("span", { class: "cw-line-sw" });
        l.style.borderTopColor = it.color;
        if (it.kind === "dash") l.style.borderTopStyle = "dashed";
        s.append(l);
      } else if (it.kind === "bracket") {
        const b = el("span", { class: "cw-bracket-sw" });
        b.style.borderColor = it.color;
        s.append(b);
      } else {
        const sw = el("span", { class: "cw-sw" });
        sw.style.background = it.color;
        s.append(sw);
      }
      s.append(document.createTextNode(it.label));
      node.append(s);
    }
  }
  // A column or bar segment: square where it starts, 4px rounded at its data end when it is the last segment.
  function columnPath(x, yTop, yBottom, w, roundTop) {
    const r = roundTop ? Math.min(4, yBottom - yTop, w / 2) : 0;
    return `M${x},${yBottom}V${yTop + r}` +
      (r > 0 ? `Q${x},${yTop} ${x + r},${yTop}H${x + w - r}Q${x + w},${yTop} ${x + w},${yTop + r}` : `H${x + w}`) +
      `V${yBottom}Z`;
  }
  function barPath(x, yTop, len, h, roundEnd) {
    const r = roundEnd ? Math.min(4, len, h / 2) : 0;
    return `M${x},${yTop}H${x + len - r}` +
      (r > 0 ? `Q${x + len},${yTop} ${x + len},${yTop + r}V${yTop + h - r}Q${x + len},${yTop + h} ${x + len - r},${yTop + h}` : `V${yTop + h}`) +
      `H${x}Z`;
  }

  return { isNum, money, times, pct, css, el, unavailable, showTip, hideTip, BAR_MAX, GAP, prep, makeSvg, textWidth,
           axisMoney, gridY, legend, columnPath, barPath };
})();
