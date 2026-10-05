"use strict";

// Progressive enhancement only: never store answers or credentials in browser
// storage, and never convert date-only campaign buckets into browser dates.
(() => {
  // The Admin sidebar menu is open in the markup so it works without
  // JavaScript. Collapse it on narrow screens, where it sits above the page
  // content, and keep it open on wide screens, where its toggle is hidden.
  const adminMenu = document.querySelector("[data-admin-menu]");
  if (adminMenu && typeof window.matchMedia === "function") {
    const wide = window.matchMedia("(min-width: 60rem)");
    const syncMenu = () => { adminMenu.open = wide.matches; };
    syncMenu();
    // Older Safari only offers the deprecated addListener on media queries.
    if (typeof wide.addEventListener === "function") wide.addEventListener("change", syncMenu);
    else if (typeof wide.addListener === "function") wide.addListener(syncMenu);
  }

  // "About this page" panels ({% aboutpage %} in templatetags/stewardship.py)
  // start closed so the page's data comes first (#227). Each browser
  // remembers, per page type, whether an Admin opened one. Only an "open"
  // marker is stored, and only after an Admin opens a panel: closed is the
  // default, so closing removes the marker. Earlier versions stored a
  // "closed" marker instead (panels then started open); that value now means
  // the default and is cleared. Storage can be unavailable (private windows,
  // blocked site data); the panel then just starts closed.
  document.querySelectorAll("details[data-about-page]").forEach((panel) => {
    const key = `pk-about-page:${panel.dataset.aboutPage}`;
    try {
      const stored = window.localStorage.getItem(key);
      if (stored === "open") panel.open = true;
      else if (stored !== null) window.localStorage.removeItem(key);
    } catch (error) { /* Keep the panel closed. */ }
    panel.addEventListener("toggle", () => {
      try {
        if (panel.open) window.localStorage.setItem(key, "open");
        else window.localStorage.removeItem(key);
      } catch (error) { /* Nothing to remember without storage. */ }
    });
  });

  // Hosted files (#346). A Copy button copies its read-only field; without
  // script (or the clipboard API) the button stays hidden and the field can
  // be selected by hand. Choosing a file fills an empty placeholder-name
  // field from the file's base name, as the server would derive it (the
  // server still derives and validates it when the field is left blank).
  // The buttons sit in table rows, so a re-sorted table wires them again
  // (enhanceTable below).
  const wireCopyButtons = (root) => {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") return;
    root.querySelectorAll("button[data-copy]").forEach((button) => {
      const field = document.getElementById(button.dataset.copy);
      if (!field) return;
      button.hidden = false;
      const label = button.textContent;
      button.addEventListener("click", () => {
        navigator.clipboard.writeText(field.value).then(
          () => { button.textContent = "Copied"; setTimeout(() => { button.textContent = label; }, 2000); },
          () => { field.select(); },
        );
      });
    });
  };
  const slugSource = document.querySelector("input[data-slug-from]");
  const slugTarget = document.querySelector("input[data-slug-to]");
  if (slugSource && slugTarget) {
    let derived = "";
    slugSource.addEventListener("change", () => {
      const file = slugSource.files && slugSource.files[0];
      if (!file || (slugTarget.value && slugTarget.value !== derived)) return;
      const stem = file.name.replace(/\.[^.]*$/, "");
      let slug = stem.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase()
        .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
      if (slug.length > 64) slug = slug.slice(0, 64).replace(/-[^-]*$/, "") || slug.slice(0, 64);
      derived = slug.replace(/^-+|-+$/g, "") || "file";
      slugTarget.value = derived;
    });
  }

  // The parish's chosen date format (date-format-v1.js, loaded first).
  if (window.ParishDates) window.ParishDates.localize(document);

  // A download's timezone choice offers the browser's own zone, chosen by
  // default; without script the choice stays UTC. Also run for markup a table
  // swap brings in (enhanceTable below).
  //
  // A form that takes a date and time typed in local time (#558) sends the
  // browser's zone in a hidden input[data-browser-zone] for the server to
  // convert to UTC, and reveals its [data-browser-zone-note] naming the zone.
  // When the browser reports no zone (or ICU's "Etc/Unknown") the field stays
  // empty and the note hidden, and the [data-browser-zone-field] controls are
  // marked invalid with the input's data-zone-missing-hint while they hold a
  // value, so the data-require-complete gate below keeps Save (or Apply)
  // unavailable and says why; they are then described by that hint instead
  // of the hidden note. An empty field is left alone: optional dates (the
  // System logs filters) need no zone, and a required one (a contact
  // attempt's date) is invalid while empty anyway, with the same hint. The
  // server's own catalog check refuses any other zone it does not know.
  // A [data-zone-dependent] field that only narrows what is shown (the
  // critical-events banner's From day for System logs) is instead disabled,
  // so it is not sent: the form still works, only less narrowly.
  const zoneValidity = (field) => {
    field.setCustomValidity(field.value ? field.dataset.zoneHint || "" : "");
  };
  const wireBrowserTimezone = (root) => {
    let zone = "";
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { /* no Intl */ }
    root.querySelectorAll("input[data-browser-zone]").forEach((input) => {
      const scope = input.form || root;
      const known = Boolean(zone) && zone !== "Etc/Unknown";
      const hint = known ? "" : input.dataset.zoneMissingHint || "";
      const note = scope.querySelector("[data-browser-zone-note]");
      const gateHint = scope.querySelector("[data-complete-hint]");
      input.value = known ? zone : "";
      scope.querySelectorAll("[data-browser-zone-name]").forEach((node) => {
        node.textContent = input.value;
      });
      if (note) note.hidden = !known;
      scope.querySelectorAll("[data-zone-dependent]").forEach((field) => {
        field.disabled = !known;
      });
      scope.querySelectorAll("[data-browser-zone-field]").forEach((field) => {
        if (hint) {
          field.dataset.missingHint = hint;
          field.dataset.zoneHint = hint;
        } else {
          delete field.dataset.missingHint;
          delete field.dataset.zoneHint;
        }
        zoneValidity(field);
        const described = known ? note : gateHint;
        if (described && described.id) field.setAttribute("aria-describedby", described.id);
      });
    });
    if (!zone || zone === "UTC") return;
    root.querySelectorAll("select[data-browser-timezone]").forEach((select) => {
      select.append(new Option(`${zone} (this browser)`, zone, true, true));
    });
  };
  wireBrowserTimezone(document);
  // Re-check a zone field as its value changes. Capturing on the document
  // runs before a form's own input and change listeners (the complete gate),
  // and covers fields a table swap brings in. A value the browser restores
  // without an event (the back/forward cache) is re-checked on pageshow, and
  // this listener is added before the gate's own.
  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    if (event.target instanceof Element && event.target.matches("[data-browser-zone-field]")) {
      zoneValidity(event.target);
    }
  }, true));
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("[data-browser-zone-field]").forEach(zoneValidity);
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
  const wireValidity = (root) => {
    root.querySelectorAll("input, select, textarea").forEach((field) => {
      field.addEventListener("blur", () => {
        if (field.willValidate) {
          field.setAttribute("aria-invalid", String(!field.validity.valid));
        }
      });
    });
  };

  // Shared Admin tables (web/tables.py, table-navigator.html and
  // table-selection.html). Row selection is per page: Select all and the
  // header checkbox choose every enabled row checkbox shown, and bulk action
  // buttons stay disabled until something is chosen. The server still
  // validates every submitted selection.
  const wireSelection = (scope) => {
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
  };
  // A new rows-per-page choice applies at once and starts again at page 1.
  // The submission has no submitter, so the select is noted for the in-place
  // handler below, which puts focus back on its replacement.
  const pendingControls = new WeakMap(); // form → control that submitted it
  const wirePageSize = (root) => {
    root.querySelectorAll("select[data-page-size]").forEach((select) => {
      select.addEventListener("change", () => {
        const page = select.form?.querySelector("[data-page-number]");
        if (page) page.value = "1";
        if (select.form) pendingControls.set(select.form, select);
        select.form?.requestSubmit();
      });
    });
  };
  // Everything above that binds to elements inside a table, so a table
  // swapped in by the in-place re-sort below behaves like one the page
  // loaded with.
  const enhanceTable = (root) => {
    wireCopyButtons(root);
    wireValidity(root);
    root.querySelectorAll("[data-select-table]").forEach(wireSelection);
    wirePageSize(root);
    if (root === document) return;
    wireBrowserTimezone(root);
    if (window.ParishDates) window.ParishDates.localize(root);
    // A swapped-in region can carry charts (the response dashboard); the
    // chart engine draws them as it drew the page's own (chart-v1.js).
    if (window.ParishCharts) window.ParishCharts.render(root);
  };
  enhanceTable(document);

  // In-place controls (#478, #484, #519): an Admin control changes the page
  // where the reader is, without a reload or a jump to the top.
  //
  // A page marks each part a control can change as a region: an element
  // with a stable id and data-in-place-region, or data-table-region for a
  // shared Admin table (its navigators and rows; the id is the TablePage's
  // anchor). The id is also the control's URL fragment, so the ordinary
  // load (without script, or when anything below goes wrong) lands on the
  // region rather than at the top of the page.
  //
  // Activating a control fetches exactly the request the control would have
  // made (a link's GET, a form's query or CSRF POST body), finds the regions
  // in the fetched page and swaps them in, so the reader keeps their scroll
  // position and focus. The server sees an ordinary page request and no
  // special response path exists. The controls are:
  // - a shared table's sort headings, navigator links and forms, and the
  //   page's filter form (form#table-filters, #484), unless that form is
  //   marked data-filter-reload because its options reshape far more of the
  //   page than the table (the participation chart);
  // - a[data-in-place]: a link that shows another view of this page (the
  //   response dashboard's mode and grain, a list's Refresh). Its value, if
  //   any, is a stable key that finds the link again in the fresh page, for
  //   focus and to announce the view now shown ("By day");
  // - form[data-in-place]: a form whose answer is this page again, such as a
  //   POST whose server redirects back here (Post/Redirect/Get).
  // A data-in-place link or form names the region it changes by its URL's
  // fragment, or else by the region it sits in. Its data-in-place-message
  // ("List refreshed.") is announced before the region's row count.
  const REGIONS = "[data-in-place-region][id], [data-table-region][id]";
  const isRegion = (node) => Boolean(node && node.matches(REGIONS));
  // A form's submit buttons, including a <button> with no type (a submit
  // button by default).
  const SUBMIT = "button:not([type]), button[type=submit], input[type=submit]";
  // A form's first submit button, including one outside it that names it
  // with form="…" (form.elements lists those; querySelector would not).
  const firstSubmit = (form) => [...form.elements].find((node) => node.matches(SUBMIT));
  // Only this site's own pages are fetched and swapped in; any other address
  // keeps its ordinary meaning.
  const sameOrigin = (url) => new URL(url, document.baseURI).origin === window.location.origin;
  const tableStatus = document.createElement("div");
  tableStatus.className = "visually-hidden";
  tableStatus.setAttribute("role", "status");
  (document.querySelector("main") || document.body).append(tableStatus);
  const announce = (text) => {
    // Clearing first makes a repeated message (the same count after a
    // re-sort) read again.
    tableStatus.textContent = "";
    window.setTimeout(() => { tableStatus.textContent = text; }, 50);
  };
  // One request in flight for the whole page: every response rewrites every
  // region, so a newer choice anywhere supersedes an older one, and an older
  // response can never overwrite a newer swap.
  let tableRequest = null; // the AbortController of the request in flight
  // The region the latest request marked aria-busy. An aborted request
  // leaves the mark only when that newer request owns the same region.
  let busyRegion = null;
  // A form[data-in-place] POST saves a change, so it is never aborted: while
  // it is in flight every other in-place control (a heading, Next, a view
  // link, the filters) is ignored, as a repeated submission is. Aborting it
  // would leave the change saved but unreported and the page out of date.
  let saving = null; // the AbortController of a saving POST in flight
  // The table region a table control belongs to, or null when the control
  // is not inside one (a page that keeps full loads) or the region has no
  // id to match by; either way the control keeps its ordinary meaning.
  const tableRegion = (node) => {
    const region = node.closest("[data-table-region]");
    return region && region.id ? region : null;
  };
  // The region a data-in-place link or form changes: the one the fragment
  // of its own href or action attribute names, else the one it sits in. The
  // attribute is read as written, so an empty action (this page) never
  // borrows the fragment of the address the page happens to show.
  const targetRegion = (control, written) => {
    const hash = (written || "").split("#")[1];
    const named = hash ? document.getElementById(hash) : null;
    if (isRegion(named)) return named;
    const around = control.closest("[data-in-place-region], [data-table-region]");
    return isRegion(around) ? around : null;
  };
  const withFragment = (url, id) => {
    const target = new URL(url, document.baseURI);
    target.hash = id;
    return target.href;
  };
  // Where focus goes after the swap. owner is the a[data-in-place] link or
  // form[data-in-place] that opted the control into the generic mechanism,
  // or null for a table control. The click and submit handlers decide it,
  // never the control's ancestors: a form="…" button can sit outside its
  // form, and a table's sort heading or navigator can sit inside a
  // form[data-in-place] (a selection form around its table) and still be a
  // table control. A data-in-place control, or any control outside the
  // region it changed, keeps focus while it is still in the document (a
  // filter button); one that was replaced (it sat in a region, or in a
  // data-table-sync node) is found again by its id, then by its owner's
  // data-in-place key, a form's key standing for its first button. A table
  // control returns to the same heading (by column) or the matching control
  // of the same navigator. Previous and Next become plain text on the first
  // or last page, so each falls back to the other. Anything still missing
  // falls back to the region itself.
  const focusAfter = (region, control, owner) => {
    if (!region.contains(control) || owner) {
      const key = owner?.dataset.inPlace;
      return () => {
        if (control.isConnected) return control;
        const again = control.id && document.getElementById(control.id);
        if (again) return again;
        const owner = key && document.querySelector(`[data-in-place="${CSS.escape(key)}"]`);
        return owner instanceof HTMLFormElement ? firstSubmit(owner) : owner;
      };
    }
    const heading = control.closest("th[data-sort-column]");
    if (heading) {
      const column = CSS.escape(heading.dataset.sortColumn);
      return (fresh) => fresh.querySelector(`th[data-sort-column="${column}"] .sort-link`);
    }
    const navs = [...region.querySelectorAll(".table-nav")];
    const index = navs.indexOf(control.closest(".table-nav"));
    const choices = control.matches("[data-table-previous]") ? ["[data-table-previous]", "[data-table-next]"]
      : control.matches("[data-table-next]") ? ["[data-table-next]", "[data-table-previous]"]
      : control.matches("[data-page-size]") ? ["[data-page-size]"]
      : control.matches("[data-page-number]") ? ["[data-page-number]"]
      : [".table-nav-form [type=submit]"];
    return (fresh) => {
      const nav = fresh.querySelectorAll(".table-nav")[index];
      return choices.map((selector) => nav?.querySelector(selector)).find(Boolean);
    };
  };
  // What the live region says (owner as for focusAfter). A data-in-place
  // control says its message
  // ("List refreshed.") and the rows now shown, or names the view now shown
  // (its fresh link's text, "By day"). A table control says the sorted
  // column and its direction after a heading, otherwise the navigator's own
  // "Showing 26–50 of 120 · Page 2 of 5". A filter changes every table on
  // the page, so each table's count is read, named by its navigator when
  // there is more than one ("Member pages: …").
  const squeeze = (text) => (text || "").replace(/\s+/g, " ").trim();
  const tableCount = (region) => squeeze(region.querySelector(".table-count")?.textContent);
  const describeTable = (fresh, control, owner) => {
    if (owner) {
      const message = owner.getAttribute("data-in-place-message");
      if (message) return [message, tableCount(fresh)].filter(Boolean).join(" ");
      // A form without a message names what was done by its button's text
      // ("Save"), never by the whole form's text.
      if (owner instanceof HTMLFormElement) {
        return squeeze(control instanceof HTMLInputElement ? control.value : control.textContent)
          || tableCount(fresh);
      }
      const key = owner.dataset.inPlace;
      const view = key && document.querySelector(`[data-in-place="${CSS.escape(key)}"]`);
      return view ? squeeze(view.textContent) : tableCount(fresh);
    }
    if (!control.closest("[data-table-region]")) {
      const regions = [...document.querySelectorAll("[data-table-region][id]")];
      if (regions.length < 2) return tableCount(fresh);
      return regions.map((region) => {
        const label = region.querySelector(".table-nav")?.getAttribute("aria-label");
        return label ? `${label}: ${tableCount(region)}` : tableCount(region);
      }).join(". ");
    }
    const column = control.closest("th[data-sort-column]")?.dataset.sortColumn;
    if (column === undefined) return tableCount(fresh);
    const heading = fresh.querySelector(`th[data-sort-column="${CSS.escape(column)}"]`);
    const label = [...(heading?.querySelector(".sort-link")?.childNodes || [])]
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent).join("").trim();
    const state = heading?.getAttribute("aria-sort");
    return state ? `Sorted by ${label}, ${state}` : `Sorted by ${label}`;
  };
  // Enhance freshly swapped-in content as the page's own was enhanced, then
  // tell any other script (a bubbling parishkit:swap event on the fresh
  // root), so it can bind to the new elements without a load-order tie to
  // this file.
  const enhanceSwapped = (fresh) => {
    enhanceTable(fresh);
    fresh.dispatchEvent(new CustomEvent("parishkit:swap", {bubbles: true}));
  };
  // Swap one region for its fresh copy. Selections are restored on the copy
  // before it enters the document, so wireSelection's first update already
  // counts them and no change event fires for a tick the reader did not make.
  const swapRegion = (region, fresh) => {
    const chosen = new Set([...region.querySelectorAll("input[data-select-row]:checked")]
      .map((box) => box.value));
    fresh.querySelectorAll("input[data-select-row]").forEach((box) => {
      if (chosen.has(box.value)) box.checked = true;
    });
    // Charts keep their space until the fresh ones are drawn (chart-v1.js).
    if (window.ParishCharts) window.ParishCharts.hold(region, fresh);
    region.replaceWith(fresh);
    enhanceSwapped(fresh);
  };
  // Controls and text outside the regions that follow a table's page, size,
  // sort or filters (or any in-place refresh) are marked data-table-sync
  // with a stable id. A form keeps its element, and so every script bound to
  // it and every visible choice (an export's format or timezone, a packet's
  // history tick, the filters just typed), and takes from the fetched page
  // only its hidden fields, which carry the table state: a filter form's
  // size and sort, an export's query and one-time request key. A filter can
  // add or drop a hidden field (an export carries only the filters in use),
  // so the hidden fields are matched by name and position, updated, removed
  // or added. A non-form node (a refresh link, a count or summary, a list of
  // choices drawn from the rows) is replaced whole and enhanced again; its
  // ticks are lost, as a full load would lose them. A browser-zone field is
  // left alone: it holds this browser's zone, not table state, and a fetched
  // page renders only the zone its request carried (none after a re-sort of
  // a page no filter was applied to), which must not blank it (#558).
  const syncHidden = (form, fresh) => {
    const hidden = (root) => [
      ...root.querySelectorAll('input[type="hidden"][name]:not([data-browser-zone])'),
    ];
    const wanted = new Map(); // name → the fresh fields of that name, in order
    hidden(fresh).forEach((field) => {
      const name = field.getAttribute("name");
      wanted.set(name, [...(wanted.get(name) || []), field]);
    });
    hidden(form).forEach((field) => {
      const source = wanted.get(field.getAttribute("name"))?.shift();
      if (source) field.value = source.value;
      else field.remove();
    });
    form.prepend(...[...wanted.values()].flat().map((field) => document.importNode(field)));
  };
  const syncControls = (parsed) => {
    document.querySelectorAll("[data-table-sync][id]").forEach((node) => {
      const fresh = parsed.getElementById(node.id);
      if (!fresh) return;
      if (node instanceof HTMLFormElement) {
        syncHidden(node, fresh);
        return;
      }
      node.replaceWith(fresh);
      enhanceSwapped(fresh);
    });
  };
  // A saving POST that got no answer at all may or may not have reached the
  // server, so it is not sent again; an alert at the top of the form says so
  // instead, until the next attempt.
  const UNREACHABLE = "The server could not be reached, so this page was not updated. "
    + "Your change may or may not have been saved: reload the page to check before trying again.";
  const clearUnreachable = (form) => form?.querySelector("[data-in-place-error]")?.remove();
  const showUnreachable = (form) => {
    clearUnreachable(form);
    const note = document.createElement("p");
    note.className = "notice notice-error";
    note.setAttribute("role", "alert");
    note.setAttribute("data-in-place-error", "");
    note.textContent = UNREACHABLE;
    form.prepend(note);
  };
  // Load the page a control leads to and bring this page up to date from it.
  // Success: every region present in both pages is swapped (the one the
  // control changes and any other on the page, so they never disagree), the
  // sync controls take their values, the address bar follows a GET choice
  // or a followed redirect, and focus and the live region report the result.
  //
  // A POST that got an answer is never sent again: that would repeat it (a
  // second access-audit row for a report read, a second change for a save).
  // So a successful answer that is not a page with this region (a sign-in
  // page after the session ended), or for a data-in-place control a page at
  // another path or origin, is shown by loading its address only when a
  // redirect led there (that address is a GET, safe to repeat); a POST's own
  // answer, like an error answer to a POST, is shown as returned. A GET is
  // safe to repeat, so a GET's error answer, or a fetch with no answer at
  // all, falls back to the control's ordinary navigation. A read-only table
  // POST with no answer falls back to the ordinary submission too; a saving
  // POST is never re-sent at all, and says the server could not be reached
  // instead (see above). A newer choice aborts an older request, unless that
  // one is a saving POST.
  //
  // options.owner is the data-in-place link or form behind the control (null
  // for a table control; see focusAfter); options.form is the
  // form a saving data-in-place POST came from (a form="…" submitter can sit
  // outside it).
  const refreshTable = async (region, control, url, init, fallback, options = {}) => {
    if (saving) {
      // The control was ignored; say why rather than doing nothing visibly.
      announce("Still saving…");
      return;
    }
    tableRequest?.abort();
    const controller = new AbortController();
    tableRequest = controller;
    const form = options.form || null;
    if (form) {
      saving = controller;
      clearUnreachable(form);
    }
    try {
      await refreshRegions(region, control, url, init, fallback, options, controller, form);
    } finally {
      if (saving === controller) saving = null;
    }
  };
  const refreshRegions = async (region, control, url, init, fallback, options, controller, form) => {
    const owner = options.owner || null;
    const focus = focusAfter(region, control, owner);
    const id = region.id;
    region.setAttribute("aria-busy", "true");
    busyRegion = region;
    let response, text;
    try {
      response = await fetch(url, {
        ...init,
        signal: controller.signal,
        credentials: "same-origin",
        headers: {"X-Requested-With": "fetch"},
      });
      text = await response.text();
    } catch (error) {
      // No answer at all (a network error): the ordinary navigation, or for
      // a saving POST the note that says so. Only a newer request aborts
      // one, and that request has already marked its own region busy
      // (busyRegion): when it is this same region the mark is left to it,
      // otherwise this region is cleared, since nothing else will.
      const aborted = error.name === "AbortError";
      if (!aborted || busyRegion !== region) region.removeAttribute("aria-busy");
      if (aborted) return;
      if (form) showUnreachable(form);
      else fallback();
      return;
    }
    const parsed = new DOMParser().parseFromString(text, "text/html");
    // An error answer (a refused filter's 400, a denial, an unavailable
    // report) or a page that renders its own error summary is not swapped in.
    // A POST is never sent again: the report views audit every POST, and a
    // saving POST would repeat its change. Its answer is shown as the page
    // instead, as a native submission would have shown it. A GET is safe to
    // repeat, so it takes the ordinary navigation.
    const showAsReturned = () => {
      document.open();
      document.write(text);
      document.close();
    };
    if (!response.ok || parsed.querySelector("[data-error-summary]")) {
      if (init.method !== "POST") {
        region.removeAttribute("aria-busy");
        fallback();
        return;
      }
      showAsReturned();
      return;
    }
    const fresh = parsed.getElementById(id);
    const answered = new URL(response.url);
    const elsewhere = answered.origin !== window.location.origin
      || (owner && answered.pathname !== window.location.pathname);
    if (elsewhere || !isRegion(fresh)) {
      if (response.redirected || init.method !== "POST") {
        window.location.assign(withFragment(response.url, id));
      } else {
        showAsReturned();
      }
      return;
    }
    document.querySelectorAll(REGIONS).forEach((other) => {
      const copy = parsed.getElementById(other.id);
      if (isRegion(copy)) swapRegion(other, copy);
    });
    syncControls(parsed);
    // A GET choice belongs in the address bar, so reload, bookmarks and
    // returning to the page keep it; a POST table's private filters never
    // reach a URL. A server redirect chose the address itself (a saving
    // POST's Post/Redirect/Get answer, so a reload repeats only the GET).
    if (response.redirected) {
      window.history.replaceState(window.history.state, "", withFragment(response.url, id));
    } else if (init.method !== "POST") {
      window.history.replaceState(window.history.state, "", url);
    }
    let target = focus(document.getElementById(id));
    if (!target) {
      target = document.getElementById(id);
      target.setAttribute("tabindex", "-1");
    }
    target.focus({preventScroll: true});
    // Focus without scrolling kept the reader's place; if the control itself
    // is just outside the viewport, bring it (and nothing more) into view.
    target.scrollIntoView({block: "nearest"});
    announce(describeTable(document.getElementById(id), control, owner));
  };
  // Links: a GET table's sort headings and navigator Previous and Next, and
  // every a[data-in-place]. A modified click (new tab, new window) keeps its
  // ordinary meaning.
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
        || event.shiftKey || event.altKey || !(event.target instanceof Element)) return;
    const link = event.target.closest("a.sort-link, .table-nav a[href], a[data-in-place][href]");
    // A link that opens elsewhere (another window, a download, another
    // site) keeps its ordinary meaning.
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")
        || !sameOrigin(link.href)) return;
    // Only a link marked data-in-place itself is generic; a sort heading or
    // navigator link stays a table control even inside a form[data-in-place].
    const owner = link.matches("[data-in-place]") ? link : null;
    const region = owner ? targetRegion(link, link.getAttribute("href")) : tableRegion(link);
    if (!region) return;
    event.preventDefault();
    refreshTable(region, link, link.href, {method: "GET"}, () => window.location.assign(link.href),
      {owner});
  });
  // Forms: a POST table's headings, Previous and Next, every table's
  // page-number form, the page's filter form (#484), whose new filters
  // refresh every table region, the summaries and counts marked
  // data-table-sync, and the export form's hidden query, and every
  // form[data-in-place]. Capture phase, so this runs before the busy-state
  // handler below, which leaves a submission script already took over
  // alone. The browser has already checked the form's own constraints (a
  // pattern, a date) before this event fires.
  // Forms whose in-place request is still in flight. The busy-state handler
  // below never sees an in-place submission (it is defaultPrevented), so
  // repeats (a double click, Enter pressed twice) are ignored here instead,
  // and the submitter is marked aria-disabled until the request settles.
  const inFlight = new WeakSet();
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
    let region, control;
    let owner = null; // the form[data-in-place] behind the submission, if any
    // Attributes, not properties: a hidden field named "action" or "method"
    // (a report's own filter) would shadow the form's property of that name.
    // A submitter's formaction or formmethod overrides the form's, as in a
    // native submission.
    const written = event.submitter?.getAttribute("formaction") ?? form.getAttribute("action");
    const method = (event.submitter?.getAttribute("formmethod")
      ?? form.getAttribute("method") ?? "get").toLowerCase();
    if (form.matches(".sort-form, .table-nav form")) {
      region = tableRegion(form);
      // A rows-per-page change submits its form from script, with no
      // submitter; wirePageSize noted the select. Failing both, the Go button.
      control = event.submitter || pendingControls.get(form) || form;
      pendingControls.delete(form);
    } else if (form.matches("form#table-filters[data-table-sync]:not([data-filter-reload])")) {
      // The first table on the page stands for them all: every region in
      // the response is swapped, and focus stays on the filter button.
      region = document.querySelector("[data-table-region][id]");
      control = event.submitter || form.querySelector("[type=submit]") || form;
    } else if (form.matches("form[data-in-place]")) {
      region = targetRegion(form, written);
      control = event.submitter || firstSubmit(form) || form;
      // The submitter's own form owns it, even when the button sits outside
      // that form (form="…"); the event's target is that same form.
      owner = event.submitter?.form || form;
    }
    if (!region || !sameOrigin(written || "")) return;
    event.preventDefault();
    if (inFlight.has(form)) return;
    // The request is built before the form is marked in flight, so nothing
    // can fail between the two and leave the form locked.
    const action = new URL(written || "", document.baseURI);
    const fields = new FormData(form, event.submitter || undefined);
    const save = Boolean(owner) && method === "post";
    let init, load;
    if (method === "post") {
      // The same body the browser would send, submitter included: multipart
      // for a file upload, urlencoded otherwise.
      const enctype = (event.submitter?.getAttribute("formenctype")
        ?? form.getAttribute("enctype") ?? "").toLowerCase();
      const body = enctype === "multipart/form-data" ? fields : new URLSearchParams(fields);
      init = {method: "POST", body};
      // A read-only table POST that got no answer is submitted natively; a
      // saving POST never is (refreshTable shows a note instead).
      load = () => HTMLFormElement.prototype.submit.call(form);
    } else {
      action.search = new URLSearchParams(fields).toString();
      init = {method: "GET"};
      // A data-in-place GET falls back to the very URL it fetched, which
      // keeps a submitter's formaction (a native re-submission would not).
      load = owner ? () => window.location.assign(action.href)
        : () => HTMLFormElement.prototype.submit.call(form);
    }
    inFlight.add(form);
    const submitter = event.submitter;
    submitter?.setAttribute("aria-disabled", "true");
    // A save shows its button busy, as an ordinary submission does.
    if (save && !saving) submitter?.classList.add("is-busy");
    const settle = () => {
      inFlight.delete(form);
      submitter?.removeAttribute("aria-disabled");
      submitter?.classList.remove("is-busy");
    };
    refreshTable(region, control, action.href, init, load, {owner, form: save ? form : null})
      .finally(settle);
  }, true);

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
  // Forms marked data-submit-waits (export queueing) can legitimately wait
  // on the server for a minute or two, e.g. behind a ParishSoft refresh's
  // final step. They stay busy much longer and say so visibly, instead of
  // silently re-enabling after ten seconds, which looked like nothing happened.
  const waitNotes = new Map();
  const slowTimers = new Map();
  const release = (form) => {
    window.clearTimeout(submitting.get(form));
    submitting.delete(form);
    // A form released early (page restored from the back/forward cache) must
    // not later show a stale "Still working" note.
    window.clearTimeout(slowTimers.get(form));
    slowTimers.delete(form);
    form.removeAttribute("aria-busy");
    submitControls(form).forEach((node) => {
      node.classList.remove("is-busy");
      node.removeAttribute("aria-disabled");
    });
    if (!submitting.size) busyStatus.textContent = "";
  };
  const waitNote = (form, submitter, text) => {
    let note = waitNotes.get(form);
    if (!note) {
      note = document.createElement("p");
      note.className = "help submit-wait";
      note.setAttribute("role", "status");
      (submitter || form).insertAdjacentElement("afterend", note);
      waitNotes.set(form, note);
    }
    note.textContent = text;
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
    if (!form.hasAttribute("data-submit-waits")) {
      submitting.set(form, window.setTimeout(() => release(form), 10000));
      return;
    }
    const submitter = event.submitter;
    slowTimers.set(form, window.setTimeout(() => waitNote(form, submitter,
      "Still working… If a ParishSoft refresh is finishing, this can take up to two minutes. Keep this page open."), 5000));
    submitting.set(form, window.setTimeout(() => {
      release(form);
      waitNote(form, submitter,
        "No response from the server yet. Your export may still have been queued; check Background work, or try again.");
    }, 180000));
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    [...submitting.keys()].forEach(release);
    waitNotes.forEach((note) => note.remove());
    waitNotes.clear();
    // A page restored from the back/forward cache still carries the one-time
    // request_key it was rendered with. Reusing it for a different export
    // (another format) would be refused as an already-bound request, so give
    // each restored form a fresh key; a genuine retry of the same submission
    // still reuses the key it was sent with.
    document.querySelectorAll('input[type="hidden"][name="request_key"]').forEach((input) => {
      if (window.crypto?.randomUUID) input.value = window.crypto.randomUUID();
    });
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
    const localTime = {format: (date) => window.ParishDates ?
      window.ParishDates.instant(date) : date.toISOString()};
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

  // Complete-before-submit (#553): a form marked data-require-complete keeps
  // its submit buttons disabled until every control that is currently shown
  // and required is valid, and says why in its [data-complete-hint] element
  // (which the buttons name with aria-describedby). Conditional requirements
  // are applied only here, never in the markup, so without JavaScript the
  // buttons stay enabled and the server's own validation answers as before:
  //   - data-required-when-shown="name …" on a data-show-when group (or an
  //     empty value on a field) makes those controls required while shown;
  //   - data-required-when="name=value" makes a field required while the
  //     form's control "name" has that value (notes for the outcome Other).
  // The hint is the first missing control's data-missing-hint, or that of
  // the nearest element around it. As with acknowledgments below, real
  // disabled is used and only buttons this gate disabled are re-enabled.
  const requiredWhen = (form) => {
    form.querySelectorAll("[data-required-when]").forEach((node) => {
      const [name, value] = node.dataset.requiredWhen.split("=");
      const control = form.elements.namedItem(name);
      // A hidden (disabled) control's leftover value does not count.
      node.required = Boolean(control) && !control.disabled && control.value === value;
    });
  };
  const gateComplete = (form) => {
    if (!form || !form.hasAttribute("data-require-complete")) return;
    requiredWhen(form);
    // A required text field holding only spaces is still empty: the server
    // trims it (notes for the outcome Other).
    const blank = (node) => node.required && node.matches("textarea, input[type=text]")
      && !node.value.trim();
    const missing = [...form.elements].find((node) => node.willValidate
      && !node.closest("[hidden]") && (!node.validity.valid || blank(node)));
    const hint = form.querySelector("[data-complete-hint]");
    if (hint) {
      hint.hidden = !missing;
      hint.textContent = missing
        ? (missing.closest("[data-missing-hint]")?.dataset.missingHint
          || "Fill in the required fields to save.")
        : "";
    }
    submitControls(form).forEach((node) => {
      if (node.formNoValidate) return;
      if (missing && !node.disabled) {
        node.disabled = true;
        node.setAttribute("data-complete-gated", "");
      } else if (!missing && node.hasAttribute("data-complete-gated")) {
        node.disabled = false;
        node.removeAttribute("data-complete-gated");
      }
    });
  };

  // A field marked data-show-when="name=value" is shown only while the form's
  // control called "name" has that value, e.g. the daily refresh time only
  // for the once-a-day frequency; "name!=value" shows it for every other
  // value, e.g. a contact attempt's date only once a channel is chosen. On a
  // field the mark hides its enclosing div; on a div (a group of fields, as
  // in Ministry follow-up) it hides that div and every control inside it.
  // Hidden fields are disabled so they are not sent; without JavaScript
  // every field simply stays visible, and the server ignores what does not
  // apply. A browser can restore form values without a change event (the
  // back/forward cache, or autofill after load), so every rule is applied
  // again on pageshow, not only at load.
  const showWhenUpdates = [];
  document.querySelectorAll("[data-show-when]").forEach((node) => {
    const rule = node.dataset.showWhen;
    const negated = rule.includes("!=");
    const [name, value] = rule.split(negated ? "!=" : "=");
    const field = node.matches("input, select, textarea");
    const form = field ? node.form : node.closest("form");
    const control = form && form.elements.namedItem(name);
    const wrapper = field ? node.closest("div") : node;
    if (!control || !wrapper) return;
    const controls = field ? [node] : [...node.querySelectorAll("input, select, textarea")];
    const listed = node.dataset.requiredWhenShown;
    const required = listed === undefined ? []
      : listed === "" ? [node]
        : listed.split(/\s+/).map((item) => form.elements.namedItem(item)).filter(Boolean);
    const update = () => {
      const shown = (control.value === value) !== negated;
      wrapper.hidden = !shown;
      controls.forEach((item) => { item.disabled = !shown; });
      required.forEach((item) => { item.required = shown; });
      gateComplete(form);
    };
    control.addEventListener("change", update);
    showWhenUpdates.push(update);
    update();
  });
  const completeForms = [...document.querySelectorAll("form[data-require-complete]")];
  completeForms.forEach((form) => {
    gateComplete(form);
    form.addEventListener("input", () => gateComplete(form));
    form.addEventListener("change", () => gateComplete(form));
    // A partly typed date ("09/__/____") is invalid (badInput) but fires no
    // input or change event, so the gate also checks as focus leaves a field.
    form.addEventListener("focusout", () => gateComplete(form));
  });
  window.addEventListener("pageshow", () => {
    showWhenUpdates.forEach((update) => update());
    completeForms.forEach(gateComplete);
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
      // aria-controls: field toggletips point at their bubble's id.
      row.querySelectorAll("[name], [id], [for], [aria-describedby], [aria-controls]").forEach((node) => {
        ["name", "id", "for", "aria-describedby", "aria-controls"].forEach((attribute) => {
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

  // Redraw the visual pane from the server's sanitizer while the HTML source
  // is edited. The pane stays visible but dims and stops accepting edits until
  // the newest request answers (a stale pane edited now would overwrite the
  // source). The one response also carries the generated plain text, relayed
  // to the plain-text preview as "stewardship:source-preview", and a
  // plain-language list of removed markup, shown as text (never as markup).
  function liveSource(form, visual, editor, source) {
    const url = visual.dataset.previewUrl;
    const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');
    const updating = visual.querySelector("[data-visual-updating]");
    const unavailable = visual.querySelector("[data-visual-unavailable]");
    const removedNotice = visual.querySelector("[data-visual-removed]");
    const removedList = visual.querySelector("[data-visual-removed-list]");
    if (!url || !csrf || !updating || !unavailable || !removedNotice || !removedList) {
      // Without the live preview, fall back to the safe old behavior.
      source.addEventListener("input", () => { visual.hidden = true; });
      return;
    }
    let timer = null;
    let controller = null;
    const tools = [...form.querySelectorAll("[data-content-tag]")];
    // A stale pane accepts neither typing nor formatting: either would call
    // sync() and overwrite newer source with the stale pane's content.
    const busy = (value) => {
      visual.setAttribute("aria-busy", String(value));
      editor.contentEditable = value ? "false" : "true";
      tools.forEach((button) => { button.disabled = value; });
      updating.hidden = !value;
    };
    const refresh = async () => {
      controller?.abort();
      controller = new AbortController();
      const request = controller;
      const posted = source.value;
      // A response applies only while it still describes the current source:
      // newer typing aborts this request and schedules another.
      const current = () => controller === request && source.value === posted;
      try {
        const response = await fetch(url, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"},
          body: new URLSearchParams({html: posted}), signal: request.signal
        });
        if (!response.ok) throw new Error("preview unavailable");
        const data = await response.json();
        if (typeof data.html !== "string" || typeof data.text !== "string"
            || !Array.isArray(data.removed)) throw new Error("preview unavailable");
        if (!current()) return;
        // Server-sanitized markup only: the same allowlist the stored content
        // passed, so this is as safe as the page's initial render.
        editor.innerHTML = data.html;
        removedList.textContent = data.removed.filter((item) => typeof item === "string").join("; ");
        removedNotice.hidden = !data.removed.length;
        unavailable.hidden = true;
        busy(false);
        form.dispatchEvent(new CustomEvent("stewardship:source-preview", {detail: {text: data.text}}));
      } catch (error) {
        if (error.name === "AbortError" || !current()) return;
        // Keep the pane read-only: its content no longer matches the source.
        updating.hidden = true;
        unavailable.hidden = false;
        form.dispatchEvent(new CustomEvent("stewardship:source-preview", {detail: {text: null}}));
      }
    };
    source.addEventListener("input", () => {
      busy(true);
      unavailable.hidden = true;
      // Invalidate any request in flight now, not when the next one starts.
      controller?.abort();
      controller = null;
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 800);
    });
    // The visual editor marks its container as live for the plain-text panel.
    visual.dataset.livePreview = "true";
  }

  // The visual editor only ever shows server-sanitized markup. It starts from
  // the stored (sanitized) content; while the Admin edits the HTML source, the
  // source is posted to the server's sanitizer after a pause and the pane is
  // redrawn from the sanitized result, never from the raw source. Paste/drop
  // into the pane are plain text.
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
    liveSource(form, visual, editor, source);
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
        if (editor.getAttribute("contenteditable") === "false") return;
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
      if (editor.getAttribute("contenteditable") === "false") return;
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
      // Like the visual preview, a response applies only to the source it
      // was generated from, so an older answer never overwrites a newer one.
      const posted = source.value;
      try {
        const response = await fetch(panel.dataset.plainTextUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"},
          body: new URLSearchParams({html: posted}), signal: request.signal
        });
        if (!response.ok) throw new Error("preview unavailable");
        const data = await response.json();
        if (typeof data.text !== "string") throw new Error("preview unavailable");
        if (box.checked && controller === request && source.value === posted) {
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
    // With a live visual editor, source edits reach the server once, through
    // the visual preview's request, which relays the generated text here.
    if (form.querySelector('[data-visual-content][data-live-preview="true"]')) {
      form.addEventListener("stewardship:source-preview", (event) => {
        if (!box.checked) return;
        // The shared answer is newer than any plain-text request in flight.
        controller?.abort();
        if (typeof event.detail?.text === "string") {
          text.value = event.detail.text;
          unavailable.hidden = true;
        } else {
          unavailable.hidden = false;
        }
      });
    } else {
      source.addEventListener("input", schedule);
    }
    form.addEventListener("stewardship:html-changed", schedule);
    form.addEventListener("submit", () => { if (box.checked) text.disabled = true; });
    window.addEventListener("pageshow", () => { text.disabled = false; });
    show();
    refresh();
  });

  // Header counters stop polling for good once the Admin session has ended
  // (401/403): repeating a refused request every 30 seconds only fills the
  // logs, and the inactivity dialog (session-v1.js) tells the Admin to sign in.
  const headerPolls = [];
  function stopHeaderPolls() {
    while (headerPolls.length) window.clearInterval(headerPolls.pop());
  }
  function signedOut(response) {
    return response.status === 401 || response.status === 403;
  }

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
      if (signedOut(response)) { stopHeaderPolls(); throw new Error("Signed out"); }
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
    headerPolls.push(window.setInterval(refreshPresence, 30000));
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
      if (signedOut(response)) { stopHeaderPolls(); throw new Error("Signed out"); }
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
  if (backgroundIndicator) headerPolls.push(window.setInterval(refreshBackground, 30000));

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
    // Once the Family form has ended the session (data-expired), keep the
    // notice shown even if the local timer still has time left.
    const ended = expired.hasAttribute("data-expired");
    warning.hidden = ended || remaining > 300000 || remaining <= 0;
    expired.hidden = !ended && remaining > 0;
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

// Toggletips, shared by the Family and Admin pages: a small "i" button beside
// a label that shows or hides one short help bubble on click, tap, Enter or
// Space. Never on hover: touch screens have none, and hover bubbles vanish
// while someone is reading them. Markup (components/toggletip.html renders it
// on the server; window.StewardshipToggletip.create(text, labelId) builds it
// in page scripts):
//   <span class="toggletip">
//     <button type="button" class="toggletip-button" aria-expanded="false"
//       aria-controls="ID" aria-label="More information"
//       aria-describedby="LABEL-ID">i</button>
//     <span class="toggletip-bubble" id="ID" hidden>Help text</span>
//   </span>
// The bubble follows its button in the document, so screen readers reach it
// next. The button's name is generic on purpose: naming it after its label
// ("More about Phone") would make it a second match for that label's text,
// both for voice control and for tests that find fields by label. Its
// description (the label, when given an id) says which field it explains. One document-level listener serves every toggletip, including ones
// page scripts add later. Only one bubble is open at a time; Escape or a
// click elsewhere closes it. Without JavaScript the bubble stays hidden and
// the button does nothing, so keep anything essential in visible text.
(() => {
  let count = 0;
  function close(tip, returnFocus = false) {
    tip.classList.remove("toggletip-open");
    tip.querySelector(".toggletip-button").setAttribute("aria-expanded", "false");
    tip.querySelector(".toggletip-bubble").hidden = true;
    if (returnFocus) tip.querySelector(".toggletip-button").focus();
  }
  function open(tip) {
    document.querySelectorAll(".toggletip-open").forEach((other) => close(other));
    const bubble = tip.querySelector(".toggletip-bubble");
    tip.classList.add("toggletip-open");
    tip.querySelector(".toggletip-button").setAttribute("aria-expanded", "true");
    bubble.hidden = false;
    // Keep the bubble on screen: a button near the right edge of a phone
    // would otherwise push it past the viewport (and scroll the page).
    bubble.style.insetInlineStart = "";
    const edge = 8, box = bubble.getBoundingClientRect();
    const overflow = box.right - (document.documentElement.clientWidth - edge);
    if (overflow > 0) {
      // Shift left by the overflow, but never past the viewport's left edge.
      bubble.style.insetInlineStart = -Math.max(0, Math.min(overflow, box.left - edge)) + "px";
    }
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.(".toggletip-button");
    const openTip = document.querySelector(".toggletip-open");
    if (button) {
      const tip = button.closest(".toggletip");
      if (tip === openTip) close(tip);
      else open(tip);
    } else if (openTip && !openTip.contains(event.target)) close(openTip);
  });
  document.addEventListener("keydown", (event) => {
    const openTip = document.querySelector(".toggletip-open");
    if (event.key === "Escape" && openTip) close(openTip, openTip.contains(document.activeElement));
  });
  window.StewardshipToggletip = {
    create(text, labelId = "") {
      // Build the markup above for script-rendered pages (the Family form).
      const tip = document.createElement("span");
      tip.className = "toggletip";
      const button = document.createElement("button");
      const id = "toggletip-" + (++count);
      Object.entries({type: "button", class: "toggletip-button", "aria-expanded": "false",
        "aria-controls": id, "aria-label": "More information"}).forEach(
        ([key, value]) => button.setAttribute(key, value));
      if (labelId) button.setAttribute("aria-describedby", labelId);
      button.textContent = "i";
      const bubble = document.createElement("span");
      bubble.className = "toggletip-bubble";
      bubble.id = id;
      bubble.hidden = true;
      bubble.textContent = text;
      tip.append(button, bubble);
      return tip;
    }
  };
})();
