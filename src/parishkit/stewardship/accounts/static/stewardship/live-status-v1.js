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
// A follow-up link also marked data-in-place is clicked instead, so ui-v1.js
// refreshes the page where the reader is rather than loading it again: an
// in-place settings page's refresh once its change settles (#532) names its
// own address, and a link naming only a fragment (a task page once its task
// ends, #869) re-reads this same address.
//
// A region that follows a steadily changing count (such as Family email
// progress) may set data-live-interval="<ms>" to poll at that fixed pace
// (2-60 s) instead of backing off. Such a region is not itself aria-live,
// because its counts change on every poll; it carries a short
// data-live-announce sentence instead, which is copied into the page's
// [data-live-announcer] element only when that sentence changes.
//
// A pending region may hold a template[data-live-refused]: when a status
// read is refused (signed out, or no longer allowed, such as after a change
// that removed the reader's own access), its content replaces the region as
// a settled final status, so the page never looks as if it were still
// waiting (#952). Without one, the refusal is said under the region.
//
// A region may also set data-live-give-up="<ms>" (at most 3 hours) to
// replace the one-hour limit; the limit then counts from the last poll that
// brought new content, so a long send watched from well before it starts
// keeps updating. data-live-refresh-label names the page's own refresh link
// in the message shown when watching stops.
//
// A region may hold an element marked [data-live-keep="<name>"] for a control
// that must sit inside the status it answers but that a poll must never
// discard: System health's Accept this change once, right under the refused
// ParishSoft change, whose preview and tick box the Administrator may have
// open (#530). Once the element has contents, every swap puts the live
// element back in place of its fresh copy, so what is open in it, ticked or
// focused stays; its contents never count as a change, and typing or
// ticking in it does not hold later swaps (a focused control in it still
// holds the swap, as anywhere in the region). An empty one (drawn before
// there was anything to show) takes the fresh copy's contents, whose empty
// CSRF field takes this page's token, as a mirrored copy's does.
//
// An in-place refresh (ui-v1.js, #519) can replace the region itself, such
// as when Dismiss on an integration's settings page swaps in the page's
// fresh status. The old region's watcher then stops (its page-wide
// listeners removed, its status line taken away), and a fresh region that
// is still pending is watched anew, from the parishkit:swap event ui-v1.js
// dispatches on the swapped-in content.
(function () {
  "use strict";
  const DELAYS = [2000, 2000, 3000, 4000, 6000, 8000, 10000];
  // Stop after an hour of watching; the manual refresh link still works.
  const GIVE_UP_MS = 60 * 60 * 1000;
  // Each watcher's check that stops it once its region has been replaced.
  const watchers = new Set();
  // Each region name's coarse status sentence (data-live-announce) last
  // read out or first shown, shared by the watchers of that name, so a
  // region swapped in by an in-place Refresh is announced only when its
  // status changed.
  const spoken = new Map();
  const announcerFor = (name) => [...document.querySelectorAll("[data-live-announcer]")]
    .find((node) => node.getAttribute("data-live-announcer") === name);
  // Read out ``region``'s coarse status when it differs from the last one.
  const speak = (region) => {
    const name = region.getAttribute("data-live-status");
    const text = region.getAttribute("data-live-announce") || "";
    const announcer = announcerFor(name);
    if (!announcer || text === (spoken.get(name) ?? "")) return;
    spoken.set(name, text);
    announcer.textContent = text;
  };
  const watch = (region) => {
    if (!region || !region.hasAttribute("data-live-pending")) return;
    // This watcher's page-wide listeners, removed when it stops.
    const listening = new AbortController();
    const signal = listening.signal;

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
    // The page's first rendering is not news; announce only later changes.
    if (!spoken.has(name)) spoken.set(name, region.getAttribute("data-live-announce") || "");
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
      listening.abort();
      watchers.delete(gone);
      if (message !== undefined) say(message);
    }

    // An in-place refresh (ui-v1.js) replaced the region: the fresh one has
    // its own watcher, so this one stops quietly and takes its status line.
    function gone() {
      if (region.isConnected) return false;
      stop();
      problem.remove();
      return true;
    }

    function schedule() {
      window.clearTimeout(timer);
      if (stopped || gone() || document.visibilityState !== "visible") return;
      if (asked - started > giveUp) {
        stop(`Still working. Use ${refreshLabel} to check again.`);
        return;
      }
      timer = window.setTimeout(check, fixed || DELAYS[Math.min(attempt, DELAYS.length - 1)]);
      attempt += 1;
    }

    // Controls the Admin has typed into or changed since the last swap. A
    // kept element survives every swap, so its controls need no hold.
    let edited = false;
    const edit = (event) => {
      if (!(event.target instanceof Element && event.target.closest("[data-live-keep]"))) {
        edited = true;
      }
    };
    region.addEventListener("input", edit);
    region.addEventListener("change", edit);

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
    }, { signal });

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
      // the first poll even when nothing changed. A kept element's contents
      // are the reader's, not the status, so they never count either.
      const copy = node.cloneNode(true);
      copy.querySelectorAll("time").forEach((time) => { time.textContent = ""; });
      copy.querySelectorAll("[data-live-keep]").forEach((kept) => { kept.replaceChildren(); });
      return copy.innerHTML;
    }

    function fillToken(root) {
      // An empty CSRF field in polled markup (which carries none, so it stays
      // the same from poll to poll) takes this page's own token.
      const token = document.querySelector('input[name="csrfmiddlewaretoken"][value]:not([value=""])');
      if (!token) return;
      root.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach((input) => {
        if (!input.value) input.value = token.value;
      });
    }

    function keepLive(fresh) {
      // Put each live kept element that has contents in place of its copy
      // in ``fresh`` (a fragment of the fresh region's children), so a swap
      // moves it rather than rebuilding it; an empty one is left to be
      // replaced by its copy, whose CSRF field takes this page's token.
      const live = new Map([...region.querySelectorAll("[data-live-keep]")]
        .filter((node) => node.firstElementChild || node.textContent.trim())
        .map((node) => [node.getAttribute("data-live-keep"), node]));
      fresh.querySelectorAll("[data-live-keep]").forEach((copy) => {
        const kept = live.get(copy.getAttribute("data-live-keep"));
        if (kept) copy.replaceWith(kept);
        else fillToken(copy);
      });
      return fresh;
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
      // A coming time worded against today ("3:10 PM tomorrow") is worded
      // again on every check, so it stays right across midnight. Only its
      // text changes, which the markup comparison ignores.
      region.querySelectorAll("time[data-time-day]").forEach((node) => {
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
      // discards what is open beside it (System health's Take a backup now,
      // #530). Each copy replaces the contents of the matching
      // [data-live-mirror-target="name"], unless the reader is using a
      // control in it. An empty CSRF field in the copy takes this page's own
      // token (the polled markup carries none, so it stays the same from poll
      // to poll).
      //
      // It runs after every successful poll, not only when the region
      // changed: a copy skipped because the reader was using the target (or
      // a target an in-place answer just put back) must catch up once they
      // move on, even if the region stays the same from then on. Each target
      // remembers the copy it last took (mirrored), so an unchanged copy is
      // not put in again and the target's markup stays put between polls.
      //
      // A target inside a kept element (data-live-keep) is the reader's, not
      // the status, so it is mirrored too, as one outside the region is.
      region.querySelectorAll("template[data-live-mirror]").forEach((source) => {
        const mirrorName = source.getAttribute("data-live-mirror");
        const html = source.innerHTML;
        document.querySelectorAll("[data-live-mirror-target]").forEach((target) => {
          if (target.getAttribute("data-live-mirror-target") !== mirrorName) return;
          if (region.contains(target) && !target.closest("[data-live-keep]")) return;
          if (target.contains(document.activeElement)) return;
          if (mirrored.get(target) === html) return;
          const copy = source.content.cloneNode(true);
          fillToken(copy);
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
      const mirrorName = source.getAttribute("data-live-mirror");
      document.querySelectorAll("[data-live-mirror-target]").forEach((target) => {
        if (target.getAttribute("data-live-mirror-target") === mirrorName) {
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
      // Anchoring cannot help at the foot of the page, where a shorter status
      // (Applied is shorter than its running indicator) shortens the page and
      // the browser pulls everything above down. A region inside a
      // [data-keep-height] one (a settings page's review region, ui-v1.js)
      // therefore keeps the height of that one the reader's place needs
      // (#736): enough that the page's foot stays at or below the viewport's
      // bottom edge (a copy of ui-v1.js's heldHeight; see it for why).
      const keep = region.closest("[data-keep-height]");
      if (keep) {
        const root = document.documentElement;
        const height = keep.getBoundingClientRect().height;
        const below = window.scrollY + root.clientHeight - (root.scrollHeight - height);
        const need = Math.min(height, below);
        keep.style.minHeight = need > 0 ? `${need}px` : "";
      }
      const nodes = document.createDocumentFragment();
      nodes.append(...[...fresh.childNodes].map((node) => document.importNode(node, true)));
      region.replaceChildren(keepLive(nodes));
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
      speak(region);
    }

    function refreshPage(link) {
      // Bring the rest of this page up to date where the reader is, as the
      // task page does when its task ends (#869): the region shows the end,
      // but the history table and Retry panels outside it were drawn when the
      // page opened. The link is followed through ui-v1.js's in-place
      // refresh, which re-reads this same address (so a history page or sort
      // the reader chose is kept) and swaps every in-place region; marked
      // data-in-place-quiet, it moves no focus and announces nothing, since
      // this region's own aria-live already said the work ended. A page with
      // no in-place region has nothing more to show.
      const target = document.querySelector("[data-in-place-region][id], [data-table-region][id]");
      if (!target) return;
      const here = new URL(window.location.href);
      here.hash = target.id;
      link.href = here.href;
      link.click();
    }

    function finish() {
      stop("");
      const follow = region.querySelector("a[data-live-follow]");
      if (follow) {
        // An in-place link naming only a fragment re-reads this page
        // (#869); one naming an address is clicked as it is (#532).
        if (follow.hasAttribute("data-in-place") && follow.getAttribute("href").startsWith("#")) refreshPage(follow);
        else if (follow.hasAttribute("data-in-place")) follow.click();
        else window.location.assign(follow.href);
        return;
      }
      const form = region.querySelector("form[data-live-autosubmit]");
      if (form) {
        form.querySelector("button")?.focus();
        form.requestSubmit();
      }
    }

    async function check() {
      if (stopped || gone() || inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      asked = Date.now();
      try {
        const response = await fetch(source, {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "text/html" }
        });
        // A reply that arrives after the page went away (pagehide), or after
        // an in-place refresh replaced the region, must not swap, finish or
        // navigate.
        if (stopped || gone()) return;
        if ([401, 403, 404].includes(response.status) || response.redirected) {
          // Signed out or no longer allowed: retrying cannot help.
          const settled = region.querySelector("template[data-live-refused]");
          if (settled) {
            region.replaceChildren(settled.content.cloneNode(true));
            region.removeAttribute("data-live-pending");
            region.setAttribute("data-live-state", "unknown");
            stop("");
            return;
          }
          stop("Couldn't check status. Refresh the page or sign in again.");
          return;
        }
        if (!response.ok) throw new Error("status unavailable");
        const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
        if (stopped || gone()) return;
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
    }, { signal });
    // Stop when the page goes away for good: an in-place answer shown as the
    // whole page (ui-v1.js) keeps this window, so this poller would otherwise
    // run beside the new page's own; check() also ignores a reply that
    // arrives after this (#562). A page kept in the back/forward cache
    // (persisted) resumes as before.
    window.addEventListener("pagehide", (event) => { if (!event.persisted) stop(); }, { signal });
    schedule();
    watchers.add(gone);
  };
  const first = document.querySelector("[data-live-status]");
  if (first) spoken.set(first.getAttribute("data-live-status"), first.getAttribute("data-live-announce") || "");
  watch(first);
  document.addEventListener("parishkit:swap", (event) => {
    // gone() stops (and so drops) each watcher whose region was replaced.
    [...watchers].forEach((gone) => gone());
    const fresh = event.target;
    if (!(fresh instanceof Element)) return;
    const region = fresh.matches("[data-live-status]") ? fresh : fresh.querySelector("[data-live-status]");
    if (!region) return;
    // A manual Refresh reads out a changed coarse status, as a poll would.
    speak(region);
    watch(region);
  });
})();
