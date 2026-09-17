/* Inspect the retained chart, without fetching live data or recalculating totals. */
"use strict";
(() => {
  const root = document.querySelector("[data-digest-chart]");
  if (!root) return;
  const image = root.querySelector("img");
  const slider = root.querySelector("input[type=range]");
  const output = root.querySelector("[data-digest-values]");
  const source = document.getElementById("digest-chart-data");
  if (!image || !slider || !output || !source) return;
  let data;
  try { data = JSON.parse(source.textContent); } catch (_) { return; }
  if (!Array.isArray(data.labels) || !data.labels.length ||
      data.labels.some(value => typeof value !== "string") ||
      !data.plot || !Array.isArray(data.limits) || data.limits.length !== 2) return;
  const show = value => {
    const index = Math.max(0, Math.min(data.labels.length - 1, Math.round(value)));
    if (!Number.isFinite(index)) return;
    slider.value = String(index);
    slider.setAttribute("aria-valuetext", data.labels[index]);
    image.title = data.labels[index];
    if (output.textContent !== data.labels[index]) output.textContent = data.labels[index];
  };
  const point = event => {
    const bounds = image.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    const plot = data.plot;
    if (x < plot.left || x > plot.right || y < 1 - plot.top || y > 1 - plot.bottom) return;
    show(data.limits[0] + (x - plot.left) / (plot.right - plot.left) *
      (data.limits[1] - data.limits[0]));
  };
  slider.addEventListener("input", () => show(Number(slider.value)));
  image.addEventListener("pointermove", point);
  image.addEventListener("pointerdown", point);
  show(data.labels.length - 1);
  root.querySelector("[data-digest-controls]").hidden = false;
})();
