/* Old-browser notice for the Family form (#384 L1).

   family-v1.js needs a browser from roughly 2022 or later (Safari 15.4,
   Chrome 98, Firefox 104). On anything older it either fails to parse or
   throws at "Begin", so the Family sees a form that silently does nothing.
   This script only reveals the hidden, server-translated notice in
   family.html when a required feature is missing. It never touches the form.

   It must stay ES5 (var, function, no arrows, no template strings) so that
   the very browsers it warns about can parse and run it.

   Syntax such as optional chaining (?.), ?? and ||= cannot be tested by
   running it: a script that uses it fails to parse as a whole, and compiling
   a test string with new Function() or eval() is forbidden by the
   Content-Security-Policy (script-src 'self', no 'unsafe-eval'). Instead we
   check APIs that every engine shipped after that syntax: structuredClone,
   Object.hasOwn, Array.prototype.at and findLastIndex arrived in Safari 15.4,
   Chrome 92-98 and Firefox 90-104, all later than optional chaining (Safari
   13.1 / iOS 13.4, Chrome 80, Firefox 74) and logical assignment (Safari 14, Chrome 85,
   Firefox 79). A browser with all of these APIs therefore parses the syntax
   too, and this avoids a second request for a probe module. */
(function () {
  "use strict";
  try {
    var notice = document.getElementById("browser-unsupported");
    if (!notice) return;
    var element = window.Element && window.Element.prototype;
    // Everything family-v1.js calls that an older supported-looking browser
    // might lack. crypto.randomUUID also needs a secure context; production
    // forces HTTPS (and 127.0.0.1 counts as secure in tests), so on a plain
    // HTTP host the notice is correct: Add member would fail there anyway.
    var required = [
      typeof window.structuredClone === "function",
      typeof Object.hasOwn === "function",
      typeof Object.fromEntries === "function",
      typeof Array.prototype.at === "function",
      typeof Array.prototype.findLastIndex === "function",
      typeof String.prototype.replaceAll === "function",
      !!element && typeof element.replaceChildren === "function",
      !!window.crypto && typeof window.crypto.randomUUID === "function",
      typeof window.fetch === "function",
      typeof window.URL === "function"
    ];
    for (var i = 0; i < required.length; i++) {
      if (!required[i]) {
        notice.hidden = false;
        return;
      }
    }
  } catch (error) {
    // A feature check must never break the page; stay silent.
  }
})();
