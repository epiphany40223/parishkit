// Shared telephone display and lenient parsing for browser pages.
//
// Mirrors web/presentation.py's phone() for North American numbers: stored
// E.164 ("+15025551234") and typed text ("(502) 555-1234", "502.555.1234",
// "+1 502 555 1234") all display as "+1 (502) 555-1234", with an optional
// " ext. N". The browser has no numbering-plan metadata, so other countries
// are shown exactly as typed; the server's phonenumbers validation remains the
// authority for what is accepted and stored.
(function () {
  "use strict";

  const EXTENSION = /\s*(?:;ext=|ext\.?|x|#)\s*([0-9]{1,12})\s*$/i;
  const SHAPE = /^\+?[0-9 ().\-]+$/;

  // Split "number ext. 12" into its parts; the extension may be absent.
  function split(text) {
    const match = EXTENSION.exec(text);
    return match ? [text.slice(0, match.index).trim(), match[1]] : [text, ""];
  }

  // Return the ten national digits of a North American number, or null.
  function nanp(number) {
    if (!SHAPE.test(number)) return null;
    let digits = number.replace(/[^0-9]/g, "");
    const international = number.trim().startsWith("+");
    if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
    else if (international || digits.length !== 10) return null;
    // Area code and exchange cannot begin with 0 or 1.
    return /^[2-9][0-9]{2}[2-9][0-9]{6}$/.test(digits) ? digits : null;
  }

  // Display any stored or typed value; unrecognized text is returned unchanged.
  function format(value) {
    const text = String(value ?? "").trim();
    if (!text) return "";
    const [number, extension] = split(text);
    const digits = nanp(number);
    if (!digits) return text;
    const shown = "+1 (" + digits.slice(0, 3) + ") " + digits.slice(3, 6) + "-" + digits.slice(6);
    return extension ? shown + " ext. " + extension : shown;
  }

  // Canonical E.164 (with ";ext=N") for a North American number, else null.
  function parse(value) {
    const [number, extension] = split(String(value ?? "").trim());
    const digits = nanp(number);
    if (!digits) return null;
    return "+1" + digits + (extension ? ";ext=" + extension : "");
  }

  // Same-meaning comparison: formatting punctuation never counts as a change.
  function same(left, right) {
    const a = parse(left);
    const b = parse(right);
    return a !== null && b !== null ? a === b : String(left ?? "").trim() === String(right ?? "").trim();
  }

  window.StewardshipPhone = Object.freeze({format, parse, same});
})();
