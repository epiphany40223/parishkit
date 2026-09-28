"use strict";

// Progressive enhancement only: never store answers or credentials in browser
// storage, and never convert date-only campaign buckets into browser dates.
(() => {
  document.querySelectorAll("time[data-local-instant]").forEach((node) => {
    const date = new Date(node.dateTime);
    if (!Number.isFinite(date.getTime()) || typeof Intl === "undefined") return;
    node.textContent = new Intl.DateTimeFormat("en-US", {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    }).format(date);
  });

  // A download's timezone choice offers the browser's own zone, chosen by
  // default; without script the choice stays UTC.
  document.querySelectorAll("select[data-browser-timezone]").forEach((select) => {
    const zone = typeof Intl === "undefined"
      ? "" : Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!zone || zone === "UTC") return;
    select.append(new Option(`${zone} (this browser)`, zone, true, true));
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

  // Shared Admin tables (web/tables.py, table-navigator.html and
  // table-selection.html). Row selection is per page: Select all and the
  // header checkbox choose every enabled row checkbox shown, and bulk action
  // buttons stay disabled until something is chosen. The server still
  // validates every submitted selection.
  document.querySelectorAll("[data-select-table]").forEach((scope) => {
    const rows = () => [...scope.querySelectorAll("input[data-select-row]")]
      .filter((node) => !node.disabled);
    const header = scope.querySelector("input[data-select-all]");
    const button = scope.querySelector("[data-select-all-button]");
    const count = scope.querySelector("[data-selected-count]");
    const actions = scope.querySelectorAll("[data-bulk-action]");
    const update = () => {
      const all = rows();
      const chosen = all.filter((node) => node.checked).length;
      const every = chosen > 0 && chosen === all.length;
      if (header) {
        header.checked = every;
        header.indeterminate = chosen > 0 && !every;
      }
      if (button) {
        button.hidden = !all.length;
        button.textContent = every ? "Clear selection" : "Select all";
      }
      if (count) count.textContent = chosen ? `${chosen} selected` : "";
      actions.forEach((node) => { node.disabled = !chosen; });
    };
    const choose = (checked) => {
      rows().forEach((node) => { node.checked = checked; });
      update();
    };
    header?.addEventListener("change", () => choose(header.checked));
    button?.addEventListener("click", () => {
      const all = rows();
      choose(!(all.length && all.every((node) => node.checked)));
    });
    scope.addEventListener("change", (event) => {
      if (event.target instanceof HTMLInputElement
          && event.target.matches("[data-select-row]")) update();
    });
    update();
  });
  // A new rows-per-page choice applies at once and starts again at page 1.
  document.querySelectorAll("select[data-page-size]").forEach((select) => {
    select.addEventListener("change", () => {
      const page = select.form?.querySelector("[data-page-number]");
      if (page) page.value = "1";
      select.form?.requestSubmit();
    });
  });

  // Ordinary form submissions: show at once that the click registered, and
  // ignore repeats (double clicks, Enter pressed twice) until the browser
  // navigates. The listener is on document, so it runs after each form's own
  // handlers; a submit that page script already took over (defaultPrevented:
  // fetch-driven forms, cancelled confirmations) is left alone. Buttons get
  // aria-disabled, never disabled: a disabled submitter's name/value would be
  // dropped from the request (action=start, page=2, ...). A download or a new
  // tab leaves this page in place, so the form is released after a while, and
  // also when the browser shows this page again from its back/forward cache.
  const busyStatus = document.createElement("span");
  busyStatus.className = "visually-hidden";
  busyStatus.setAttribute("role", "status");
  (document.querySelector("main") || document.body).append(busyStatus);
  const submitting = new Map();
  const submitControls = (form) => [...form.elements].filter((node) =>
    (node instanceof HTMLButtonElement || node instanceof HTMLInputElement)
    && node.type === "submit");
  const release = (form) => {
    window.clearTimeout(submitting.get(form));
    submitting.delete(form);
    form.removeAttribute("aria-busy");
    submitControls(form).forEach((node) => {
      node.classList.remove("is-busy");
      node.removeAttribute("aria-disabled");
    });
    if (!submitting.size) busyStatus.textContent = "";
  };
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (submitting.has(form)) { event.preventDefault(); return; }
    if (event.defaultPrevented || form.hasAttribute("data-submit-repeatable")) return;
    form.setAttribute("aria-busy", "true");
    submitControls(form).forEach((node) => node.setAttribute("aria-disabled", "true"));
    event.submitter?.classList.add("is-busy");
    busyStatus.textContent = "Working…";
    submitting.set(form, window.setTimeout(() => release(form), 10000));
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) [...submitting.keys()].forEach(release);
  });

  // Required acknowledgments: a form's submit buttons stay disabled (muted by
  // button:disabled) until every visible [data-acknowledgment] checkbox that
  // belongs to it is checked. A hidden one (a setup test's "may have arrived"
  // prompt before any doubt) does not count. This is progressive enhancement
  // only: without JavaScript the buttons stay enabled and the server refuses a
  // missing acknowledgment exactly as before. Real disabled is used, unlike
  // the busy state above, because no submission should start at all; only
  // buttons disabled here (data-acknowledgment-gated) are ever re-enabled, so
  // a button the server or another script disabled (a pending test, setup
  // that is not ready) stays disabled. formnovalidate buttons are never gated.
  const gateAcknowledgments = (form) => {
    if (!form) return;
    const boxes = [...form.elements].filter((node) => node instanceof HTMLInputElement
      && node.type === "checkbox" && node.hasAttribute("data-acknowledgment"));
    if (!boxes.length) return;
    const blocked = boxes.some((box) => !box.checked && !box.closest("[hidden]"));
    submitControls(form).forEach((node) => {
      if (node.formNoValidate) return;
      if (blocked && !node.disabled) {
        node.disabled = true;
        node.setAttribute("data-acknowledgment-gated", "");
      } else if (!blocked && node.hasAttribute("data-acknowledgment-gated")) {
        node.disabled = false;
        node.removeAttribute("data-acknowledgment-gated");
      }
    });
  };
  document.querySelectorAll("form").forEach((form) => {
    gateAcknowledgments(form);
    form.addEventListener("change", () => gateAcknowledgments(form));
  });
  // Back/forward navigation can restore checkbox state without a change event.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form").forEach(gateAcknowledgments);
  });

  // Readiness status is a passive GET, never the source-load idle-renewal
  // exception. No message content, key or answer is retained by this poller.
  document.querySelectorAll("[data-setup-mail]").forEach((panel) => {
    const button = panel.querySelector("[data-mail-send]");
    const warning = panel.querySelector("[data-mail-status-error]");
    const uncertain = panel.querySelector("[data-mail-uncertain]");
    const acknowledgement = uncertain?.querySelector("input");
    // Until this revision's test is accepted the send button is the page's
    // primary action and Continue waits hidden (setup-test-actions.html).
    const onward = panel.querySelector("[data-mail-continue]");
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
        if (onward?.hidden && data.items.some((item) =>
          item.current === true && item.state === "accepted")) {
          onward.hidden = false;
          button.hidden = true;
        }
        pending = data.pending;
        // This poller owns the send button's pending state; take it back from
        // the acknowledgment gate, then let the gate apply the new prompt.
        button.removeAttribute("data-acknowledgment-gated");
        button.disabled = pending;
        uncertain.hidden = !data.unknown;
        acknowledgement.required = data.unknown;
        gateAcknowledgments(acknowledgement.form);
      } catch {
        stopped = true;
        button.removeAttribute("data-acknowledgment-gated");
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

  // "Finishing setup" polls a passive GET status (it never renews the setup
  // login) every 15 seconds while visible. The server returns only a short
  // signature of what the page shows: a change reloads the page, and
  // completion reveals the Continue link instead of leaving the page.
  document.querySelectorAll("[data-finishing]").forEach((panel) => {
    const done = document.querySelector("[data-finishing-done]");
    const warning = panel.querySelector("[data-finishing-unavailable]");
    let stopped = false;
    let inFlight = false;
    let timer = null;
    async function refresh() {
      if (stopped || inFlight || document.hidden) return;
      inFlight = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(panel.dataset.finishingUrl, {
          method: "GET", credentials: "same-origin", cache: "no-store",
          headers: {"Accept": "application/json"}, signal: controller.signal
        });
        if (!response.ok) throw new Error("status unavailable");
        const data = await response.json();
        if (typeof data.signature !== "string" || typeof data.overall !== "string") {
          throw new Error("status changed");
        }
        warning.hidden = true;
        if (data.overall === "completed") {
          stopped = true;
          if (done) done.hidden = false;
          panel.hidden = true;
        } else if (data.signature !== panel.dataset.finishingSignature) {
          stopped = true;
          window.location.reload();
        }
      } catch {
        warning.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        inFlight = false;
        if (!stopped) timer = window.setTimeout(refresh, 15000);
      }
    }
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !inFlight) {
        window.clearTimeout(timer);
        refresh();
      }
    });
    window.addEventListener("pagehide", () => {
      stopped = true;
      window.clearTimeout(timer);
    });
    timer = window.setTimeout(refresh, 15000);
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
    // Liveness display: elapsed time and the worker's last report tick locally
    // between checks, so a download whose totals are not yet known (0 of 0)
    // still visibly progresses. These are display-only; nothing is posted.
    const texts = Object.fromEntries([...panel.querySelectorAll("[data-progress-text]")]
      .map(node => [node.dataset.progressText, node.textContent]));
    const summary = panel.querySelector("[data-progress-summary]");
    const bar = panel.querySelector("[data-progress-bar]");
    const elapsed = panel.querySelector("[data-progress-elapsed]");
    const quiet = panel.querySelector("[data-progress-quiet]");
    const checked = panel.querySelector("[data-progress-checked]");
    const done = document.querySelector("[data-progress-done]");
    const failed = document.querySelector("[data-progress-failed]");
    const records = panel.querySelector("[data-load-records]");
    const phaseList = panel.querySelector(".setup-load-phases");
    const collectionList = panel.querySelector("[data-load-collections]");
    const phaseOrder = ["fetching", "staging", "validating"];
    // Fill a server-rendered wording template such as "{count} loaded".
    const fill = (template, values) => template.replace(/\{(\w+)\}/g,
      (match, name) => (name in values ? number.format(values[name]) : match));
    const mark = (node, state, text) => {
      node.classList.remove("setup-load-done", "setup-load-active", "setup-load-waiting");
      node.classList.add(`setup-load-${state}`);
      const status = node.querySelector("[data-load-status]");
      if (status && status.textContent !== text) status.textContent = text;
    };
    // Mirrors setup_progress_views.phases() and collections().
    const showLoad = (data, key) => {
      const index = phaseOrder.indexOf(data.phase);
      phaseList?.querySelectorAll("[data-load-phase]").forEach((node) => {
        const position = phaseOrder.indexOf(node.dataset.loadPhase);
        const state = key === "done" || position < index ? "done"
          : position === index && data.active ? "active" : "waiting";
        mark(node, state, phaseList.dataset[`status${state[0].toUpperCase()}${state.slice(1)}`]);
      });
      if (collectionList) {
        const text = collectionList.dataset;
        const fetching = data.phase === "fetching" && data.active;
        let waiting = false;
        data.collections.forEach((item) => {
          const node = collectionList.querySelector(`[data-collection="${item.key}"]`);
          let state = "waiting";
          if (item.done) state = "done";
          else if (fetching && !waiting) { state = "active"; waiting = true; }
          const rosters = item.key === "ministry_roster" && item.expected !== null;
          mark(node, state, rosters && state !== "waiting" ? fill(text.textRosters, item)
            : state === "done" ? fill(text.textDone, item)
            : state === "active" ? text.textActive : text.textWaiting);
        });
      }
      if (records) records.hidden = !["staging", "validating"].includes(data.phase);
    };
    const validCollections = (items) => Array.isArray(items) && items.length <= 20
      && items.every((item) => item && typeof item.key === "string" && /^[a-z_]{1,40}$/.test(item.key)
        && collectionList?.querySelector(`[data-collection="${item.key}"]`)
        && Number.isSafeInteger(item.count) && item.count >= 0 && typeof item.done === "boolean"
        && [item.finished, item.expected].every((value) => value === null
          || (Number.isSafeInteger(value) && value >= 0)));
    let startedAt = Date.parse(panel.dataset.progressStarted || "");
    let heartbeatAt = Date.parse(panel.dataset.progressHeartbeat || "");
    const duration = (milliseconds) => {
      const seconds = Math.max(0, Math.floor(milliseconds / 1000));
      return seconds < 60 ? `${seconds} seconds`
        : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
    };
    // Mirrors the server's summary() choice of plain-language status text.
    const statusKey = (data) => {
      if (data.setup_state === "expired"
          || ["failed", "cancelled", "abandoned"].includes(data.task_state)) return "failed";
      if (data.task_state === "succeeded" || data.setup_state === "collecting") return "done";
      if (["queued", "retry_wait"].includes(data.task_state)) return data.task_state;
      return data.phase in texts ? data.phase : "working";
    };
    const tick = () => {
      const now = serverAt + performance.now() - observedAt;
      if (elapsed && Number.isFinite(startedAt) && active) elapsed.textContent = duration(now - startedAt);
      if (quiet && Number.isFinite(heartbeatAt) && active) quiet.textContent = `${duration(now - heartbeatAt)} ago`;
    };
    const ticker = window.setInterval(tick, 1000);
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
            || typeof data.phase !== "string" || !validCollections(data.collections)
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
          `${number.format(data.current)} of ${number.format(data.total)} (${percentage}%)`;
        // Unknown totals show an indeterminate bar rather than a stuck 0%.
        if (bar && data.total) { bar.max = data.total; bar.value = data.current; }
        else if (bar) bar.removeAttribute("value");
        if (typeof data.started_at === "string" && Number.isFinite(Date.parse(data.started_at))) {
          startedAt = Date.parse(data.started_at);
        }
        if (typeof data.heartbeat_at === "string" && Number.isFinite(Date.parse(data.heartbeat_at))) {
          heartbeatAt = Date.parse(data.heartbeat_at);
        }
        const key = statusKey(data);
        if (summary && texts[key] && summary.textContent !== texts[key]) summary.textContent = texts[key];
        showLoad(data, key);
        if (done) done.hidden = key !== "done";
        if (failed) failed.hidden = key !== "failed";
        if (checked) {
          checked.dateTime = data.server_now;
          checked.textContent = localTime.format(new Date(serverAt));
        }
        tick();
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
      window.clearInterval(ticker);
      if (controller) controller.abort();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && controller) controller.abort();
      if (!document.hidden) refresh();
    });
    tick();
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

  // A financial period is one year: entering its start fills an empty end
  // with the day before the first anniversary (Feb 29 anniversaries fall on
  // Feb 28, as the server's rule does). Dates stay YYYY-MM-DD text and the
  // arithmetic uses UTC, so no browser time zone can shift the day. An end the
  // person typed is never replaced; one filled here follows later start edits
  // (typing a year passes through values like 0002-01-01). The server still
  // checks every period.
  const periodEnd = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return null;
    const [year, month, day] = match.slice(1).map(Number);
    const end = new Date(0);
    end.setUTCFullYear(year + 1, month - 1, month === 2 && day === 29 ? 28 : day);
    end.setUTCDate(end.getUTCDate() - 1);
    if (!Number.isFinite(end.getTime()) || end.getUTCFullYear() > 9999) return null;
    return [String(end.getUTCFullYear()).padStart(4, "0"),
      String(end.getUTCMonth() + 1).padStart(2, "0"),
      String(end.getUTCDate()).padStart(2, "0")].join("-");
  };
  document.querySelectorAll("input[data-fills-end]").forEach((start) => {
    const end = start.form?.elements[start.dataset.fillsEnd];
    if (!(end instanceof HTMLInputElement)) return;
    const note = document.createElement("span");
    note.className = "help";
    note.setAttribute("role", "status");
    end.after(note);
    const fill = () => {
      const value = periodEnd(start.value);
      if (!value || (end.value && end.value !== end.dataset.autofilled)) return;
      if (end.value === value) return;
      end.value = value;
      end.dataset.autofilled = value;
      note.textContent = "End date filled in; change it if needed.";
      // Let dependent checks (the overlap confirmation) see the new end.
      end.dispatchEvent(new Event("input", {bubbles: true}));
    };
    start.addEventListener("input", fill);
    start.addEventListener("change", fill);
    end.addEventListener("input", (event) => {
      if (event.isTrusted) note.textContent = "";
    });
  });

  // The overlap confirmation is needed only while the financial period and
  // the campaign share at least one day (campaign_forms.overlaps). Hidden, it
  // is also unchecked so a stale confirmation is never submitted. The server
  // renders it visible whenever it is needed, so this is only a convenience.
  document.querySelectorAll("[data-overlap-confirmation]").forEach((group) => {
    const form = group.closest("form");
    const box = group.querySelector('input[type="checkbox"]');
    if (!form || !box) return;
    const read = (key) => {
      const name = group.dataset[`${key}Name`];
      const value = name ? form.elements[name]?.value : group.dataset[`${key}Value`];
      return /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? value : null;
    };
    const update = () => {
      const [start, end, periodStart, periodEnd] =
        ["campaignStart", "campaignEnd", "periodStart", "periodEnd"].map(read);
      const needed = Boolean(start && end && periodStart && periodEnd
        && periodStart <= end && periodEnd >= start);
      group.hidden = !needed;
      if (!needed) box.checked = false;
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  });

  // Mail schedule rows: show only the fields the chosen mail type uses (the
  // row's data-schedule-fields is schedule_forms.FIELDS), and offer only
  // emails of that type. A field hidden here is also cleared, so a stale date
  // or weekday is never submitted. When the page first loads, a field the
  // server reported an error on stays visible with its value, so the message
  // can be read and acted on. Without this script every field shows and the
  // server still explains any value that does not apply.
  const scheduleRow = (row) => {
    let rules;
    try { rules = JSON.parse(row.dataset.scheduleFields); } catch { return; }
    const kind = row.querySelector('[data-schedule-field="kind"] select');
    const template = row.querySelector('[data-schedule-field="template_version"] select');
    if (!kind || !template || !rules) return;
    const governed = new Set(Object.values(rules).flat());
    const emails = [...template.options];
    const update = (firstLoad) => {
      const wanted = rules[kind.value] || [];
      row.querySelectorAll("[data-schedule-field]").forEach((group) => {
        if (!governed.has(group.dataset.scheduleField)) return;
        const show = wanted.includes(group.dataset.scheduleField)
          || (firstLoad && group.querySelector(".errorlist") !== null);
        group.hidden = !show;
        if (!show) group.querySelectorAll("input, select").forEach((control) => {
          control.value = "";
        });
      });
      const selected = template.value;
      template.replaceChildren(...emails.filter((option) =>
        !option.value || !option.dataset.kind || option.dataset.kind === kind.value));
      template.value = [...template.options].some((option) => option.value === selected)
        ? selected : "";
    };
    kind.addEventListener("change", () => update(false));
    update(true);
  };
  document.querySelectorAll("[data-schedule-row]").forEach(scheduleRow);

  // "Add another schedule" clones the formset's empty form (rendered in a
  // <template> with __prefix__ names) as the next index and raises
  // TOTAL_FORMS, so several new schedules save in one submission and the
  // server validates them all together as before. Rows added here can be
  // removed again before saving; later added rows are renumbered so the
  // indexes stay contiguous. Without this script the button stays hidden and
  // each save offers one blank row.
  document.querySelectorAll("[data-schedule-template]").forEach((template) => {
    const form = template.closest("form");
    const total = form?.querySelector('input[name="schedules-TOTAL_FORMS"]');
    const maximum = Number(form?.querySelector('input[name="schedules-MAX_NUM_FORMS"]')?.value);
    const rows = form?.querySelector("[data-schedule-rows]");
    const addRow = form?.querySelector("[data-schedule-add-row]");
    const add = addRow?.querySelector("[data-schedule-add]");
    const status = form?.querySelector("[data-schedule-status]");
    if (!total || !rows || !add || !status) return;
    const first = Number(total.value); // The server's rows keep their indexes.
    const added = [];
    const renumber = (row, index) => {
      row.querySelectorAll("[name], [id], [for], [aria-describedby]").forEach((node) => {
        ["name", "id", "for", "aria-describedby"].forEach((attribute) => {
          const value = node.getAttribute(attribute);
          if (value) node.setAttribute(attribute,
            value.replace(/schedules-(?:\d+|__prefix__)-/g, `schedules-${index}-`));
        });
      });
      row.querySelector("[data-schedule-number]").textContent =
        (index + 1).toLocaleString("en-US");
    };
    const refresh = () => {
      added.forEach((row, offset) => renumber(row, first + offset));
      total.value = String(first + added.length);
      add.disabled = Number.isFinite(maximum) && first + added.length >= maximum;
    };
    add.addEventListener("click", () => {
      const row = template.content.firstElementChild.cloneNode(true);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button-secondary";
      remove.textContent = "Remove this new schedule";
      remove.addEventListener("click", () => {
        added.splice(added.indexOf(row), 1);
        row.remove();
        refresh();
        status.textContent = "New schedule removed.";
        add.focus();
      });
      row.append(remove);
      rows.append(row);
      added.push(row);
      refresh();
      scheduleRow(row);
      status.textContent = `Schedule ${row.querySelector("[data-schedule-number]")
        .textContent} added. Choose its mail type.`;
      row.querySelector('[data-schedule-field="kind"] select')?.focus();
    });
    addRow.hidden = false;
  });

  // Plain multi-select lists: say how many items are chosen, since a long list
  // (a parish may have hundreds of Ministries) hides most of its selection.
  document.querySelectorAll("select[multiple]").forEach((select) => {
    const count = document.createElement("p");
    count.className = "help";
    count.setAttribute("aria-live", "polite");
    select.after(count);
    const show = () => {
      const chosen = select.selectedOptions.length;
      count.textContent = `${chosen.toLocaleString("en-US")} of ${
        select.options.length.toLocaleString("en-US")} selected`;
    };
    // Also on any form change: turning on a module preselects its options
    // without a change event on the list itself.
    (select.form || select).addEventListener("change", show);
    show();
  });

  // Editable regions write one <div> per line in Chrome/WebKit (and a bare
  // first line), <b>/<i> for bold/italic, and trailing <br> placeholders.
  // The server allowlist has no <div>, so without this the lines would reach
  // the sanitizer as one run-on paragraph. Normalize a detached copy (never
  // the live editor, whose caret would jump) into <p> paragraphs: loose
  // top-level text becomes a paragraph, two or more <br> in a row start a new
  // paragraph, and blank lines are dropped (paragraph spacing replaces them).
  // The server applies the same div-to-paragraph rule to pasted or older
  // markup; this only makes the stored source match what the editor showed.
  const inlineContent = (node) => node.nodeType === Node.TEXT_NODE
    || (node.nodeType === Node.ELEMENT_NODE
      && !["P", "H2", "H3", "UL", "OL", "BLOCKQUOTE", "DIV"].includes(node.nodeName));
  const blank = (nodes) => nodes.every((node) => node.nodeName === "BR"
    || (node.nodeType === Node.TEXT_NODE && !node.data.replace(/ /g, " ").trim()));
  const retag = (node, tag) => {
    const replacement = document.createElement(tag);
    replacement.append(...node.childNodes);
    node.replaceWith(replacement);
    return replacement;
  };
  const paragraphs = (nodes) => {
    // Split one line run at each group of 2+ <br>, trimming edge breaks.
    const result = [];
    let current = [];
    let breaks = [];
    const close = () => {
      if (!blank(current)) {
        const paragraph = document.createElement("p");
        paragraph.append(...current);
        result.push(paragraph);
      }
      current = [];
    };
    for (const node of nodes) {
      if (node.nodeName === "BR") { breaks.push(node); continue; }
      if (node.nodeType === Node.TEXT_NODE && !node.data.trim() && breaks.length) continue;
      if (breaks.length >= 2) close();
      else if (breaks.length && current.length) current.push(...breaks);
      breaks = [];
      current.push(node);
    }
    close();
    return result;
  };
  const normalizedSource = (editor) => {
    const copy = editor.cloneNode(true);
    copy.querySelectorAll("b").forEach((node) => retag(node, "strong"));
    copy.querySelectorAll("i").forEach((node) => retag(node, "em"));
    // Innermost first, so a line <div> inside a wrapper <div> is seen first.
    [...copy.querySelectorAll("div")].reverse().forEach((node) => {
      if ([...node.children].some((child) => !inlineContent(child))) {
        node.replaceWith(...node.childNodes);
      } else {
        retag(node, "p");
      }
    });
    // A <p> holding a block (a list inserted mid-paragraph) is split around it.
    const flow = (nodes) => {
      const blocks = [];
      let run = [];
      for (const node of nodes) {
        if (inlineContent(node)) { run.push(node); continue; }
        blocks.push(...paragraphs(run));
        run = [];
        if (node.nodeName === "P") blocks.push(...flow([...node.childNodes]));
        else blocks.push(node);
      }
      blocks.push(...paragraphs(run));
      return blocks;
    };
    copy.replaceChildren(...flow([...copy.childNodes]));
    return copy.innerHTML;
  };

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
    // Enter starts a <p> rather than a <div> where the browser supports it.
    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch { /* optional */ }
    const sync = () => {
      source.value = normalizedSource(editor);
      // Programmatic value changes fire no input event; tell the plain-text
      // preview below that the HTML changed.
      form.dispatchEvent(new Event("stewardship:html-changed"));
    };
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
        // A new block never nests inside the paragraph or heading it was made
        // in: split that line around it and drop any half left empty.
        const host = node.parentElement;
        if (tag !== "strong" && tag !== "em" && host !== editor
            && host.parentElement === editor && /^(P|H2|H3|DIV)$/.test(host.nodeName)) {
          const tail = host.cloneNode(false);
          while (node.nextSibling) tail.append(node.nextSibling);
          host.after(node);
          node.after(tail);
          [host, tail].forEach((part) => { if (blank([...part.childNodes])) part.remove(); });
        }
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
      // Keep the pasted text's lines: each line break becomes a <br> (a blank
      // line, two of them, becomes a paragraph break when the source is
      // normalized). Only text nodes and <br> are created, never parsed markup.
      const fragment = document.createDocumentFragment();
      event.clipboardData.getData("text/plain").replace(/\r\n?/g, "\n").split("\n")
        .forEach((line, index) => {
          if (index) fragment.append(document.createElement("br"));
          if (line) fragment.append(document.createTextNode(line));
        });
      const last = fragment.lastChild;
      if (!last) return;
      range.insertNode(fragment);
      range.setStartAfter(last);
      range.collapse(true);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      sync();
    });
    editor.addEventListener("drop", (event) => { event.preventDefault(); });
    editor.addEventListener("click", (event) => {
      if (event.target.closest("a")) event.preventDefault();
    });
  });

  // "Generate plain text from HTML": while it is checked the plain-text field
  // is read-only and shows the server's own generated text for the current
  // HTML, so the Admin sees exactly what a save stores. "Edit plain text" (or
  // unchecking the box) keeps that text and makes it editable; typed text is
  // never replaced unless the Admin checks the box again. A checked box
  // submits no plain text (the field is disabled just for the submission), so
  // a slightly stale preview cannot conflict with the server's generation;
  // without this script the server refuses typed text that would be dropped.
  document.querySelectorAll("[data-plain-text]").forEach((panel) => {
    const form = panel.closest("form");
    const box = panel.querySelector('input[name="generate_text"]');
    const text = panel.querySelector('textarea[name="text"]');
    const source = form?.querySelector('textarea[name="html"]');
    const note = panel.querySelector("[data-generated-note]");
    const unavailable = panel.querySelector("[data-generated-unavailable]");
    const csrf = form?.querySelector('input[name="csrfmiddlewaretoken"]');
    if (!form || !box || !text || !source || !note || !unavailable || !csrf) return;
    let timer = null;
    let controller = null;
    const refresh = async () => {
      if (!box.checked) return;
      controller?.abort();
      controller = new AbortController();
      const request = controller;
      try {
        const response = await fetch(panel.dataset.plainTextUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"},
          body: new URLSearchParams({html: source.value}), signal: request.signal
        });
        if (!response.ok) throw new Error("preview unavailable");
        const data = await response.json();
        if (typeof data.text !== "string") throw new Error("preview unavailable");
        if (box.checked && controller === request) {
          text.value = data.text;
          unavailable.hidden = true;
        }
      } catch (error) {
        if (error.name !== "AbortError") unavailable.hidden = !box.checked;
      }
    };
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 400);
    };
    const show = () => {
      text.readOnly = box.checked;
      note.hidden = !box.checked;
      if (!box.checked) unavailable.hidden = true;
    };
    const edit = () => {
      box.checked = false;
      show();
      text.focus();
    };
    box.addEventListener("change", () => { show(); if (box.checked) refresh(); });
    panel.querySelector("[data-edit-plain-text]")?.addEventListener("click", edit);
    // Typing into the read-only generated text means "let me edit it".
    text.addEventListener("keydown", (event) => {
      if (!box.checked || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key.length === 1 || ["Backspace", "Delete", "Enter"].includes(event.key)) {
        edit();
      }
    });
    source.addEventListener("input", schedule);
    form.addEventListener("stewardship:html-changed", schedule);
    form.addEventListener("submit", () => { if (box.checked) text.disabled = true; });
    window.addEventListener("pageshow", () => { text.disabled = false; });
    show();
    refresh();
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

  // A page showing work in progress (an integration key being installed)
  // reloads itself every few seconds while visible, until the server renders
  // it without the marker. Its forms are disabled meanwhile, so no typing is lost.
  if (document.querySelector("[data-reload-while-pending]")) {
    window.setInterval(() => {
      if (!document.hidden) window.location.reload();
    }, 5000);
  }

  // Admin pages use the shared inactivity dialog in session-v1.js instead.
  const session = document.querySelector("[data-family-session]");
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
    try {
      await fetch("/family/presence", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": csrf.value, "Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({section: session.dataset.presenceSection || "welcome"})
      });
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
    try {
      const response = await fetch("/family/keepalive", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"}
      });
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
