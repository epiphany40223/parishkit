"use strict";

// The Admin sidebar's collapsible menu groups and the tips of its unavailable
// entries (admin-portal spec, "Menu groups" and "Stable menu shape"). The
// markup is admin-navigation.html. This file is kept out of ui-v1.js, which
// every page loads, and the Admin portal requires JavaScript (#565), so there
// is no no-script fallback here.
(() => {
  // Collapsible groups. Groups render open. Each browser remembers which
  // groups an Admin collapsed: only a "closed" marker under the group's key,
  // removed when the group is opened again, so with nothing stored every
  // group starts open. The group holding the current page (data-menu-current)
  // always opens, so its aria-current entry is never hidden; that does not
  // forget the stored choice for other pages. Storage can be unavailable
  // (private windows, blocked site data); groups then just start open.
  //
  // The choice is recorded from the summary's click (Enter and Space on a
  // summary click it too), not from the "toggle" event: browsers also fire
  // "toggle" for a group parsed with the open attribute, and WebKit delivers
  // it after this script runs, which would forget every stored choice. The
  // click fires before the browser flips the group, so the new state is the
  // opposite of the current one.
  const storageKey = (group) => `pk-admin-menu-group:${group.dataset.menuGroup}`;
  document.querySelectorAll("details[data-menu-group]").forEach((group) => {
    try {
      if (
        !group.hasAttribute("data-menu-current") &&
        window.localStorage.getItem(storageKey(group)) === "closed"
      ) {
        group.open = false;
      }
    } catch (error) { /* Keep the group open. */ }
    const summary = group.querySelector(":scope > summary");
    if (!summary) return;
    summary.addEventListener("click", () => {
      try {
        if (group.open) window.localStorage.setItem(storageKey(group), "closed");
        else window.localStorage.removeItem(storageKey(group));
      } catch (error) { /* Nothing to remember without storage. */ }
    });
  });

  // Unavailable entries. Each is a link without href, in the tab order, whose
  // reason sits in a role="tooltip" element named by its aria-describedby, so
  // screen readers announce it with the entry.
  //
  // The tip opens BESIDE the entry, to the right of its row, so it never
  // covers the entries below it; with no room there (narrow screens) it opens
  // above the entry instead, or below if above does not fit. It is fixed-
  // positioned from the entry's box because the sidebar's own scroll area
  // would clip anything placed outside it.
  //
  // Pointer: the tip shows after the pointer rests on the entry for SHOW_DELAY,
  // so passing over an entry pops nothing. It stays while the pointer is over
  // the entry or its tip (both sit in one wrapper; HIDE_GRACE covers the gap
  // between them) or while the entry has focus, and hides at once when the
  // pointer reaches another link or control.
  // Keyboard: focus shows it at once. Escape hides it without moving focus or
  // the pointer (WCAG 1.4.13).
  // Touch: a tap shows it at once. A tapped tip ignores pointer events, so
  // the next tap anywhere, even on another entry under the tip, both hides
  // it and acts on whatever was tapped. Only a tap or Escape hides it: the
  // mouse events browsers emulate around a tap must not.
  const SHOW_DELAY = 350;
  const HIDE_GRACE = 150;
  const GAP = 6;
  const entries = Array.from(document.querySelectorAll("[data-menu-tip]"));
  const tipOf = (entry) => document.getElementById(entry.getAttribute("aria-describedby"));
  // The wrapper holds the entry and its tip: the menu's list item, or an
  // in-page control's span.
  const wrapperOf = (entry) => entry.parentElement;
  let shown = null;
  let showTimer = null;
  let hideTimer = null;
  let lastPointer = "mouse";
  let tapped = false;
  const clearTimers = () => {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
  };

  const place = (entry, tip) => {
    // "Beside" a menu entry is past the sidebar's edge, so the tip covers no
    // entry; an in-page control's tip sits just past the control.
    const row = entry.closest(".admin-sidebar") || entry;
    const box = entry.getBoundingClientRect();
    const right = row.getBoundingClientRect().right;
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    let left = right + GAP;
    let top = box.top + (box.height - height) / 2;
    if (left + width > window.innerWidth - GAP) {
      left = Math.max(GAP, Math.min(box.left, window.innerWidth - GAP - width));
      top = box.top - height - GAP;
      if (top < GAP) top = box.bottom + GAP;
    }
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(Math.max(GAP, top))}px`;
  };
  const hide = () => {
    clearTimers();
    if (shown) {
      const tip = tipOf(shown);
      if (tip) tip.classList.remove("is-shown", "is-tapped");
    }
    shown = null;
    tapped = false;
  };
  const show = (entry, byTap) => {
    clearTimers();
    if (shown && shown !== entry) hide();
    const tip = tipOf(entry);
    if (!tip) return;
    tip.classList.add("is-shown");
    tip.classList.toggle("is-tapped", Boolean(byTap));
    place(entry, tip);
    shown = entry;
    tapped = Boolean(byTap);
  };

  entries.forEach((entry) => {
    const wrapper = wrapperOf(entry);
    wrapper.addEventListener("mouseenter", () => {
      if (tapped || lastPointer === "touch") return;
      if (shown === entry) { clearTimers(); return; }
      if (shown) hide();
      clearTimers();
      showTimer = setTimeout(() => show(entry, false), SHOW_DELAY);
    });
    wrapper.addEventListener("mouseleave", () => {
      clearTimeout(showTimer);
      if (tapped) return;
      if (shown === entry && document.activeElement !== entry) {
        hideTimer = setTimeout(hide, HIDE_GRACE);
      }
    });
    entry.addEventListener("focus", () => show(entry, false));
    entry.addEventListener("blur", () => {
      if (shown === entry && (tapped || !wrapper.matches(":hover"))) hide();
    });
    entry.addEventListener("pointerdown", (event) => { lastPointer = event.pointerType; });
    // Activating an unavailable entry does nothing but show its reason.
    entry.addEventListener("click", (event) => {
      event.preventDefault();
      show(entry, lastPointer === "touch");
    });
  });
  if (entries.length) {
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hide();
    });
    // pointerdown, not click: touch browsers send no click for a tap on
    // content that is not interactive, and that tap must still dismiss.
    document.addEventListener("pointerdown", (event) => {
      lastPointer = event.pointerType;
      if (shown && !wrapperOf(shown).contains(event.target)) hide();
    });
    // Reaching another link or control hides the tip at once.
    document.addEventListener("mouseover", (event) => {
      if (
        shown && !tapped && !wrapperOf(shown).contains(event.target) &&
        event.target.closest && event.target.closest("a, button, summary, input, select, textarea")
      ) {
        hide();
      }
    });
    // Keep a shown tip beside its entry while the page or sidebar scrolls.
    const follow = () => { if (shown) place(shown, tipOf(shown)); };
    window.addEventListener("scroll", follow, {capture: true, passive: true});
    window.addEventListener("resize", follow);
  }
})();
