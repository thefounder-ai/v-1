(function () {
  "use strict";

  const toggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector("[data-app-nav]");
  const sidebar = document.querySelector("[data-app-sidebar]");
  let backdrop = document.querySelector("[data-sidebar-backdrop]");

  function ensureBackdrop() {
    if (backdrop || !sidebar) return backdrop;
    backdrop = document.createElement("div");
    backdrop.className = "sidebar-backdrop";
    backdrop.setAttribute("data-sidebar-backdrop", "");
    backdrop.hidden = true;
    document.body.appendChild(backdrop);
    backdrop.addEventListener("click", closeSidebar);
    return backdrop;
  }

  function isMobileNav() {
    return window.matchMedia("(max-width: 960px)").matches;
  }

  function closeSidebar() {
    if (!sidebar) return;
    sidebar.classList.remove("is-open");
    if (nav) nav.classList.remove("is-open");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
    if (backdrop) backdrop.hidden = true;
    document.body.classList.remove("sidebar-open");
  }

  function openSidebar() {
    if (!sidebar) return;
    ensureBackdrop();
    sidebar.classList.add("is-open");
    if (nav) nav.classList.add("is-open");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
    if (backdrop) backdrop.hidden = false;
    document.body.classList.add("sidebar-open");
  }

  if (toggle && (nav || sidebar)) {
    toggle.addEventListener("click", function () {
      if (sidebar && sidebar.classList.contains("is-open")) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });
  }

  if (sidebar) {
    sidebar.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        if (isMobileNav()) closeSidebar();
      });
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeSidebar();
  });

  window.addEventListener("resize", function () {
    if (!isMobileNav()) closeSidebar();
  });

  document.querySelectorAll(".flash-dismiss").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const flash = btn.closest(".flash");
      if (flash) flash.remove();
    });
  });
})();
