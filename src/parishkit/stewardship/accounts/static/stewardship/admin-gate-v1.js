"use strict";

// The Admin portal requires JavaScript (#565). Admin pages render with a
// js-required class on <html>, which hides the page behind a "needs
// JavaScript" panel (ui-v1.css). This file loads in <head> without defer, so
// removing the class here happens before the body is parsed and a browser
// with JavaScript never shows the panel. Keep this file this small: it
// blocks page rendering while it loads.
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

  // Set data-theme from the choice and show the choice in the select.
  function apply() {
    const chosen = choice();
    const scheme = chosen === "auto" ? (device.matches ? "dark" : "light") : chosen;
    document.documentElement.dataset.theme = scheme;
    const select = document.querySelector("[data-appearance]");
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
  device.addEventListener("change", apply);
  // Another Admin tab changed the choice, or the page came back from the
  // back/forward cache after one did.
  window.addEventListener("storage", (event) => {
    if (event.key === KEY || event.key === null) apply();
  });
  window.addEventListener("pageshow", apply);
  document.addEventListener("DOMContentLoaded", apply);
  // The select does not exist yet, so listen on the document.
  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-appearance]")) save(event.target.value);
  });
})();
