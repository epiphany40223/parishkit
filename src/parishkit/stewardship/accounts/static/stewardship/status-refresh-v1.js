// Reload a passive status page while its work is still pending.
//
// Pages opt in with a data-status-refresh element. Status reads are passive
// (they never extend a sign-in), so reloading is safe; it pauses while the
// tab is hidden and slows down after the first minute.
(function () {
  "use strict";
  var marker = document.querySelector("[data-status-refresh]");
  if (!marker) {
    return;
  }
  var started = Date.now();

  function editing() {
    // Never discard what someone is typing: a focused or changed form field
    // postpones the reload until the field is left unchanged again.
    var active = document.activeElement;
    if (active && active.form) {
      return true;
    }
    return Array.prototype.some.call(
      document.querySelectorAll("input, textarea, select"),
      function (field) {
        if (field.type === "hidden" || field.type === "submit") {
          return false;
        }
        if (field.type === "checkbox" || field.type === "radio") {
          return field.checked !== field.defaultChecked;
        }
        if (field.tagName === "SELECT") {
          return Array.prototype.some.call(field.options, function (option) {
            return option.selected !== option.defaultSelected;
          });
        }
        return field.value !== field.defaultValue;
      }
    );
  }

  function delay() {
    // Quick checks at first; most changes apply within seconds.
    return Date.now() - started < 60000 ? 2000 : 10000;
  }

  function schedule() {
    window.setTimeout(function () {
      if (editing()) {
        schedule();
      } else if (document.visibilityState === "visible") {
        window.location.reload();
      } else {
        document.addEventListener("visibilitychange", function once() {
          document.removeEventListener("visibilitychange", once);
          window.location.reload();
        });
      }
    }, delay());
  }

  schedule();
})();
