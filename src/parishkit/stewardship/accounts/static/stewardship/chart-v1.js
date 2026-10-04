/* Draw each embedded Vega-Lite chart; its data table stays as the exact record. */
"use strict";
(() => {
  if (typeof vegaEmbed !== "function" || !window.vega) return;
  // Every chart carries its data inline, so nothing may be fetched: a loader
  // that refuses every load and every URL (data, images, links) keeps a
  // specification from reaching the network even if one were to name a URL.
  // The CSP would block a third-party fetch anyway; this is defence in depth.
  const refuse = () => Promise.reject(new Error("Charts load nothing."));
  const loader = vega.loader();
  loader.load = refuse;
  loader.sanitize = refuse;
  // The drawn views by figure, so a view whose figure has left the page (a
  // region refreshed in place by ui-v1.js) is finalized: Vega then removes
  // its resize and pointer listeners instead of keeping them alive.
  const drawn = new Map();
  const forget = () => {
    drawn.forEach((result, root) => {
      if (root.isConnected) return;
      fit.unobserve(root.querySelector("[data-chart-view]"));
      result.finalize();
      drawn.delete(root);
    });
  };
  // A chart follows its panel's width ("container"), which Vega measures
  // only when it draws and on a window resize. The panel can still change
  // width afterwards: WebKit can measure it before the page's layout settles
  // on first load, drawing the chart wider than its panel (then scaled down,
  // so it looks short), and a sidebar or a font can move it later. When a
  // drawn chart no longer matches its panel, a window resize event makes
  // Vega measure again; one event per frame covers every chart.
  let fitting = false;
  const fit = new ResizeObserver((entries) => {
    const off = entries.some(({target}) => {
      const svg = target.querySelector("svg");
      return svg && Math.abs(Number(svg.getAttribute("width")) - target.clientWidth) > 1;
    });
    if (!off || fitting) return;
    fitting = true;
    window.requestAnimationFrame(() => {
      fitting = false;
      window.dispatchEvent(new Event("resize"));
    });
  });
  const draw = (root) => {
    const source = document.getElementById(root.dataset.chart);
    const view = root.querySelector("[data-chart-view]");
    if (!view) return;
    // A chart that cannot be drawn gives back the space hold() kept for it;
    // its summary and table remain.
    const release = () => { view.style.minHeight = ""; };
    if (!source) { release(); return; }
    let spec;
    try { spec = JSON.parse(source.textContent); } catch (_) { release(); return; }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) { release(); return; }
    // Drawn once: a figure already rendered (or failed) is left alone.
    root.dataset.chartState = "drawing";
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
    }).then((result) => {
      // Its region was refreshed in place while it was drawing: release the
      // view now rather than keep its listeners for a figure that is gone.
      if (!root.isConnected) {
        result.finalize();
        return;
      }
      drawn.set(root, result);
      fit.observe(view);
      // Vega makes the view a graphics document named by the spec's
      // description; name it by the chart's title instead (the summary is
      // its description). It takes focus so a keyboard or screen-reader user
      // hears that name and summary; tooltips follow only the pointer, so
      // those users get the exact values from the table below the chart.
      view.setAttribute("aria-label", view.dataset.chartTitle || "");
      view.tabIndex = 0;
      view.style.minHeight = "";
      root.dataset.chartState = "rendered";
    }).catch(() => {
      // The summary and table remain; the empty view must not take focus.
      root.dataset.chartState = "failed";
      view.style.minHeight = "";
      view.hidden = true;
    });
  };
  // Draw every pending chart in ``root`` (the document, or a region swapped
  // in place), and let go of views whose figures were replaced.
  const render = (root) => {
    forget();
    const charts = [...root.querySelectorAll("[data-chart][data-chart-state=pending]")];
    if (root instanceof Element && root.matches("[data-chart][data-chart-state=pending]")) {
      charts.unshift(root);
    }
    charts.forEach(draw);
  };
  // Before ``fresh`` replaces ``old`` (an in-place refresh), give each chart
  // in it the height its predecessor of the same key had, until it is drawn:
  // otherwise the empty views collapse for a moment and the page under the
  // reader moves. CSSOM, not a style attribute, so the CSP allows it.
  const hold = (old, fresh) => {
    fresh.querySelectorAll("[data-chart]").forEach((root) => {
      const before = old.querySelector(`[data-chart="${CSS.escape(root.dataset.chart)}"] [data-chart-view]`);
      const view = root.querySelector("[data-chart-view]");
      if (before && view && before.offsetHeight) {
        view.style.minHeight = `${before.offsetHeight}px`;
      }
    });
  };
  window.ParishCharts = { render, hold };
  render(document);
})();
