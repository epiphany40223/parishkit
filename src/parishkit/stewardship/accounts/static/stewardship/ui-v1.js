"use strict";

// Progressive enhancement only: never store answers or credentials in browser
// storage, and never convert date-only campaign buckets into browser dates.
(() => {
  // Family routes answer a stale page token with a 403 JSON body
  // {error: "csrf_failed", csrf_token}; the rejected request did nothing and
  // the session is still valid. Install the fresh token in every page token
  // field (sign-out form, keepalive, presence and family-v1.js all read them)
  // and report whether the caller may retry once. Shared with family-v1.js.
  async function recoverCsrf(response) {
    if (response.status !== 403 ||
        !response.headers.get("Content-Type")?.includes("application/json")) return false;
    const denial = await response.json().catch(() => null);
    if (denial?.error !== "csrf_failed" || typeof denial.csrf_token !== "string" ||
        !denial.csrf_token) return false;
    document.querySelectorAll('[name="csrfmiddlewaretoken"]').forEach((input) => {
      input.value = denial.csrf_token;
    });
    return true;
  }
  window.stewardshipRecoverCsrf = recoverCsrf;

  document.querySelectorAll("time[data-local-instant]").forEach((node) => {
    const date = new Date(node.dateTime);
    if (!Number.isFinite(date.getTime()) || typeof Intl === "undefined") return;
    node.textContent = new Intl.DateTimeFormat("en-US", {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    }).format(date);
  });

  const summary = document.querySelector("[data-error-summary]");
  if (summary) {
    summary.focus();
    summary.querySelectorAll('a[href^="#"]').forEach((link) => {
      link.addEventListener("click", (event) => {
        const target = document.getElementById(link.hash.slice(1));
        if (target) { event.preventDefault(); target.focus(); }
      });
    });
  }
  document.querySelectorAll("input, select, textarea").forEach((field) => {
    field.addEventListener("blur", () => {
      if (field.willValidate) {
        field.setAttribute("aria-invalid", String(!field.validity.valid));
      }
    });
  });

  // Readiness status is a passive GET, never the source-load idle-renewal
  // exception. No message content, key or answer is retained by this poller.
  document.querySelectorAll("[data-setup-mail]").forEach((panel) => {
    const button = panel.querySelector("[data-mail-send]");
    const warning = panel.querySelector("[data-mail-status-error]");
    const uncertain = panel.querySelector("[data-mail-uncertain]");
    const acknowledgement = uncertain?.querySelector("input");
    const states = new Set([
      "queued", "submitting", "accepted", "not_sent", "delivery_unknown", "cancelled"
    ]);
    if (!button || !warning || !uncertain || !acknowledgement) return;
    acknowledgement.required = !uncertain.hidden;
    let pending = panel.dataset.mailPending === "true";
    let inFlight = false;
    let stopped = false;
    let activeRequest = null;
    async function refresh() {
      if (!pending || inFlight || stopped || document.hidden) return;
      inFlight = true;
      const controller = new AbortController();
      activeRequest = controller;
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(panel.dataset.mailStatusUrl, {
          method: "GET", credentials: "same-origin", cache: "no-store",
          headers: {"Accept": "application/json"}, signal: controller.signal
        });
        if (!response.ok) throw new Error("status unavailable");
        const data = await response.json();
        if (!Number.isSafeInteger(data.revision) ||
            String(data.revision) !== panel.dataset.mailRevision ||
            typeof data.pending !== "boolean" || typeof data.unknown !== "boolean" ||
            !Array.isArray(data.items) || data.items.length > 25) {
          throw new Error("status changed");
        }
        const nodes = new Map([...panel.querySelectorAll("[data-mail-id]")]
          .map((node) => [node.dataset.mailId, node.querySelector("[data-mail-state]")]));
        for (const item of data.items) {
          if (!states.has(item.state) || typeof item.label !== "string" ||
              item.label.length > 512 || !nodes.get(item.id)) {
            throw new Error("status changed");
          }
        }
        for (const item of data.items) nodes.get(item.id).textContent = item.label;
        pending = data.pending;
        button.disabled = pending;
        uncertain.hidden = !data.unknown;
        acknowledgement.required = data.unknown;
      } catch {
        stopped = true;
        button.disabled = true;
        warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        activeRequest = null;
        inFlight = false;
      }
    }
    const timer = window.setInterval(refresh, 5000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("pagehide", () => {
      stopped = true;
      window.clearInterval(timer);
      activeRequest?.abort();
    });
    refresh();
  });

  // Only the exact source-progress page posts this renewal exception. The
  // server checks the original login, task/source leases and five-minute limit;
  // visibility and local deadlines merely stop unnecessary browser requests.
  document.querySelectorAll("[data-setup-progress]").forEach((panel) => {
    const form = panel.querySelector("form");
    const warning = panel.querySelector("[data-progress-unavailable]");
    const deadlines = [...panel.querySelectorAll("[data-progress-deadline]")];
    const number = new Intl.NumberFormat("en-US");
    const localTime = new Intl.DateTimeFormat("en-US", {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    });
    let active = panel.dataset.progressActive === "true";
    let closed = false, pending = false, timer = null, controller = null;
    // Anchor server instants to monotonic elapsed time, not the browser's wall
    // clock. Clock skew or a later system-clock correction must not stop renewal.
    let serverAt = Date.parse(panel.dataset.progressNow), observedAt = performance.now();
    const live = () => {
      const until = Math.min(...deadlines.map(node => Date.parse(node.dateTime)));
      const now = serverAt + performance.now() - observedAt;
      return !closed && active && Number.isFinite(until) && Number.isFinite(now) && now < until;
    };
    const schedule = () => {
      window.clearTimeout(timer);
      if (live()) timer = window.setTimeout(refresh, 15000);
    };
    async function refresh() {
      if (!live() || pending) return;
      if (document.hidden) { schedule(); return; }
      pending = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch(panel.dataset.progressUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"Content-Type": "application/x-www-form-urlencoded"},
          body: new URLSearchParams(new FormData(form)), signal: controller.signal
        });
        if (!response.ok) throw new Error("unavailable");
        const data = await response.json();
        if (data.task_id !== panel.dataset.progressTask || typeof data.active !== "boolean"
            || !Number.isFinite(Date.parse(data.server_now))
            || !Number.isSafeInteger(data.current) || !Number.isSafeInteger(data.total)
            || data.current < 0 || data.total < data.current
            || !["queued", "running", "retry_wait", "abandoned", "succeeded", "failed", "cancelled"].includes(data.task_state)
            || !["collecting", "loading", "frozen", "completed", "expired"].includes(data.setup_state)
            || typeof data.phase !== "string"
            || deadlines.some(node => !Number.isFinite(Date.parse(data[node.dataset.progressDeadline])))) {
          throw new Error("unavailable");
        }
        if (closed || document.hidden) return;
        serverAt = Date.parse(data.server_now);
        observedAt = performance.now();
        active = data.active;
        panel.querySelector("[data-task-state]").textContent = data.task_state;
        panel.querySelector("[data-setup-state]").textContent = data.setup_state;
        panel.querySelector("[data-task-phase]").textContent = data.phase;
        const percentage = data.total ? Math.round(data.current * 100 / data.total) : 0;
        panel.querySelector("[data-task-counts]").textContent =
          `${number.format(data.current)} out of ${number.format(data.total)} (${percentage}%)`;
        deadlines.forEach(node => {
          node.dateTime = data[node.dataset.progressDeadline];
          node.textContent = localTime.format(new Date(node.dateTime));
        });
        warning.hidden = true;
      } catch (_) {
        if (!closed && !document.hidden) warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        pending = false;
        controller = null;
        schedule();
      }
    }
    form.addEventListener("submit", event => {
      // A stopped automatic poller must not disable the normal manual form.
      if (live()) { event.preventDefault(); refresh(); }
    });
    window.addEventListener("pagehide", () => {
      closed = true;
      window.clearTimeout(timer);
      if (controller) controller.abort();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && controller) controller.abort();
      if (!document.hidden) refresh();
    });
    refresh();
  });

  // Optional modules remain ordinary accessible fieldsets without JavaScript.
  // Hidden fields are disabled, not silently copied into submitted data. The
  // server independently rejects stray data for every disabled module.
  document.querySelectorAll("[data-campaign-form]").forEach((form) => {
    form.querySelectorAll("[data-campaign-module]").forEach((group) => {
      const toggle = document.getElementById(group.dataset.campaignModule);
      if (!toggle) return;
      let initialized = toggle.checked || !form.hasAttribute("data-new-campaign");
      function update() {
        group.hidden = !toggle.checked;
        group.disabled = !toggle.checked;
        if (toggle.checked && !initialized) {
          const ministries = group.querySelector('select[name="ministry_duids"]');
          if (ministries) Array.from(ministries.options).forEach((item) => {
            item.selected = true;
          });
          initialized = true;
        }
      }
      toggle.addEventListener("change", update);
      update();
    });
  });

  // The visual editor starts with server-sanitized markup only. Raw source
  // edits never go through innerHTML: they must round-trip through the preview
  // sanitizer before returning to visual editing. Paste/drop are plain text.
  document.querySelectorAll("[data-content-form]").forEach((form) => {
    const visual = form.querySelector("[data-visual-content]");
    const editor = form.querySelector("[data-content-editor]");
    const source = form.querySelector('textarea[name="html"]');
    if (!visual || !editor || !source) return;
    visual.hidden = false;
    form.querySelector("[data-html-source]").open = false;
    const sync = () => { source.value = editor.innerHTML; };
    editor.addEventListener("input", sync);
    source.addEventListener("input", () => { visual.hidden = true; });
    const selectedRange = () => {
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount) return null;
      const range = selection.getRangeAt(0);
      return editor.contains(range.commonAncestorContainer) ? range : null;
    };
    form.querySelectorAll("[data-content-tag]").forEach((button) => {
      let saved = null;
      button.addEventListener("pointerdown", (event) => {
        saved = selectedRange();
        event.preventDefault(); // Keep the selected text when clicking a tool.
      });
      button.addEventListener("click", () => {
        const tag = button.dataset.contentTag;
        if (!["strong", "em", "p", "h2", "ul"].includes(tag)) return;
        const range = saved || selectedRange();
        saved = null;
        if (!range || !editor.contains(range.commonAncestorContainer)) return;
        const node = document.createElement(tag);
        const target = tag === "ul" ? node.appendChild(document.createElement("li")) : node;
        target.appendChild(range.extractContents());
        if (!target.hasChildNodes()) target.appendChild(document.createElement("br"));
        range.insertNode(node);
        const selection = window.getSelection();
        range.selectNodeContents(target);
        selection.removeAllRanges();
        selection.addRange(range);
        editor.focus();
        sync();
      });
    });
    editor.addEventListener("paste", (event) => {
      event.preventDefault();
      const range = selectedRange();
      if (!range || !event.clipboardData) return;
      range.deleteContents();
      const text = document.createTextNode(event.clipboardData.getData("text/plain"));
      range.insertNode(text);
      range.setStartAfter(text);
      range.collapse(true);
      sync();
    });
    editor.addEventListener("drop", (event) => { event.preventDefault(); });
    editor.addEventListener("click", (event) => {
      if (event.target.closest("a")) event.preventDefault();
    });
  });

  // Presence is observational: these requests never count as user activity.
  // Timers skip hidden tabs and never overlap requests or catch up missed ticks.
  const presenceIndicator = document.querySelector("[data-presence-indicator]");
  let presencePending = false;
  async function refreshPresence() {
    if (document.hidden || presencePending || !presenceIndicator) return;
    presencePending = true;
    const unavailable = document.querySelector("[data-presence-unavailable]");
    try {
      const response = await fetch("/admin/presence?format=count", {
        credentials: "same-origin", cache: "no-store"
      });
      if (!response.ok) throw new Error("Presence unavailable");
      const result = await response.json();
      if (!Number.isSafeInteger(result.count) || result.count < 0) throw new Error("Invalid count");
      presenceIndicator.querySelector("[data-presence-count]").textContent = result.count.toLocaleString("en-US");
      if (unavailable) unavailable.hidden = true;
    } catch {
      if (unavailable) unavailable.hidden = false;
    } finally { presencePending = false; }
  }
  if (presenceIndicator) {
    refreshPresence();
    window.setInterval(refreshPresence, 30000);
  }

  const backgroundIndicator = document.querySelector("[data-background-indicator]");
  let backgroundPending = false;
  async function refreshBackground() {
    if (document.hidden || backgroundPending || !backgroundIndicator) return;
    backgroundPending = true;
    const unavailable = document.querySelector("[data-background-unavailable]");
    try {
      const response = await fetch("/admin/background/counts", {
        credentials: "same-origin", cache: "no-store"
      });
      if (!response.ok) throw new Error("Background work unavailable");
      const result = await response.json();
      const values = ["queued", "running", "retry_wait", "abandoned", "active"].map(
        (key) => result.counts[key]);
      if (values.some((value) => !Number.isSafeInteger(value) || value < 0)) throw new Error("Invalid counts");
      const total = values.slice(0, 4).reduce((sum, value) => sum + value, 0);
      if (!Number.isSafeInteger(total)) throw new Error("Invalid total");
      const deliveryWarning = document.querySelector("[data-delivery-warning]");
      if (deliveryWarning && (!Number.isSafeInteger(result.delivery_unknown) || result.delivery_unknown < 0)) throw new Error("Invalid delivery count");
      backgroundIndicator.querySelector("[data-background-total]").textContent = total.toLocaleString("en-US");
      backgroundIndicator.querySelector("[data-background-running]").textContent = values[4].toLocaleString("en-US");
      if (deliveryWarning) {
        const count = deliveryWarning.querySelector("[data-delivery-unknown]");
        const formatted = result.delivery_unknown.toLocaleString("en-US");
        if (count.textContent !== formatted) count.textContent = formatted;
        const hidden = result.delivery_unknown === 0;
        if (deliveryWarning.hidden !== hidden) deliveryWarning.hidden = hidden;
      }
      if (unavailable) unavailable.hidden = true;
    } catch {
      if (unavailable) unavailable.hidden = false;
    } finally { backgroundPending = false; }
  }
  if (backgroundIndicator) window.setInterval(refreshBackground, 30000);

  const session = document.querySelector("[data-family-session], [data-admin-session]");
  if (!session) return;
  const warning = document.getElementById("session-warning");
  const expired = document.getElementById("session-expired");
  const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
  const offset = Date.parse(session.dataset.serverNow) - Date.now();
  let deadline = Date.parse(session.dataset.idleDeadline);
  const absolute = Date.parse(session.dataset.absoluteDeadline);
  let dirty = false;
  let pending = false;
  let lastAttempt = Date.now();
  let expiryAnnounced = false;
  let familyFinished = false;
  document.addEventListener("stewardship:family-finished", () => {
    if (!session.hasAttribute("data-family-session")) return;
    familyFinished = true;
    warning.hidden = expired.hidden = true;
  });
  if (![offset, deadline, absolute].every(Number.isFinite)) return;

  let familyPresencePending = false;
  async function familyPresence() {
    if (familyFinished || !session.hasAttribute("data-family-session") || document.hidden ||
        familyPresencePending || !csrf ||
        Date.now() + offset >= Math.min(deadline, absolute)) return;
    familyPresencePending = true;
    const beat = () => fetch("/family/presence", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"X-CSRFToken": csrf.value, "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams({section: session.dataset.presenceSection || "welcome"})
    });
    try {
      // One retry with a refreshed token; a second failure waits for the next beat.
      if (await recoverCsrf(await beat())) await beat();
    } catch {
      // Presence failure neither renews the session nor replays any form values.
    } finally { familyPresencePending = false; }
  }
  if (session.hasAttribute("data-family-session")) {
    familyPresence();
    window.setInterval(familyPresence, 30000);
  }

  // Polling, focus and visibility do not imply activity. The empty server-side
  // keepalive is explicitly an untrusted claim, capped there too at five minutes.
  ["input", "keydown", "pointerdown"].forEach((event) => {
    document.addEventListener(event, () => { dirty = true; }, {passive: true});
  });
  async function tick() {
    if (familyFinished) return;
    const now = Date.now() + offset;
    const remaining = Math.min(deadline, absolute) - now;
    warning.hidden = remaining > 300000 || remaining <= 0;
    expired.hidden = remaining > 0;
    if (remaining <= 0 && !expiryAnnounced && session.hasAttribute("data-family-session")) {
      expiryAnnounced = true;
      document.dispatchEvent(new Event("stewardship:family-expired"));
    }
    if (!session.hasAttribute("data-family-session") || remaining <= 0 ||
        !dirty || pending || document.hidden || !csrf ||
        Date.now() - lastAttempt < 300000) return;
    pending = true;
    dirty = false;
    lastAttempt = Date.now();
    const claim = () => fetch("/family/keepalive", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"}
    });
    try {
      let response = await claim();
      // A stale token never reached the view, so the claim is still unused:
      // retry it once with the refreshed token instead of failing forever.
      if (await recoverCsrf(response)) response = await claim();
      if (response.ok) {
        const result = await response.json();
        const next = Date.parse(result.idle_deadline);
        if (!Number.isFinite(next)) throw new Error("Invalid activity response");
        deadline = Math.min(next, absolute);
      } else {
        dirty = true;
      }
    } catch {
      // Retry the unconsumed claim at the next allowed five-minute interval.
      // Failure never extends the local deadline or bypasses server admission.
      dirty = true;
    } finally { pending = false; }
  }
  tick();
  window.setInterval(tick, 15000);
})();
