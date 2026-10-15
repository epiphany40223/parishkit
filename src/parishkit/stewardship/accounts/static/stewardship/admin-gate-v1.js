"use strict";

// The Admin portal requires JavaScript (#565). Admin pages render with a
// js-required class on <html>, which hides the page behind a "needs
// JavaScript" panel (ui-v1.css). This file loads in <head> without defer, so
// removing the class here happens before the body is parsed and a browser
// with JavaScript never shows the panel. Keep this file small, because it
// blocks page rendering while it loads: test_admin_javascript_gate.py holds
// it to a byte budget.
document.documentElement.classList.remove("js-required");

// The Admin portal's light or dark scheme (#1264; ui-conventions "Colour
// scheme"). It runs here, before the body is parsed, so a page never shows
// in the wrong scheme first. data-theme on <html> always holds the scheme in
// use, "light" or "dark", and ui-v1.css draws the dark tokens from it. The
// sidebar's Appearance select saves an explicit choice per browser; with no
// saved choice ("Match this device"), the operating system decides and a
// change there applies at once. Family pages never load this file and stay
// light. Storage can be unavailable (a private window, blocked site data),
// so every access is guarded; a choice then lasts only for this page.
(() => {
  const KEY = "pk-admin-appearance";
  const CHOICES = ["light", "dark"];
  const device = window.matchMedia("(prefers-color-scheme: dark)");
  let unsaved = null; // This page's choice when storage refused it.

  // The saved choice, "light", "dark" or "auto" (nothing saved).
  function choice() {
    if (unsaved) return unsaved;
    try {
      const saved = window.localStorage.getItem(KEY);
      return CHOICES.includes(saved) ? saved : "auto";
    } catch {
      return "auto";
    }
  }

  // Set data-theme from the choice and show the choice in the select. The
  // choice is also kept as data-theme-choice, from which
  // admin-menu-state-v1.js, running right after the sidebar is parsed,
  // shows it in the select before the rest of the page is drawn.
  function apply() {
    const root = document.documentElement;
    const chosen = choice();
    root.dataset.themeChoice = chosen;
    root.dataset.theme = chosen === "auto" ? (device.matches ? "dark" : "light") : chosen;
    const select = document.querySelector("select[data-appearance]");
    if (select) select.value = chosen;
  }

  // Save the select's new choice ("auto" saves nothing), then apply it in place.
  function save(chosen) {
    try {
      if (chosen === "auto") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, chosen);
      unsaved = null;
    } catch {
      unsaved = chosen;
    }
    apply();
  }

  apply();
  // An assignment, not addEventListener: a page replaced through
  // document.open (ui-v1.js's returned-page view) can run this file again, and
  // the MediaQueryList outlives the old document, so listeners would pile up.
  device.onchange = apply;
  // Another Admin tab changed the choice, or the page came back from the
  // back/forward cache after one did.
  window.addEventListener("storage", (event) => {
    if (event.key === KEY || event.key === null) apply();
  });
  window.addEventListener("pageshow", apply);
  document.addEventListener("DOMContentLoaded", apply);
  // The select does not exist yet, so listen on the document.
  document.addEventListener("change", (event) => {
    if (event.target.matches("select[data-appearance]")) save(event.target.value);
  });
})();
