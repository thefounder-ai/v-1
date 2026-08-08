(function () {
  "use strict";

  function stepsFromDom() {
    return Array.prototype.map.call(
      document.querySelectorAll("#demo-step-list .demo-step"),
      function (el, index) {
        return {
          index: index,
          title: el.querySelector("strong") ? el.querySelector("strong").textContent : "Step " + (index + 1),
          detail: el.querySelector("small") ? el.querySelector("small").textContent : "",
          href: el.getAttribute("data-href") || "/",
          action: el.getAttribute("data-action") || "",
          element: el,
        };
      }
    );
  }

  function setActiveStep(steps, activeIndex) {
    steps.forEach(function (step, index) {
      step.element.classList.toggle("is-active", index === activeIndex);
      step.element.classList.toggle("is-done", index < activeIndex);
    });
  }

  function setStatus(message, isError) {
    var node = document.getElementById("demo-run-status");
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("is-error", Boolean(isError));
  }

  function postJson(url) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          var detail = body && (body.detail || body.message);
          throw new Error(detail || "Request failed.");
        }
        return body;
      });
    });
  }

  function runDemoSeedIfAdmin() {
    var flag = document.getElementById("demo-admin-flag");
    if (!flag || flag.getAttribute("data-admin") !== "true") {
      return Promise.resolve(null);
    }
    setStatus("Seeding demo activity…");
    return postJson("/api/admin/demo-seed").then(function (result) {
      setStatus("Seeded " + (result.events_seeded || 0) + " events.");
      return result;
    });
  }

  function refreshProfile() {
    setStatus("Syncing interest profile…");
    return postJson("/api/interest-profile/refresh");
  }

  function generatePath() {
    setStatus("Running LangGraph pipeline (retrieve → Mesh)…");
    return postJson("/api/recommendations/generate?force=true");
  }

  function bindStepNavigation() {
    var steps = stepsFromDom();
    if (!steps.length) return;

    var overlay = document.getElementById("demo-overlay");
    var overlayTitle = document.getElementById("demo-overlay-title");
    var overlayDetail = document.getElementById("demo-overlay-detail");
    var current = 0;

    function openOverlay(index) {
      current = index;
      var step = steps[current];
      if (!overlay || !step) return;
      overlayTitle.textContent = step.title;
      overlayDetail.textContent = step.detail;
      overlay.classList.remove("hidden");
      setActiveStep(steps, current);
    }

    function closeOverlay() {
      if (overlay) overlay.classList.add("hidden");
    }

    steps.forEach(function (step) {
      var go = step.element.querySelector(".demo-step-go");
      if (go) {
        go.addEventListener("click", function () {
          if (step.action === "generate" || step.action === "refresh") {
            openOverlay(step.index);
            return;
          }
          window.location.href = step.href;
        });
      }
    });

    var nextBtn = document.getElementById("demo-overlay-next");
    if (nextBtn) {
      nextBtn.addEventListener("click", function () {
        var step = steps[current];
        if (!step) return;
        if (step.action === "generate" || step.action === "refresh") {
          closeOverlay();
          generatePath().then(function () {
            window.location.href = "/trace";
          }).catch(function (error) {
            setStatus(error.message, true);
          });
          return;
        }
        if (current >= steps.length - 1) {
          closeOverlay();
          return;
        }
        openOverlay(current + 1);
      });
    }

    var skipBtn = document.getElementById("demo-overlay-skip");
    if (skipBtn) skipBtn.addEventListener("click", closeOverlay);

    var autoBtn = document.getElementById("demo-auto-run");
    if (autoBtn) {
      autoBtn.addEventListener("click", function () {
        autoBtn.disabled = true;
        runDemoSeedIfAdmin()
          .catch(function () { /* optional for non-admin */ })
          .then(refreshProfile)
          .then(generatePath)
          .then(function () {
            window.location.href = "/dashboard";
          })
          .catch(function (error) {
            setStatus(error.message, true);
            autoBtn.disabled = false;
          });
      });
    }
  }

  function bindAdminSeedButton() {
    var button = document.getElementById("admin-demo-seed");
    var status = document.getElementById("demo-seed-status");
    if (!button) return;
    button.addEventListener("click", function () {
      button.disabled = true;
      if (status) status.textContent = "Seeding demo activity…";
      postJson("/api/admin/demo-seed")
        .then(function (result) {
          if (status) {
            status.textContent = "Seeded " + (result.events_seeded || 0) + " events for demo.";
          }
        })
        .catch(function (error) {
          if (status) status.textContent = error.message;
        })
        .finally(function () {
          button.disabled = false;
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindStepNavigation();
    bindAdminSeedButton();
  });
})();
