// Shared Admin inactivity warning (every Admin page, setup wizard included).
//
// The server renders the session's idle and absolute deadlines on the page.
// Five minutes before the nearer one, this opens a modal dialog with a live
// countdown. "Stay signed in" posts to the renewal endpoint, which counts as
// Admin activity and returns the renewed idle deadline; it never moves the
// absolute sign-in limit, so near that limit the dialog says so instead of
// offering renewal. Status re-syncs (before warning, on returning to the tab
// and while the dialog is open) use a passive endpoint that renews nothing, so
// activity in another tab can dismiss this tab's warning. Only the server's
// clock decides: local time is anchored to the page's server timestamp.
(() => {
  "use strict";
  const root = document.querySelector("[data-admin-session]");
  const dialog = root?.querySelector("dialog");
  if (!root || !dialog || typeof dialog.showModal !== "function") return;

  const WARN_MS = 5 * 60 * 1000;
  const SYNC_MS = 30 * 1000;
  const form = dialog.querySelector("[data-session-renew]");
  const warning = dialog.querySelector("#session-warning");
  const expired = dialog.querySelector("#session-expired");
  const message = dialog.querySelector("[data-session-message]");
  const countdown = dialog.querySelector("[data-session-countdown]");
  const error = dialog.querySelector("[data-session-error]");
  const stay = dialog.querySelector("[data-session-stay]");
  const signin = dialog.querySelector("[data-session-signin]");
  const announce = root.querySelector("[data-session-announce]");

  let offset = Date.parse(root.dataset.serverNow) - Date.now();
  let idle = Date.parse(root.dataset.idleDeadline);
  let absolute = Date.parse(root.dataset.absoluteDeadline);
  if (![offset, idle, absolute].every(Number.isFinite)) return;

  let signedOut = false;
  let syncing = null;
  let lastSync = 0;
  let lastAnnounced = null;

  const now = () => Date.now() + offset;
  const deadline = () => Math.min(idle, absolute);
  // Renewal cannot help when the absolute limit is the binding deadline.
  const absoluteBound = () => absolute - now() <= WARN_MS;

  // "4:59" style countdown; the spoken form rounds up to whole minutes.
  function clock(ms) {
    const seconds = Math.max(0, Math.ceil(ms / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function minutes(ms) {
    const count = Math.max(1, Math.ceil(ms / 60000));
    return count === 1 ? "1 minute" : `${count} minutes`;
  }

  // Adopt server deadlines from a status or renewal response.
  function adopt(data) {
    const serverNow = Date.parse(data.server_now);
    const nextIdle = Date.parse(data.idle_deadline);
    const nextAbsolute = Date.parse(data.absolute_deadline);
    if (![serverNow, nextIdle, nextAbsolute].every(Number.isFinite)) {
      throw new Error("Invalid session status");
    }
    offset = serverNow - Date.now();
    idle = nextIdle;
    absolute = nextAbsolute;
  }

  // Passive status read; failures leave the local deadlines unchanged, but a
  // 401 means the session has ended, whatever the countdown says (#457 M4).
  async function sync() {
    if (signedOut) return;
    if (syncing) return syncing;
    lastSync = Date.now();
    syncing = (async () => {
      try {
        const response = await fetch(root.dataset.statusUrl, {
          credentials: "same-origin", cache: "no-store",
          headers: {"Accept": "application/json"}
        });
        if (response.status === 401) showExpired();
        else if (response.ok) adopt(await response.json());
      } catch {
        // Offline or unavailable: keep counting down from what we know.
      } finally {
        syncing = null;
      }
    })();
    return syncing;
  }

  // The ended dialog names a cause only when it knows it: a passed deadline is
  // inactivity or the sign-in time limit; a 401 before then means the session
  // ended some other way (signed out in another tab, or access changed).
  function showExpired() {
    signedOut = true;
    const cause = deadline() > now() ? "other" : absolute <= idle ? "limit" : "idle";
    for (const reason of expired.querySelectorAll("[data-session-ended]")) {
      reason.hidden = reason.dataset.sessionEnded !== cause;
    }
    warning.hidden = true;
    expired.hidden = false;
    dialog.setAttribute("aria-labelledby", "session-expired-title");
    dialog.removeAttribute("aria-describedby");
    if (!dialog.open) dialog.showModal();
    expired.querySelector("a")?.focus();
  }

  function render() {
    const remaining = deadline() - now();
    const limited = absoluteBound();
    const template = limited ? message.dataset.absoluteText : message.dataset.idleText;
    message.textContent = template.replace("{time}", minutes(remaining));
    countdown.textContent = clock(remaining);
    stay.hidden = limited;
    signin.hidden = !limited;
    // Announce once per minute, not every second, to avoid a noisy reader.
    const spoken = message.textContent;
    if (spoken !== lastAnnounced) {
      lastAnnounced = spoken;
      announce.textContent = spoken;
    }
  }

  async function tick() {
    if (signedOut) return;
    let remaining = deadline() - now();
    if (remaining <= WARN_MS && !dialog.open) {
      // Another tab may have renewed the session; check before interrupting.
      await sync();
      remaining = deadline() - now();
      if (signedOut || remaining > WARN_MS) return;
      warning.hidden = false;
      expired.hidden = true;
      error.hidden = true;
      render();
      // Two overlapping ticks can both reach here after one shared sync.
      if (!dialog.open) dialog.showModal();
      (absoluteBound() ? signin : stay).focus();
      return;
    }
    if (!dialog.open) return;
    if (remaining <= 0) {
      // Confirm with the server before declaring the session over.
      await sync();
      if (!signedOut && deadline() - now() <= 0) showExpired();
      return;
    }
    if (Date.now() - lastSync >= SYNC_MS) {
      await sync();
      if (signedOut) return;
      if (deadline() - now() > WARN_MS) {
        dialog.close();
        return;
      }
    }
    render();
  }

  stay.addEventListener("click", async () => {
    if (stay.getAttribute("aria-disabled") === "true") return;
    stay.setAttribute("aria-disabled", "true");
    error.hidden = true;
    try {
      const token = form.querySelector('input[name="csrfmiddlewaretoken"]').value;
      const response = await fetch(form.action, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Accept": "application/json", "X-CSRFToken": token},
        body: new FormData(form)
      });
      if (response.status === 401) {
        showExpired();
        return;
      }
      if (!response.ok) throw new Error("Renewal refused");
      adopt(await response.json());
      dialog.close();
    } catch {
      error.hidden = false;
    } finally {
      stay.removeAttribute("aria-disabled");
    }
  });

  // Escape must not silently dismiss the warning: the Admin chooses to stay.
  dialog.addEventListener("cancel", (event) => event.preventDefault());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) sync().then(tick);
  });
  window.setInterval(tick, 1000);
})();
