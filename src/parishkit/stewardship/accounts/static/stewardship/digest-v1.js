/* Inspect the retained chart, without fetching live data or recalculating totals.

   Pointing at or tapping a date, or moving the slider, shows a short tooltip
   beside that date (#575): the date as a small heading, then a few
   "label: value" lines. The fuller sentence for each date stays in the
   visually hidden live region and the slider's value text for screen readers;
   the tooltip itself is aria-hidden so it is not announced twice. */
"use strict";
(() => {
  const root = document.querySelector("[data-digest-chart]");
  if (!root) return;
  const image = root.querySelector("img");
  const slider = root.querySelector("input[type=range]");
  const output = root.querySelector("[data-digest-values]");
  const tip = root.querySelector("[data-digest-tip]");
  const source = document.getElementById("digest-chart-data");
  const controls = root.querySelector("[data-digest-controls]");
  if (!image || !slider || !output || !tip || !source || !controls) return;
  let data;
  try { data = JSON.parse(source.textContent); } catch (_) { return; }
  const isText = value => typeof value === "string";
  const isPoint = point => point && isText(point.date) && Array.isArray(point.rows) &&
    point.rows.every(row => Array.isArray(row) && row.length === 2 && row.every(isText));
  if (!Array.isArray(data.labels) || !data.labels.length ||
      !data.labels.every(isText) || !Array.isArray(data.points) ||
      data.points.length !== data.labels.length || !data.points.every(isPoint) ||
      !data.plot || !Array.isArray(data.limits) || data.limits.length !== 2) return;
  const plot = data.plot;
  const [low, high] = data.limits;
  let shown = -1;
  // When the chart was last pressed (ms, performance.now() clock). See blur.
  let pressedAt = -Infinity;
  const PRESS_GRACE_MS = 1000;

  // Build the tooltip's text nodes (never HTML) for one date.
  const fill = index => {
    const point = data.points[index];
    const heading = document.createElement("p");
    heading.className = "digest-tip-date";
    heading.textContent = point.date;
    const rows = document.createElement("dl");
    rows.className = "digest-tip-rows";
    for (const [label, value] of point.rows) {
      const term = document.createElement("dt");
      term.textContent = label + ":";
      const detail = document.createElement("dd");
      detail.textContent = value;
      rows.append(term, detail);
    }
    tip.replaceChildren(heading, rows);
  };

  // Put the tooltip beside the date's x position, at the top of the plot.
  // It sits right of the date in the left half and left of it in the right
  // half, clamped so it never leaves the image (or widens a phone's page).
  const place = index => {
    const width = image.clientWidth;
    const height = image.clientHeight;
    if (!width || !height) return;
    const x = (plot.left + (index - low) / (high - low) * (plot.right - plot.left)) * width;
    const gap = 12;
    const box = tip.offsetWidth;
    let left = x <= width / 2 ? x + gap : x - gap - box;
    left = Math.max(0, Math.min(left, width - box));
    tip.style.left = `${left}px`;
    tip.style.top = `${(1 - plot.top) * height}px`;
  };

  const show = (value, { visible = true } = {}) => {
    const index = Math.max(0, Math.min(data.labels.length - 1, Math.round(value)));
    if (!Number.isFinite(index)) return;
    slider.value = String(index);
    slider.setAttribute("aria-valuetext", data.labels[index]);
    if (output.textContent !== data.labels[index]) output.textContent = data.labels[index];
    if (!visible) return;
    if (index !== shown) { fill(index); shown = index; }
    tip.classList.add("digest-tip-open");
    place(index);
  };
  const hide = () => { tip.classList.remove("digest-tip-open"); };

  const point = event => {
    const bounds = image.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    if (x < plot.left || x > plot.right || y < 1 - plot.top || y > 1 - plot.bottom) return;
    show(low + (x - plot.left) / (plot.right - plot.left) * (high - low));
  };
  slider.addEventListener("input", () => show(Number(slider.value)));
  slider.addEventListener("focus", () => show(Number(slider.value)));
  // Leaving the slider hides the tooltip (keyboard users tabbing on), but not
  // when focus left because the chart was pressed: that press has just opened
  // the tooltip for the pressed date. A mouse press blurs the slider right
  // after pointerdown; a touch tap can blur it only after the finger lifts,
  // so a short grace period covers both. The image is not focusable, so
  // relatedTarget alone cannot tell a chart press from tabbing away.
  slider.addEventListener("blur", event => {
    if (event.relatedTarget && root.querySelector(".digest-figure")?.contains(event.relatedTarget)) return;
    if (performance.now() - pressedAt < PRESS_GRACE_MS) return;
    hide();
  });
  image.addEventListener("pointermove", point);
  image.addEventListener("pointerdown", event => {
    pressedAt = performance.now();
    point(event);
  });
  // A mouse leaving the chart hides the tooltip; a tap keeps it until the
  // next tap, slider change or Escape, since touch has no hover to leave.
  image.addEventListener("pointerleave", event => {
    if (event.pointerType === "mouse") hide();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") hide();
  });
  window.addEventListener("resize", () => {
    if (tip.classList.contains("digest-tip-open")) place(shown);
  });
  show(data.labels.length - 1, { visible: false });
  // Fill (but do not open) the last date, so a phone's reserved space for
  // the tooltip already has the right height. The template's hidden
  // attribute only covers pages without this script; from here the
  // digest-tip-open class decides whether the tooltip shows.
  fill(data.labels.length - 1);
  shown = data.labels.length - 1;
  tip.hidden = false;
  controls.hidden = false;
})();
