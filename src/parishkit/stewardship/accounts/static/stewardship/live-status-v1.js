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
// Never disrupt the Admin: while a form control inside the region has focus
// or has been edited, the swap waits for the next check; while text in it
// is selected, for at most a minute from when it was selected. A poll that
// brings nothing new leaves the region alone. A swap puts focus back on the
// matching link or disclosure and keeps each wide table's sideways scroll,
// so a keyboard reader keeps their place. Only the region is swapped; the
// page itself is never reloaded, so a POST response is never re-sent.
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

  // When the Admin began selecting text in the region (0 when not), kept up
  // to date as the selection changes, so the hold is measured from the
  // moment the selection was made, not from the first poll that saw it.
  let selectingSince = 0;
  // A selection holds the swap for at most this long; then it is replaced.
  const SELECTION_HOLD_MS = 60 * 1000;

  function selecting() {
    // Whether text inside the region is selected right now.
    const selection = window.getSelection ? window.getSelection() : null;
    return Boolean(selection && !selection.isCollapsed && selection.rangeCount
      && region.contains(selection.getRangeAt(0).commonAncestorContainer));
  }

  document.addEventListener("selectionchange", () => {
    if (!selecting()) selectingSince = 0;
    else if (!selectingSince) selectingSince = Date.now();
  });

  function busy() {
    // A focused or edited form control inside the region must not be
    // replaced: that would lose what the Admin is typing. Focused links and
    // disclosures do not hold the swap (a mouse click focuses them too, and
    // would freeze the region); adopt() puts focus back on them instead.
    const active = document.activeElement;
    const holding = selecting() && selectingSince
      && Date.now() - selectingSince < SELECTION_HOLD_MS;
    return edited || Boolean(holding) || Boolean(active && active !== region
      && region.contains(active)
      && active.matches("input, select, textarea, button, [contenteditable]"));
  }

  function focusKey(node) {
    // How to find the focused element again in the new markup: its id, else
    // its tag, link target and text.
    if (!node || node === region || !region.contains(node)) return null;
    return node.id ? { id: node.id }
      : { tag: node.tagName, href: node.getAttribute("href"), text: node.textContent };
  }

  function refocus(key) {
    // Focus the new element matching ``key`` without scrolling, if any.
    if (!key) return;
    const match = key.id ? document.getElementById(key.id)
      : [...region.querySelectorAll(key.tag)].find((node) =>
        node.getAttribute("href") === key.href && node.textContent === key.text);
    if (match && region.contains(match)) match.focus({ preventScroll: true });
  }

  function markup(node) {
    // What the server said, without how this browser worded its times:
    // ui-v1.js and this script rewrite <time> text in place (local dates,
    // "2 minutes ago"), so comparing raw HTML would rebuild the region on
    // the first poll even when nothing changed.
    const copy = node.cloneNode(true);
    copy.querySelectorAll("time").forEach((time) => { time.textContent = ""; });
    return copy.innerHTML;
  }

  // The markup last adopted, so an unchanged poll leaves the live region
  // alone: rebuilding it would make screen readers repeat the same status
  // and lose the reader's place.
  let lastMarkup = markup(region);

  function checked() {
    // "Last checked" lines (time[data-live-checked], usually outside the
    // region so they never count as a change) move to this successful check.
    document.querySelectorAll("time[data-live-checked]").forEach((node) => {
      node.dateTime = new Date(Date.now() + skew).toISOString();
      node.textContent = node.dateTime;
      localize(node.parentNode);
    });
  }

  function scrollKey(node, index) {
    // A wide table's identity across a swap: its caption, else its position.
    const caption = node.querySelector("caption");
    return caption ? `caption:${caption.textContent.trim()}` : `index:${index}`;
  }

  // The copy each mirror target last took (see mirror()).
  const mirrored = new WeakMap();

  function mirror() {
    // A region may carry copies of controls that live elsewhere on the page
    // (template[data-live-mirror="name"]), such as a button whose state the
    // status decides but which sits outside the region so a poll never
    // discards what is open beside it. Each copy replaces the contents of
    // the matching [data-live-mirror-target="name"], unless the reader is
    // using a control in it. An empty CSRF field in the copy takes this
    // page's own token (the polled markup carries none, so it stays the
    // same from poll to poll).
    //
    // It runs after every successful poll, not only when the region
    // changed: a copy skipped because the reader was using the target (or
    // a target an in-place answer just put back) must catch up once they
    // move on, even if the region stays the same from then on. Each target
    // remembers the copy it last took (mirrored), so an unchanged copy is
    // not put in again and the target's markup stays put between polls.
    const token = document.querySelector('input[name="csrfmiddlewaretoken"][value]:not([value=""])');
    region.querySelectorAll("template[data-live-mirror]").forEach((source) => {
      const name = source.getAttribute("data-live-mirror");
      const html = source.innerHTML;
      document.querySelectorAll("[data-live-mirror-target]").forEach((target) => {
        if (target.getAttribute("data-live-mirror-target") !== name) return;
        if (region.contains(target) || target.contains(document.activeElement)) return;
        if (mirrored.get(target) === html) return;
        const copy = source.content.cloneNode(true);
        copy.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach((input) => {
          if (!input.value && token) input.value = token.value;
        });
        target.replaceChildren(copy);
        mirrored.set(target, html);
        localize(target);
      });
    });
  }

  // Each target was rendered with the page, from the same state as the copy
  // the region carries now, so record that copy as already taken. Otherwise
  // the first poll would replace an unchanged button, and a click landing
  // just then (WebKit) could be lost with the old button.
  region.querySelectorAll("template[data-live-mirror]").forEach((source) => {
    const name = source.getAttribute("data-live-mirror");
    document.querySelectorAll("[data-live-mirror-target]").forEach((target) => {
      if (target.getAttribute("data-live-mirror-target") === name) {
        mirrored.set(target, source.innerHTML);
      }
    });
  });

  function adopt(fresh) {
    // Keep the region element (and its live announcement); replace what it says.
    const freshMarkup = markup(fresh);
    if (freshMarkup === lastMarkup
        && fresh.hasAttribute("data-live-pending") === region.hasAttribute("data-live-pending")) {
      return;
    }
    lastMarkup = freshMarkup;
    if (renews) started = Date.now();
    const key = focusKey(document.activeElement);
    // Wide tables scroll sideways inside .table-scroll; keep each one's
    // offset, matching tables by their caption (a table can come or go
    // above another one), else by position. The page's own scroll is left
    // to the browser's scroll anchoring, which keeps the reader's place when
    // the region above them changes height.
    const sideways = new Map([...region.querySelectorAll(".table-scroll")]
      .map((node, index) => [scrollKey(node, index), node.scrollLeft]));
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
    refocus(key);
    region.querySelectorAll(".table-scroll").forEach((node, index) => {
      const offset = sideways.get(scrollKey(node, index));
      if (offset) node.scrollLeft = offset;
    });
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
      // A reply that arrives after the page went away (pagehide) must not
      // swap, finish or navigate.
      if (stopped) return;
      if ([401, 403, 404].includes(response.status) || response.redirected) {
        // Signed out or no longer allowed: retrying cannot help.
        stop("Couldn't check status. Refresh the page or sign in again.");
        return;
      }
      if (!response.ok) throw new Error("status unavailable");
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      if (stopped) return;
      const fresh = [...parsed.querySelectorAll("[data-live-status]")]
        .find((node) => node.getAttribute("data-live-status") === name);
      if (!fresh) throw new Error("status region missing");
      say("");
      if (busy()) {
        // Wait for the Admin; check again later without replacing their
        // input. "Last checked" stays put: the region was not brought up
        // to date.
        schedule();
        return;
      }
      adopt(fresh);
      mirror();
      checked();
      if (region.hasAttribute("data-live-pending")) schedule();
      else finish();
    } catch {
      if (stopped) return;
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
  // Stop when the page goes away for good: an in-place answer shown as the
  // whole page (ui-v1.js) keeps this window, so this poller would otherwise
  // run beside the new page's own; check() also ignores a reply that
  // arrives after this (#562). A page kept in the back/forward cache
  // (persisted) resumes as before.
  window.addEventListener("pagehide", (event) => { if (!event.persisted) stop(); });
  schedule();
})();
