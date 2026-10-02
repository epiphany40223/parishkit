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
//
// A region that follows a steadily changing count (such as Family email
// progress) may set data-live-interval="<ms>" to poll at that fixed pace
// (2-60 s) instead of backing off. Such a region is not itself aria-live,
// because its counts change on every poll; it carries a short
// data-live-announce sentence instead, which is copied into the page's
// [data-live-announcer] element only when that sentence changes.
//
// A region may also set data-live-give-up="<ms>" (at most 3 hours) to
// replace the one-hour limit; the limit then counts from the last poll that
// brought new content, so a long send watched from well before it starts
// keeps updating. data-live-refresh-label names the page's own refresh link
// in the message shown when watching stops.
(function () {
  "use strict";
  const DELAYS = [2000, 2000, 3000, 4000, 6000, 8000, 10000];
  // Stop after an hour of watching; the manual refresh link still works.
  const GIVE_UP_MS = 60 * 60 * 1000;
  const region = document.querySelector("[data-live-status]");
  if (!region || !region.hasAttribute("data-live-pending")) return;

  const name = region.getAttribute("data-live-status");
  // A page whose own view counts as activity names a passive status URL to
  // read instead (same-origin paths only); otherwise it re-reads itself.
  const own = region.getAttribute("data-live-url");
  const source = own && own.startsWith("/") && !own.startsWith("//")
    ? own : window.location.href;
  // When watching started, or (for a region with its own limit) when it
  // last brought new content.
  let started = Date.now();
  // When the latest check was sent. The limit is judged from it, not from
  // the moment its answer is read: if the clock jumps while a check is in
  // flight (a computer waking from sleep), the answer predates the jump, so
  // one fresh check must still run before watching stops.
  let asked = started;
  const ownLimit = Number(region.getAttribute("data-live-give-up"));
  const renews = Number.isFinite(ownLimit) && ownLimit > 0;
  const giveUp = renews ? Math.min(ownLimit, 3 * GIVE_UP_MS) : GIVE_UP_MS;
  const refreshLabel = region.getAttribute("data-live-refresh-label") || "Refresh status";
  // A fixed pace, when the region asks for one, within sensible bounds.
  const interval = Number(region.getAttribute("data-live-interval"));
  const fixed = Number.isFinite(interval) && interval > 0
    ? Math.min(Math.max(interval, 2000), 60000) : 0;
  const announcer = [...document.querySelectorAll("[data-live-announcer]")]
    .find((node) => node.getAttribute("data-live-announcer") === name);
  // The page's first rendering is not news; announce only later changes.
  let announced = region.getAttribute("data-live-announce") || "";
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
    // Same parish date format as ui-v1.js applies at page load.
    if (window.ParishDates) window.ParishDates.localize(scope);
  }

  // Server clock minus this computer's clock. The times being counted from
  // are server times, so a computer whose clock is off would otherwise show
  // a wrong (even negative) elapsed time. The Admin session chrome renders
  // the server's time into every Admin page; without it, trust this clock.
  const serverNow = Date.parse(
    document.querySelector("[data-admin-session]")?.getAttribute("data-server-now") || "");
  const skew = Number.isFinite(serverNow) ? serverNow - Date.now() : 0;

  function ago(seconds) {
    // Whole units, rounded down, in the same words as the server's first
    // rendering (jobs/queue_wait.py waited_words).
    const [count, unit] = seconds < 60 ? [seconds, "second"]
      : seconds < 3600 ? [Math.floor(seconds / 60), "minute"]
        : [Math.floor(seconds / 3600), "hour"];
    return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
  }

  function elapsed() {
    // "started 12 seconds ago" beside a running indicator, when one has a time.
    region.querySelectorAll("time[data-live-since]").forEach((node) => {
      const date = new Date(node.dateTime);
      if (!Number.isFinite(date.getTime())) return;
      const seconds = Math.max(0, Math.floor((Date.now() + skew - date.getTime()) / 1000));
      node.textContent = ago(seconds);
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
    if (asked - started > giveUp) {
      stop(`Still working. Use ${refreshLabel} to check again.`);
      return;
    }
    timer = window.setTimeout(check, fixed || DELAYS[Math.min(attempt, DELAYS.length - 1)]);
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

  // The markup last adopted, so an unchanged poll leaves the live region
  // alone: rebuilding it would make screen readers repeat the same status.
  let lastMarkup = region.innerHTML;

  function adopt(fresh) {
    // Keep the region element (and its live announcement); replace what it says.
    if (fresh.innerHTML === lastMarkup
        && fresh.hasAttribute("data-live-pending") === region.hasAttribute("data-live-pending")) {
      return;
    }
    lastMarkup = fresh.innerHTML;
    if (renews) started = Date.now();
    region.replaceChildren(...[...fresh.childNodes].map((node) => document.importNode(node, true)));
    // Mirror the fresh region's attributes (pending, state markers such as
    // data-export-state) so the page and its tests see the current state.
    [...region.attributes].forEach((attribute) => {
      if (attribute.name !== "data-live-url" && !fresh.hasAttribute(attribute.name)) {
        region.removeAttribute(attribute.name);
      }
    });
    [...fresh.attributes].forEach((attribute) => {
      region.setAttribute(attribute.name, attribute.value);
    });
    localize(region);
    elapsed();
    announce();
  }

  function announce() {
    // Polite and only on change, so a screen reader is not interrupted by
    // every poll's new counts.
    const text = region.getAttribute("data-live-announce") || "";
    if (!announcer || text === announced) return;
    announced = text;
    announcer.textContent = text;
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
    asked = Date.now();
    try {
      const response = await fetch(source, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" }
      });
      if ([401, 403, 404].includes(response.status) || response.redirected) {
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
