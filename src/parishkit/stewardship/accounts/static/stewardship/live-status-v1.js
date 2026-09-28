// Live status regions: pages that follow background work update themselves.
//
// A page marks the part that changes with [data-live-status="<name>"] and,
// while the work is still queued or running, adds [data-live-pending]. This
// script re-reads the same page (every status page is a passive read that
// never extends a sign-in) and swaps in the fresh region's contents: every
// 2 s at first, backing off to 10 s. It pauses while the tab is hidden,
// checks again as soon as it is shown, and stops once the region is no
// longer pending. The region element itself stays in place, so its
// aria-live announcement reaches screen readers. The page's manual refresh
// link remains the no-JavaScript fallback.
//
// Never disrupt the Admin: while a control inside the region has focus or has
// been edited, the swap waits for the next check. Only the region is swapped;
// the page itself is never reloaded, so a POST response is never re-sent.
//
// When the work finishes while the page is watching, a terminal region may
// ask for one follow-up: an a[data-live-follow] link is opened, or a
// form[data-live-autosubmit] (such as a ready download) is submitted once.
(function () {
  "use strict";
  const DELAYS = [2000, 2000, 3000, 4000, 6000, 8000, 10000];
  // Stop after an hour of watching; the manual refresh link still works.
  const GIVE_UP_MS = 60 * 60 * 1000;
  const region = document.querySelector("[data-live-status]");
  if (!region || !region.hasAttribute("data-live-pending")) return;

  const name = region.getAttribute("data-live-status");
  const started = Date.now();
  let attempt = 0;
  let timer = 0;
  let inFlight = false;
  let stopped = false;

  // One status line after the region reports checking problems.
  const problem = document.createElement("p");
  problem.className = "notice live-status-problem";
  problem.setAttribute("role", "status");
  problem.hidden = true;
  region.after(problem);

  function say(text) {
    problem.textContent = text;
    problem.hidden = !text;
  }

  function localize(scope) {
    // Same formatting as ui-v1.js applies at page load.
    if (typeof Intl === "undefined") return;
    const format = new Intl.DateTimeFormat("en-US", {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    });
    scope.querySelectorAll("time[data-local-instant]").forEach((node) => {
      const date = new Date(node.dateTime);
      if (Number.isFinite(date.getTime())) node.textContent = format.format(date);
    });
  }

  function elapsed() {
    // "started 12 seconds ago" beside a running indicator, when one has a time.
    region.querySelectorAll("time[data-live-since]").forEach((node) => {
      const date = new Date(node.dateTime);
      if (!Number.isFinite(date.getTime())) return;
      const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
      node.textContent = seconds < 90
        ? `${seconds} second${seconds === 1 ? "" : "s"} ago`
        : `${Math.round(seconds / 60)} minutes ago`;
    });
  }

  function stop(message) {
    stopped = true;
    window.clearTimeout(timer);
    window.clearInterval(ticker);
    if (message !== undefined) say(message);
  }

  function schedule() {
    window.clearTimeout(timer);
    if (stopped || document.visibilityState !== "visible") return;
    if (Date.now() - started > GIVE_UP_MS) {
      stop("Still working. Use Refresh status to check again.");
      return;
    }
    timer = window.setTimeout(check, DELAYS[Math.min(attempt, DELAYS.length - 1)]);
    attempt += 1;
  }

  // Controls the Admin has typed into or changed since the last swap.
  let edited = false;
  region.addEventListener("input", () => { edited = true; });
  region.addEventListener("change", () => { edited = true; });

  function busy() {
    // A focused or edited control inside the region must not be replaced.
    const active = document.activeElement;
    return edited || Boolean(active && active !== region && region.contains(active)
      && active.matches("input, select, textarea, button, [contenteditable]"));
  }

  function adopt(fresh) {
    // Keep the region element (and its live announcement); replace what it says.
    region.replaceChildren(...[...fresh.childNodes].map((node) => document.importNode(node, true)));
    region.toggleAttribute("data-live-pending", fresh.hasAttribute("data-live-pending"));
    const state = fresh.getAttribute("data-live-state");
    if (state !== null) region.setAttribute("data-live-state", state);
    localize(region);
    elapsed();
  }

  function finish() {
    stop("");
    const follow = region.querySelector("a[data-live-follow]");
    if (follow) {
      window.location.assign(follow.href);
      return;
    }
    const form = region.querySelector("form[data-live-autosubmit]");
    if (form) {
      form.querySelector("button")?.focus();
      form.requestSubmit();
    }
  }

  async function check() {
    if (stopped || inFlight || document.visibilityState !== "visible") return;
    inFlight = true;
    try {
      const response = await fetch(window.location.href, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" }
      });
      if ([401, 403, 404].includes(response.status)) {
        // Signed out or no longer allowed: retrying cannot help.
        stop("Couldn't check status. Refresh the page or sign in again.");
        return;
      }
      if (!response.ok) throw new Error("status unavailable");
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      const fresh = [...parsed.querySelectorAll("[data-live-status]")]
        .find((node) => node.getAttribute("data-live-status") === name);
      if (!fresh) throw new Error("status region missing");
      say("");
      if (busy()) {
        // Wait for the Admin; check again later without replacing their input.
        schedule();
        return;
      }
      adopt(fresh);
      if (region.hasAttribute("data-live-pending")) schedule();
      else finish();
    } catch {
      say("Couldn't check status — retrying.");
      schedule();
    } finally {
      inFlight = false;
    }
  }

  const ticker = window.setInterval(elapsed, 1000);
  elapsed();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !stopped) check();
    else window.clearTimeout(timer);
  });
  schedule();
})();
