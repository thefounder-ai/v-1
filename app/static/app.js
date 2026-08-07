(function () {
  "use strict";

  const toggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector("[data-app-nav]");
  const sidebar = document.querySelector("[data-app-sidebar]");
  if (toggle && (nav || sidebar)) {
    toggle.addEventListener("click", function () {
      const target = sidebar || nav;
      const open = target.classList.toggle("is-open");
      if (nav && sidebar) nav.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  document.querySelectorAll(".flash-dismiss").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const flash = btn.closest(".flash");
      if (flash) flash.remove();
    });
  });
})();
