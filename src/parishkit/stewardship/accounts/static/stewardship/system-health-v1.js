// System health's dashboard (admin-portal spec, "Health dashboard", #1214).
//
// Opening and closing tiles: each tile's summary row is a disclosure button
// (aria-expanded, aria-controls) whose details are a band the tile grid
// places after the tile's row (ui-v1.css). The server draws Warning and
// Error tiles open and Working tiles closed; this script also opens the
// tile an address's anchor names (Home's lines link /admin/system/health/
// #health-backups), on load and when a link on the page names one. After
// that only the Administrator opens or closes a tile: live-status-v1.js's
// polls change the tiles' words through mirror targets, never aria-expanded
// or the band's hidden attribute. The browser stores nothing, so every
// load applies the rule again (decision 9 of 2026-10-13).
//
// Not current (decision 12): live-status-v1.js says when the status region
// is no longer current (parishkit:live-current). The dashboard then shows
// the Not current form that every summary row and the overall line already
// hold, inside their fixed heights; this script names the time of the last
// successful check, renames each band "<Area> details, not current", and
// announces the change once. Once current again, live-status-v1.js announces
// the counted sentence. Nothing opens, closes or moves, and focus stays.
(function () {
  "use strict";
  const dashboard = document.querySelector("[data-health-dashboard]");
  if (!dashboard) return;

  function setOpen(button, open) {
    // Open or close one tile's details band.
    const band = document.getElementById(button.getAttribute("aria-controls"));
    if (!band) return;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    band.hidden = !open;
  }

  function openNamed(hash) {
    // Open the tile ``hash`` ("#health-backups") names, if it names one.
    let id = "";
    try {
      id = decodeURIComponent((hash || "").replace(/^#/, ""));
    } catch {
      return;
    }
    const tile = id && document.getElementById(id);
    if (!tile || !tile.matches("[data-health-tile]")) return;
    const button = tile.querySelector("[data-health-toggle]");
    if (button) setOpen(button, true);
  }

  dashboard.addEventListener("click", (event) => {
    const button = event.target instanceof Element && event.target.closest("[data-health-toggle]");
    if (button) setOpen(button, button.getAttribute("aria-expanded") !== "true");
  });
  // A link on this page to a tile (a problem's "See the counts") opens it
  // too, even when the address already names it and no hashchange follows.
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element && event.target.closest("a[href*='#']");
    if (!link) return;
    const url = new URL(link.href, window.location.href);
    if (url.pathname === window.location.pathname) openNamed(url.hash);
  });
  window.addEventListener("hashchange", () => openNamed(window.location.hash));
  openNamed(window.location.hash);

  dashboard.addEventListener("parishkit:live-current", (event) => {
    const current = Boolean(event.detail && event.detail.current);
    dashboard.toggleAttribute("data-not-current", !current);
    dashboard.querySelectorAll("[data-health-band]").forEach((band) => {
      band.setAttribute("aria-label",
        band.getAttribute(current ? "data-label" : "data-label-not-current"));
    });
    if (current) return;
    // "Not checked since" the last successful check, which "Last checked"
    // (time[data-live-checked]) holds.
    const checked = document.querySelector("time[data-live-checked]");
    const last = dashboard.querySelector("time[data-health-last]");
    if (checked && last) {
      last.dateTime = checked.dateTime;
      if (window.ParishDates) window.ParishDates.localize(last.parentNode);
    }
    const announcer = [...document.querySelectorAll("[data-live-announcer]")]
      .find((node) => node.getAttribute("data-live-announcer") === "system-health");
    const words = dashboard.getAttribute("data-health-not-current-announce") || "";
    if (announcer && words) announcer.textContent = words.replace("%(time)s", last ? last.textContent : "");
  });
})();
