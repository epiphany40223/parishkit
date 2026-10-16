// System health's dashboard (admin-portal spec, "Health dashboard", #1214).
//
// Opening and closing tiles: each tile's summary row is a disclosure button
// (aria-expanded, aria-controls) whose details are a band the tile grid
// places after the tile's row (ui-v1.css). The server draws Warning and
// Error tiles open and Working tiles closed. An address whose anchor names
// a tile (Home's lines link /admin/system/health/#health-backups) opens
// exactly that tile on load and closes the others, whatever their state
// (ui-conventions "Disclosures"); later, a link on the page or a changed
// anchor opens its tile and closes nothing. After that only the
// Administrator opens or closes a tile, by the toggle or by a click on the
// open band's blank space or plain text: live-status-v1.js's polls change
// the tiles' words through mirror targets, never aria-expanded or the
// band's hidden attribute. The browser stores nothing, so every load
// applies the rule again (decision 9 of 2026-10-13).
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
  // What keeps its own click inside a band: anything a reader can operate
  // or focus, and a toggletip's bubble or a dialog drawn there.
  const OWN_CLICK = "a[href], button, input, select, textarea, label, summary, "
    + "[role='button'], [tabindex], [contenteditable], iframe, dialog, .toggletip";
  // How long a band click waits for a second click, so a double or triple
  // click that selects words never closes the tile under the pointer.
  const SECOND_CLICK_MS = 300;

  function setOpen(button, open) {
    // Open or close one tile's details band.
    const band = document.getElementById(button.getAttribute("aria-controls"));
    if (!band) return;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    band.hidden = !open;
  }

  function namedToggle(hash) {
    // The toggle of the tile ``hash`` ("#health-backups") names, or null.
    let id = "";
    try {
      id = decodeURIComponent((hash || "").replace(/^#/, ""));
    } catch {
      return null;
    }
    const tile = id && document.getElementById(id);
    if (!tile || !tile.matches("[data-health-tile]")) return null;
    return tile.querySelector("[data-health-toggle]");
  }

  function openNamed(hash) {
    // Open the tile ``hash`` names, if it names one; close nothing.
    const button = namedToggle(hash);
    if (button) setOpen(button, true);
  }

  function openOnlyNamed(hash) {
    // On load: open exactly the tile ``hash`` names and close every other,
    // then bring it into view, since the closed bands above it moved it
    // from where the browser scrolled. No named tile keeps the defaults.
    const named = namedToggle(hash);
    if (!named) return;
    dashboard.querySelectorAll("[data-health-toggle]").forEach((button) => {
      setOpen(button, button === named);
    });
    named.closest("[data-health-tile]").scrollIntoView({ block: "start" });
  }

  dashboard.addEventListener("click", (event) => {
    const button = event.target instanceof Element && event.target.closest("[data-health-toggle]");
    if (button) setOpen(button, button.getAttribute("aria-expanded") !== "true");
  });

  // A click on an open band's blank space or plain text closes its tile,
  // as the toggle would (ui-conventions "Disclosures"). It waits out a
  // second click, and does nothing for a control's own click or a text
  // selection, including the click that only clears one. Focus moves to
  // the toggle, never lost in hidden content.
  let pending = 0;
  let hadSelection = false;
  dashboard.addEventListener("mousedown", () => {
    const selection = window.getSelection();
    hadSelection = Boolean(selection && !selection.isCollapsed);
  });
  dashboard.addEventListener("click", (event) => {
    window.clearTimeout(pending);
    const target = event.target instanceof Element ? event.target : null;
    const band = target && target.closest("[data-health-band]");
    if (!band || event.button !== 0 || event.detail > 1 || hadSelection) return;
    // Only inside the band: <main tabindex="-1"> holds every band.
    const own = target.closest(OWN_CLICK);
    if (own && band.contains(own)) return;
    pending = window.setTimeout(() => {
      const selection = window.getSelection();
      if (band.hidden || (selection && !selection.isCollapsed)) return;
      const button = dashboard.querySelector(`[data-health-toggle][aria-controls="${band.id}"]`);
      if (!button) return;
      setOpen(button, false);
      button.focus({ preventScroll: true });
      if (button.getBoundingClientRect().top < 0) button.scrollIntoView({ block: "nearest" });
    }, SECOND_CLICK_MS);
  });
  // A link on this page to a tile opens it too, even when the address
  // already names it and no hashchange follows.
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element && event.target.closest("a[href*='#']");
    if (!link) return;
    const url = new URL(link.href, window.location.href);
    if (url.pathname === window.location.pathname) openNamed(url.hash);
  });
  window.addEventListener("hashchange", () => openNamed(window.location.hash));
  openOnlyNamed(window.location.hash);

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
