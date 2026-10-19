"use strict";

// The tips of the Admin sidebar's unavailable entries (admin-portal spec,
// "Stable menu shape"). The markup is admin-navigation.html. The same tips
// serve unavailable in-page controls (components/disabled-control.html,
// navigation rule 10), and a tip of the same look shows a table name cut
// short (clipped names, at the end), so this file is deferred and runs once
// the whole page is parsed. The menu's remembered group and scroll state is
// admin-menu-state-v1.js, which must run earlier. This file is kept out of
// ui-v1.js, which every page loads, and the Admin portal requires JavaScript
// (#565), so there is no no-script fallback here.
(() => {
  // Unavailable entries and controls. Each is a link without href, in the tab
  // order, whose reason sits in a role="tooltip" element named by its
  // aria-describedby, so screen readers announce it with the entry.
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
    // Activating an unavailable entry does nothing but show its reason. A
    // control that is only sometimes unavailable (Clear filters, #1651:
    // ui-v1.js marks it aria-disabled and names its tip only while it has
    // nothing to do) acts normally while it is available.
    entry.addEventListener("click", (event) => {
      if (entry.getAttribute("aria-disabled") !== "true") return;
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

// Clipped names (#1136): a name in a table cell that its column cuts short
// ([data-clip-tip], such as the Ministry follow-up queue's Member and a
// Ministry name too long for two lines) shows whole in a tip styled like
// the menu's. The whole name is already the element's text, so screen
// readers hear it; the tip only repeats it on screen and is aria-hidden.
// A name that fits shows no tip. One document-level listener serves every
// row, including rows an in-place filter, sort or page change brings in.
//
// Pointer: the tip shows after the pointer rests on the name for
// SHOW_DELAY. It stays while the pointer is over the name or the tip, so
// the pointer can move onto it (WCAG 1.4.13); HIDE_GRACE covers the gap
// between them, and leaving both hides it.
// Keyboard: focus shows it at once. A clipped name that is not a link is
// made focusable so the keyboard reaches it (mark(), rerun after an
// in-place swap and on resize, since what is clipped depends on the
// width); the focused name keeps its place in the tab order until focus
// leaves it, so a resize never drops focus.
// Touch: a tap shows it (a tap on a link also opens the link, whose page
// shows the name whole). A tapped tip ignores the pointer, so the next tap
// anywhere both hides it and acts on whatever was tapped.
// Escape, or a tap or click elsewhere, hides it. The tip is fixed under the
// name, or above it with no room below, and follows it as the page, the
// table or the window changes; it moves nothing on the page.
(() => {
  const SHOW_DELAY = 350;
  const HIDE_GRACE = 150;
  const GAP = 6;
  const SELECTOR = "[data-clip-tip]";
  let tip = null;
  let shown = null;
  let showTimer = null;
  let hideTimer = null;
  const clipped = (name) =>
    name.scrollWidth > name.clientWidth + 1 || name.scrollHeight > name.clientHeight + 1;
  const nameOf = (target) => (target && target.closest ? target.closest(SELECTOR) : null);
  const onTip = (target) => Boolean(tip && target && tip.contains(target));
  const clearTimers = () => {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
  };
  const hide = () => {
    clearTimers();
    if (tip) tip.classList.remove("is-shown", "is-tapped");
    shown = null;
  };
  // Hide after HIDE_GRACE unless the name keeps focus or the pointer
  // reaches the name or the tip first.
  const hideSoon = () => {
    clearTimeout(hideTimer);
    if (shown && document.activeElement !== shown) hideTimer = setTimeout(hide, HIDE_GRACE);
  };
  // Put the shown tip under its name, or above it with no room below, kept
  // inside the viewport; rerun on scroll and resize, since a fixed tip
  // stays put.
  const place = () => {
    const box = shown.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    let top = box.bottom + GAP;
    if (top + height > window.innerHeight - GAP) top = box.top - height - GAP;
    tip.style.left = `${Math.round(Math.max(GAP, Math.min(box.left, window.innerWidth - GAP - width)))}px`;
    tip.style.top = `${Math.round(Math.max(GAP, top))}px`;
  };
  const show = (name, byTap) => {
    clearTimers();
    if (!name.isConnected || !clipped(name)) return;
    if (!tip) {
      tip = document.createElement("span");
      tip.className = "admin-tip";
      tip.setAttribute("aria-hidden", "true");
      document.body.append(tip);
      // Over the tip the pointer keeps it shown; leaving it hides it,
      // unless the pointer went back to its name.
      tip.addEventListener("mouseenter", () => clearTimeout(hideTimer));
      tip.addEventListener("mouseleave", (event) => {
        if (shown && !shown.contains(event.relatedTarget)) hideSoon();
      });
    }
    tip.textContent = name.textContent.trim();
    tip.classList.add("is-shown");
    tip.classList.toggle("is-tapped", Boolean(byTap));
    shown = name;
    place();
  };
  // Clipped names that are not links join the tab order; a name that fits
  // again leaves it, except the focused one, which focusout releases.
  const mark = (root) => {
    root.querySelectorAll(`${SELECTOR}:not(a)`).forEach((name) => {
      if (clipped(name)) name.setAttribute("tabindex", "0");
      else if (name !== document.activeElement) name.removeAttribute("tabindex");
    });
  };
  mark(document);
  document.addEventListener("parishkit:swap", (event) => { hide(); mark(event.target); });
  window.addEventListener("resize", () => {
    if (shown && clipped(shown)) place();
    else hide();
    mark(document);
  });
  document.addEventListener("mouseover", (event) => {
    if (onTip(event.target)) return;
    const name = nameOf(event.target);
    if (!name) return;
    if (name === shown) { clearTimeout(hideTimer); return; }
    hide();
    showTimer = setTimeout(() => show(name, false), SHOW_DELAY);
  });
  document.addEventListener("mouseout", (event) => {
    const name = nameOf(event.target);
    if (!name || name.contains(event.relatedTarget) || onTip(event.relatedTarget)) return;
    clearTimeout(showTimer);
    if (name === shown && !tip.classList.contains("is-tapped")) hideSoon();
  });
  // A tap focuses the name it shows; the tip that tap opened is already
  // shown and keeps its tapped mode, so it never swallows the next tap.
  document.addEventListener("focusin", (event) => {
    const name = nameOf(event.target);
    if (name && name !== shown) show(name, false);
  });
  document.addEventListener("focusout", (event) => {
    const name = nameOf(event.target);
    if (!name) return;
    if (name === shown) hide();
    if (!name.matches("a") && !clipped(name)) name.removeAttribute("tabindex");
  });
  document.addEventListener("pointerdown", (event) => {
    if (onTip(event.target)) return;
    const name = nameOf(event.target);
    if (event.pointerType === "touch" && name) show(name, true);
    else if (shown && name !== shown) hide();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });
  window.addEventListener("scroll", () => { if (shown) place(); }, {capture: true, passive: true});
})();
