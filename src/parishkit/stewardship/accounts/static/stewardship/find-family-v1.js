"use strict";

// The header's Find a Family box (#561; admin-portal spec, "Admin
// navigation"). The markup is find-family.html, rendered only for viewers
// who may use it; the answer is find-family-results.html.
//
// Load: no request per keystroke. A search starts after a pause in typing
// (PAUSE), only for MINIMUM or more characters, and a newer search aborts an
// older one still in flight, so a lookup costs a few bounded reads. Enter
// searches at once.
//
// Privacy: the text goes in a CSRF POST body, never a URL, so it stays out
// of the address bar, the history and server logs.
//
// Keyboard: the results are ordinary links (and a "See all" button) below
// the field. Down arrow in the field moves to the first (or reopens a
// closed answer), Up and Down move between them (Up from the first returns
// to the field), and Escape, in the field or the results, closes them and
// returns to the field. Tab leaves as usual, and leaving the box closes
// it. A polite live region says how many Families matched.
(() => {
  const PAUSE = 300;
  const MINIMUM = 2;
  const box = document.querySelector("[data-find-family]");
  if (!box) {
    return;
  }
  const form = box.querySelector("[data-find-family-form]");
  const input = box.querySelector("[data-find-family-input]");
  const status = box.querySelector("[data-find-family-status]");
  const results = box.querySelector("[data-find-family-results]");
  let timer = null;
  let controller = null;
  let announcement = null;
  // The text of the search whose answer is shown, so an unchanged field
  // (an arrow key, a repeated Enter) does not search again. It is null
  // while a search is pending or after one failed, so retyping the same
  // text searches again rather than showing a stale or failed answer.
  let shown = null;

  // Clear the live status first and write it a moment later, so the same
  // words twice in a row (the same count for a new search) are announced
  // again rather than ignored as unchanged.
  const say = (text) => {
    clearTimeout(announcement);
    status.textContent = "";
    if (text) {
      announcement = setTimeout(() => {
        status.textContent = text;
      }, 100);
    }
  };

  // Whether the reader is still in the box. Results never open over the
  // page once focus has moved on, even if an answer arrives late.
  const inBox = () => box.contains(document.activeElement);

  // A search field may not carry aria-expanded (it is no combobox); the
  // live status announces each answer instead.
  const close = () => {
    results.hidden = true;
  };

  const open = () => {
    if (inBox()) {
      results.hidden = false;
    }
  };

  // Stop a pending (timer) or in-flight search; its answer is never shown.
  const cancel = () => {
    clearTimeout(timer);
    controller?.abort();
    controller = null;
  };

  // Drop any pending or in-flight search and the shown answer.
  const reset = () => {
    cancel();
    shown = null;
    results.replaceChildren();
    close();
  };

  const targets = () => Array.from(results.querySelectorAll("[data-find-family-result]"));

  // Fixed text for a refused or failed search; the server never words it.
  // A redirect (to the sign-in page) or a 403 means the session ended or
  // access was removed: the wording matches the other live Admin regions.
  const failure = (statusCode) => {
    if (statusCode === 400) {
      return "That search could not be used. Try a shorter one.";
    }
    if (statusCode === 401 || statusCode === 403) {
      return "Couldn't search. Refresh the page or sign in again.";
    }
    return "Find a Family is unavailable right now. Try again in a moment.";
  };

  const showMessage = (text) => {
    const note = document.createElement("p");
    note.className = "find-family-summary";
    note.textContent = text;
    results.replaceChildren(note);
    open();
    say(text);
  };

  // Post the field's text and show the answer. Only the newest search's
  // answer is shown. cancel() aborts the request in flight and clears
  // controller whenever the field changes, a newer search starts, the field
  // returns to the text already shown, or focus leaves; an answer that
  // still arrives for that request then fails the controller guard below
  // and is dropped, never shown or announced.
  const search = async () => {
    clearTimeout(timer);
    const text = input.value.trim();
    if (text.length < MINIMUM) {
      reset();
      return;
    }
    if (text === shown) {
      // The answer on screen is for this text already: reopen it. The
      // input handler has already cancelled any search for other text.
      cancel();
      open();
      return;
    }
    // A new search. Until its answer lands nothing matches the field, so
    // shown is cleared: retyping this text after a failure searches again.
    cancel();
    shown = null;
    const mine = new AbortController();
    controller = mine;
    // A form-encoded body (with the CSRF token), as the form itself posts.
    const body = new URLSearchParams(new FormData(form));
    body.set("search", text);
    let response;
    let answer;
    try {
      response = await fetch(form.action, {
        method: "POST",
        body,
        credentials: "same-origin",
        signal: mine.signal,
      });
      answer = await response.text();
    } catch (error) {
      if (controller !== mine) {
        return;
      }
      controller = null;
      showMessage(failure(0));
      return;
    }
    if (controller !== mine) {
      return;
    }
    controller = null;
    // A redirect is never a search answer: something in front of the view
    // (an ended session's sign-in page) answered instead. The POST is not
    // repeated and the page is not left; the reader is told what to do.
    if (response.redirected) {
      showMessage(failure(401));
      return;
    }
    if (!response.ok) {
      showMessage(failure(response.status));
      return;
    }
    const parsed = new DOMParser().parseFromString(answer, "text/html");
    results.replaceChildren(...parsed.body.childNodes);
    shown = text;
    open();
    say(results.querySelector("[data-find-family-summary]")?.textContent.trim() || "");
  };

  // Any edit cancels the pending and in-flight search at once, so an
  // answer for older text can never appear (or be announced) during the
  // pause before the new search starts.
  input.addEventListener("input", () => {
    cancel();
    if (input.value.trim().length < MINIMUM) {
      reset();
      say("");
      return;
    }
    timer = setTimeout(search, PAUSE);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim().length < MINIMUM) {
      say("Type at least 2 characters to find a Family.");
      return;
    }
    search();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      const first = targets()[0];
      if (first && !results.hidden) {
        event.preventDefault();
        first.focus();
      } else if (shown !== null) {
        event.preventDefault();
        open();
      }
    } else if (event.key === "Escape" && !results.hidden) {
      event.preventDefault();
      close();
    }
  });

  results.addEventListener("keydown", (event) => {
    const list = targets();
    const index = list.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      input.focus();
    } else if (event.key === "ArrowDown" && index >= 0) {
      event.preventDefault();
      list[Math.min(index + 1, list.length - 1)].focus();
    } else if (event.key === "ArrowUp" && index >= 0) {
      event.preventDefault();
      (index === 0 ? input : list[index - 1]).focus();
    }
  });

  // A press on a result keeps focus where it was. Safari (WebKit) does
  // not focus a clicked button, so without this, pressing "See all" would
  // move focus out of the box, close the results before the click landed
  // and the click would do nothing. The click itself still follows the
  // link or submits the form.
  results.addEventListener("mousedown", (event) => {
    event.preventDefault();
  });

  // Once focus or a click leaves the whole box, close the results and stop
  // any pending or in-flight search, so nothing opens over the page the
  // reader moved on to (where Escape would not reach it). Coming back and
  // pressing Enter or Down searches again or reopens the shown answer.
  box.addEventListener("focusout", (event) => {
    if (!box.contains(event.relatedTarget)) {
      cancel();
      close();
    }
  });
  document.addEventListener("click", (event) => {
    if (!box.contains(event.target)) {
      cancel();
      close();
    }
  });
  // A page restored from the back/forward cache keeps its last DOM, so
  // close an answer that was open when the reader followed a result.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      cancel();
      close();
    }
  });
})();
