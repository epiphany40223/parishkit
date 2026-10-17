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
    if (!off || fitting || swapped) return;
    fitting = true;
    window.requestAnimationFrame(() => {
      fitting = false;
      window.dispatchEvent(new Event("resize"));
    });
  });
  // The colour scheme (#1264; ui-conventions "Colour scheme"). The page's
  // scheme is data-theme on the root element, which admin-gate-v1.js sets
  // from the Appearance choice or the device; a chart never asks the device
  // itself, or it would disagree with an explicit Light or Dark choice.
  // A specification names its colours by their light (email) values, each
  // a token's: a portal token in ui-v1.css or a chart token in chart-v1.css,
  // whose ":root, .light-card" rules declare the light values. The page
  // draws each in that token's value for the chart's own scheme, so the
  // server's specification, and the email drawn from it, stay as they are.
  const HEX = /^#(?:[0-9a-f]{3}){1,2}$/i;
  const hex6 = (value) => {
    const color = value.trim().toLowerCase();
    return color.length === 4 ? `#${[...color.slice(1)].map((d) => d + d).join("")}` : color;
  };
  // Each light value's token name, and each token's light value, read once
  // from the light rules. Two tokens with one light value share their dark
  // value too (tested), so the first is as good as any.
  let names = null;
  const lights = new Map();
  const tokenNames = () => {
    if (names && names.size) return names;
    names = new Map();
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = sheet.cssRules; } catch (_) { continue; }
      for (const rule of rules) {
        if (!(rule instanceof CSSStyleRule)) continue;
        if (!rule.selectorText.split(",").some((part) => part.trim() === ".light-card")) continue;
        for (const name of rule.style) {
          const value = rule.style.getPropertyValue(name).trim();
          if (!name.startsWith("--") || !HEX.test(value)) continue;
          lights.set(name, hex6(value));
          if (!names.has(hex6(value))) names.set(hex6(value), name);
        }
      }
    }
    return names;
  };
  // ``spec`` with each token colour in its value at ``root``, and the
  // Vega-Lite config that draws the chart's own furniture (background,
  // axes, gridlines, legend, titles and unstyled text) in the tokens, in
  // place of Vega's fixed light defaults. A specification's own config
  // still wins, and its colours are tokens too. ``print`` maps each colour
  // drawn back to its token's light value, for printing (below); the
  // tokens a chart uses have distinct values in each scheme (tested).
  const themed = (spec, root) => {
    const style = getComputedStyle(root);
    const known = tokenNames();
    const print = { colours: new Map(), paper: lights.get("--paper") };
    const token = (name) => {
      const value = style.getPropertyValue(name).trim();
      if (HEX.test(value) && lights.has(name)) print.colours.set(hex6(value), lights.get(name));
      return value;
    };
    const paint = (value) => {
      if (typeof value === "string") {
        const name = HEX.test(value) && known.get(hex6(value));
        return (name && token(name)) || value;
      }
      if (Array.isArray(value)) return value.map(paint);
      if (value && typeof value === "object") {
        return Object.fromEntries(Object.entries(value).map(([key, part]) => [key, paint(part)]));
      }
      return value;
    };
    const config = {
      background: token("--paper"),
      axis: {
        labelColor: token("--muted"),
        titleColor: token("--ink"),
        domainColor: token("--control-border"),
        tickColor: token("--control-border"),
        gridColor: token("--border"),
      },
      legend: { labelColor: token("--ink"), titleColor: token("--ink") },
      title: { color: token("--ink"), subtitleColor: token("--muted") },
      text: { color: token("--ink") },
      view: { stroke: token("--border") },
    };
    return { spec: paint(spec), config, print };
  };
  // Each drawn chart's way back to light, from themed().
  const printing = new WeakMap();
  const scheme = () => document.documentElement.dataset.theme || "light";
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
    // the page lets the same chart follow its panel's width instead. A chart
    // of stacked panels (the participation chart, #720) gives each panel
    // that width; its own fit-x autosize keeps the axes inside it.
    (Array.isArray(spec.vconcat) ? spec.vconcat : [spec]).forEach((part) => {
      part.width = "container";
    });
    // The CSP allows no eval and no inline styles: Vega runs in interpreter
    // mode (ast), nothing injects a stylesheet (chart-v1.css carries the
    // tooltip's), and there is no actions menu to style.
    const drawnIn = scheme();
    const colours = themed(spec, root);
    vegaEmbed(view, colours.spec, {
      config: colours.config,
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
      printing.set(root, colours.print);
      fit.observe(view);
      // Vega makes the view a graphics document named by the spec's
      // description; name it by the chart's title instead (the summary is
      // its description). It takes focus so a keyboard or screen-reader user
      // hears that name and summary; tooltips follow only the pointer, so
      // those users get the exact values from the table below the chart,
      // or, where the chart draws its values, from each mark's name.
      view.setAttribute("aria-label", view.dataset.chartTitle || "");
      view.tabIndex = 0;
      view.style.minHeight = "";
      root.dataset.chartState = "rendered";
      // The scheme changed while it was drawing: draw it again in the new.
      if (drawnIn !== scheme()) redraw(root);
    }).catch(() => {
      // The summary and table remain; the empty view must not take focus.
      root.dataset.chartState = "failed";
      view.style.minHeight = "";
      view.hidden = true;
    });
  };
  // Draw every pending chart in ``root`` (the document, or a region swapped
  // in place), and let go of views whose figures were replaced. A chart in
  // a closed disclosure waits:
  // its panel has no width until it opens, and Vega would measure the
  // container as empty. Opening the disclosure draws it (below).
  const render = (root) => {
    forget();
    const charts = [...root.querySelectorAll("[data-chart][data-chart-state=pending]")];
    if (root instanceof Element && root.matches("[data-chart][data-chart-state=pending]")) {
      charts.unshift(root);
    }
    charts.filter((chart) => !chart.closest("details:not([open])")).forEach(draw);
  };
  // "toggle" does not bubble, so listen in the capture phase.
  document.addEventListener("toggle", (event) => {
    if (event.target instanceof HTMLDetailsElement && event.target.open) render(event.target);
  }, true);
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
  // Draw a drawn chart again, in the page's scheme now. Its view keeps its
  // height until the new drawing replaces the old, so nothing below moves.
  const redraw = (root) => {
    const result = drawn.get(root);
    const view = root.querySelector("[data-chart-view]");
    if (!result || !view) return;
    view.style.minHeight = `${view.offsetHeight}px`;
    fit.unobserve(view);
    result.finalize();
    drawn.delete(root);
    root.dataset.chartState = "pending";
    draw(root);
  };
  // A change of scheme (the Appearance switch, the device's setting, or
  // another tab's choice) redraws every drawn chart in place. Charts not
  // yet drawn take the scheme when they are. While the page prints, the
  // charts stay light; a change made meanwhile is drawn once it ends.
  let current = scheme();
  const follow = () => {
    if (swapped || scheme() === current) return;
    current = scheme();
    [...drawn.keys()].forEach(redraw);
  };
  new MutationObserver(follow)
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  // A printed page is light (ui-conventions "Colour scheme"): the dark
  // tokens apply to the screen only. A chart drawn dark keeps its colours
  // in its SVG, so before the page prints, each drawn colour is swapped
  // for its token's light value in place. The swap is synchronous, so it
  // is done before the browser lays out the printed page (a fresh Vega
  // drawing is asynchronous, too late for that). The print layout can
  // make Vega draw a chart again in its screen colours (it follows the
  // window's resize), so while printing, an observer swaps whatever Vega
  // draws as soon as it draws it, before the next paint. Afterwards the
  // colours go back. The "print" media query is the medium, not the
  // colour scheme, which comes only from data-theme.
  let swapped = null;
  const lighten = (root) => {
    const print = printing.get(root);
    const svg = root.querySelector("[data-chart-view] svg");
    if (!print || !svg) return;
    svg.querySelectorAll("[fill], [stroke]").forEach((el) => {
      ["fill", "stroke"].forEach((attribute) => {
        const value = el.getAttribute(attribute);
        const light = value && HEX.test(value) && print.colours.get(hex6(value));
        if (light && light !== hex6(value)) {
          swapped.push(() => el.setAttribute(attribute, value));
          el.setAttribute(attribute, light);
        }
      });
    });
    const background = svg.style.backgroundColor;
    swapped.push(() => { svg.style.backgroundColor = background; });
    svg.style.backgroundColor = print.paper;
  };
  // Re-swaps a chart Vega has drawn again while printing. It stops
  // watching while it swaps, so its own changes do not call it back.
  const redrawn = new MutationObserver((records) => {
    const roots = new Set(records.map(({target}) => target.closest("[data-chart]")));
    redrawn.disconnect();
    roots.forEach((root) => { if (root && drawn.has(root)) lighten(root); });
    watch();
  });
  const watch = () => drawn.forEach((_result, root) => {
    const view = root.querySelector("[data-chart-view]");
    if (view) {
      redrawn.observe(view, { subtree: true, childList: true, attributes: true,
        attributeFilter: ["fill", "stroke", "style"] });
    }
  });
  const toLight = () => {
    if (swapped) return;
    swapped = [];
    drawn.forEach((_result, root) => lighten(root));
    watch();
  };
  const fromLight = () => {
    if (!swapped) return;
    redrawn.disconnect();
    // Newest first, so each element ends with the colour it had first.
    swapped.reverse().forEach((undo) => undo());
    swapped = null;
    // A scheme chosen while printing is drawn now.
    follow();
    // Refitting was paused while printing, and the print layout may have
    // left a chart at the paper's width: a resize makes Vega measure each
    // chart's panel again once the screen layout is back.
    window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  };
  window.addEventListener("beforeprint", toLight);
  window.addEventListener("afterprint", fromLight);
  window.matchMedia("print").addEventListener("change", (event) => {
    if (event.matches) toLight(); else fromLight();
  });
  // Vega shows a tooltip on pointermove, which a tap never sends: a touch
  // pointer goes over, down, up and out again, then the page gets a click.
  // So a tap on a drawn chart replays itself as a pointermove there, and
  // the tooltip opens; a tap anywhere else sends each drawn chart a
  // pointerout, which closes any open tooltip. Pointer and mouse users are
  // unaffected: only a click that follows a touch pointerdown counts.
  let touched = false;
  document.addEventListener("pointerdown", (event) => {
    touched = event.pointerType === "touch";
  }, true);
  document.addEventListener("click", (event) => {
    if (!touched) return;
    touched = false;
    const target = event.target instanceof Element
      ? event.target.closest("[data-chart-view] svg *") && event.target
      : null;
    const at = { bubbles: true, clientX: event.clientX, clientY: event.clientY, pointerType: "touch" };
    drawn.forEach((_result, root) => {
      const svg = root.querySelector("[data-chart-view] svg");
      if (svg && !(target && svg.contains(target))) {
        svg.dispatchEvent(new PointerEvent("pointerout", at));
      }
    });
    if (target) target.dispatchEvent(new PointerEvent("pointermove", at));
  }, true);
  window.ParishCharts = { render, hold };
  render(document);
})();
