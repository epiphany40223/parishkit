/* Draw each embedded Vega-Lite chart; its data table stays as the exact record. */
"use strict";
(() => {
  const charts = document.querySelectorAll("[data-chart]");
  if (!charts.length || typeof vegaEmbed !== "function" || !window.vega) return;
  // Every chart carries its data inline, so nothing may be fetched: a loader
  // that refuses every load and every URL (data, images, links) keeps a
  // specification from reaching the network even if one were to name a URL.
  // The CSP would block a third-party fetch anyway; this is defence in depth.
  const refuse = () => Promise.reject(new Error("Charts load nothing."));
  const loader = vega.loader();
  loader.load = refuse;
  loader.sanitize = refuse;
  charts.forEach(root => {
    const source = document.getElementById(root.dataset.chart);
    const view = root.querySelector("[data-chart-view]");
    if (!source || !view) return;
    let spec;
    try { spec = JSON.parse(source.textContent); } catch (_) { return; }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) return;
    // The server renders the fixed width the spec carries (the email image);
    // the page lets the same chart follow its panel's width instead.
    spec.width = "container";
    // The CSP allows no eval and no inline styles: Vega runs in interpreter
    // mode (ast), nothing injects a stylesheet (chart-v1.css carries the
    // tooltip's), and there is no actions menu to style.
    vegaEmbed(view, spec, {
      mode: "vega-lite",
      renderer: "svg",
      actions: false,
      ast: true,
      defaultStyle: false,
      tooltip: { disableDefaultStyle: true },
      loader,
    }).then(() => {
      // Vega makes the view a graphics document named by the spec's
      // description; name it by the chart's title instead (the summary is
      // its description). It takes focus so a keyboard or screen-reader user
      // hears that name and summary; tooltips follow only the pointer, so
      // those users get the exact values from the table below the chart.
      view.setAttribute("aria-label", view.dataset.chartTitle || "");
      view.tabIndex = 0;
      root.dataset.chartState = "rendered";
    }).catch(() => {
      // The summary and table remain; the empty view must not take focus.
      root.dataset.chartState = "failed";
      view.hidden = true;
    });
  });
})();
