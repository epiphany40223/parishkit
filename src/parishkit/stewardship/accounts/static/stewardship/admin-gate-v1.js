"use strict";

// The Admin portal requires JavaScript (#565). Admin pages render with a
// js-required class on <html>, which hides the page behind a "needs
// JavaScript" panel (ui-v1.css). This file loads in <head> without defer, so
// removing the class here happens before the body is parsed and a browser
// with JavaScript never shows the panel. Keep this file this small: it
// blocks page rendering while it loads.
document.documentElement.classList.remove("js-required");
