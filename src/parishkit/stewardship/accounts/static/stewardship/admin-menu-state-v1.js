"use strict";

// The Admin sidebar's remembered state: which menu groups an Admin collapsed,
// and where the menu link an Admin followed sat on screen (admin-portal spec,
// "Menu groups"). admin-navigation.html loads this file without defer right
// after the sidebar, so it runs once the menu is parsed but before the page
// content is, and the menu is set before the first paint where the browser
// allows, rather than jumping after the page shows. The Admin
// portal requires JavaScript (#565), so there is no no-script fallback here.
// Storage can be unavailable (private windows, blocked site data); every
// access is guarded and the menu then just starts with every group open and
// the current page's entry in view.
(() => {
  const sidebar = document.querySelector(".admin-sidebar");
  if (!sidebar) return;

  // Collapsible groups. Groups render open. Each browser remembers which
  // groups an Admin collapsed: only a "closed" marker under the group's key,
  // removed when the group is opened again, so with nothing stored every
  // group starts open. The group holding the current page (data-menu-current)
  // always opens, so its aria-current entry is never hidden; that does not
  // forget the stored choice for other pages.
  //
  // The choice is recorded from the summary's click (Enter and Space on a
  // summary click it too), not from the "toggle" event: browsers also fire
  // "toggle" for a group parsed with the open attribute, and WebKit delivers
  // it after this script runs, which would forget every stored choice. The
  // click fires before the browser flips the group, so the new state is the
  // opposite of the current one.
  const storageKey = (group) => `pk-admin-menu-group:${group.dataset.menuGroup}`;
  sidebar.querySelectorAll("details[data-menu-group]").forEach((group) => {
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

  // Scroll position (#620). On wide screens the sidebar scrolls on its own.
  // Following one of its links saves, under one key in this tab's
  // sessionStorage, the link's href and its top edge on screen; the next
  // page scrolls the menu so the same link sits at the same height, so the
  // entry just clicked stays under the pointer. Matching the link's screen
  // position, not the menu's scrollTop, also holds when the page itself was
  // scrolled (the sticky sidebar then starts at the window's top instead of
  // below the header) and when a group above the link opened or closed. The
  // page content still starts at its top. The value is read once and
  // removed, and is used only on the page its link names, so a page reached
  // any other way (a reload, a new tab, a link from elsewhere, a cancelled
  // navigation) does not reuse a stale one. On narrow screens the sidebar is
  // not a scroll area, so setting its scrollTop does nothing and the page
  // itself is never scrolled. This runs after the groups above are set, so
  // it sees the menu's final shape.
  const SCROLL_KEY = "pk-admin-menu-scroll";
  // The value is removed before it is parsed, so a malformed one never
  // lingers, and the parse and the URL check stay inside the try, so a bad
  // value cannot stop this script before the click handler below attaches.
  let saved = null;
  try {
    const raw = window.sessionStorage.getItem(SCROLL_KEY);
    window.sessionStorage.removeItem(SCROLL_KEY);
    const value = JSON.parse(raw);
    if (
      value && typeof value.href === "string" && typeof value.top === "number" &&
      new URL(value.href, window.location.href).pathname === window.location.pathname
    ) {
      saved = value;
    }
  } catch (error) { /* Nothing saved without storage, or nothing usable. */ }
  if (saved) {
    // The clicked link, or the current page's entry if the page does not
    // list that exact href.
    const link =
      Array.from(sidebar.querySelectorAll("a[href]")).find(
        (node) => node.getAttribute("href") === saved.href
      ) || sidebar.querySelector("[aria-current]");
    if (link) sidebar.scrollTop += link.getBoundingClientRect().top - saved.top;
  }

  // The saved height cannot always be reached. After a click with the page
  // scrolled down (the header off screen), the new page's menu starts a
  // header's height lower and still reaches past the window's bottom, so
  // an entry near the menu's end, where the menu cannot scroll further, can
  // land up to a header's height lower than it was, possibly below the
  // window. Nothing here can scroll the menu past its end.
  //
  // With nothing saved, or when the saved position cannot be reached (the
  // link would sit above the menu's visible top), bring the current page's
  // entry into view, but only if it is outside the visible part of the menu,
  // and only by scrolling the menu. This is scrollIntoView's "nearest" rule
  // written out, because scrollIntoView would also scroll the page on narrow
  // screens. The visible part ends at the window's bottom edge, since the
  // sticky sidebar can start below the header and reach past the window.
  const current = sidebar.querySelector("[aria-current]");
  if (current && current.getClientRects().length) {
    const view = sidebar.getBoundingClientRect();
    const box = current.getBoundingClientRect();
    const bottom = Math.min(view.bottom, window.innerHeight);
    if (box.top < view.top) sidebar.scrollTop -= view.top - box.top;
    else if (box.bottom > bottom) sidebar.scrollTop += box.bottom - bottom;
  }

  // Save on a plain activation of a menu link: a click, or Enter, which
  // clicks it too. A modified click opens another tab or window and leaves
  // this page in place, so it saves nothing. Unavailable entries have no
  // href and the Sign out button is a form, so neither saves.
  sidebar.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (
      !link || event.defaultPrevented || event.button !== 0 ||
      event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
    ) {
      return;
    }
    try {
      window.sessionStorage.setItem(SCROLL_KEY, JSON.stringify({
        href: link.getAttribute("href"),
        top: link.getBoundingClientRect().top,
      }));
    } catch (error) { /* The next page shows its own entry instead. */ }
  });
})();
