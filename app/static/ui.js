(function () {
  "use strict";

  const AI_STAGES = [
    "Analyzing your learning signals",
    "Retrieving grounded resources from Qdrant",
    "Generating your path with Mesh AI",
    "Validating catalog matches",
  ];

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toastContainer() {
    let container = document.getElementById("skillorbit-toasts");
    if (!container) {
      container = document.createElement("div");
      container.id = "skillorbit-toasts";
      container.className = "toast-stack";
      container.setAttribute("aria-live", "polite");
      document.body.appendChild(container);
    }
    return container;
  }

  function showToast(message, type) {
    const item = document.createElement("div");
    item.className = "toast toast-" + (type || "info");
    item.textContent = message;
    toastContainer().appendChild(item);
    window.setTimeout(function () {
      item.classList.add("is-leaving");
      window.setTimeout(function () { item.remove(); }, 220);
    }, 3200);
  }

  async function parseJsonResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return { data: null, raw: await response.text() };
    }
    return { data: await response.json(), raw: "" };
  }

  function setButtonLoading(button, loading, loadingLabel) {
    if (!button) return;
    if (loading) {
      if (!button.dataset.defaultLabel) {
        button.dataset.defaultLabel = button.textContent.trim();
      }
      button.classList.add("is-loading");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (loadingLabel) button.textContent = loadingLabel;
    } else {
      button.classList.remove("is-loading");
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (button.dataset.defaultLabel) {
        button.textContent = button.dataset.defaultLabel;
      }
    }
  }

  function panelLoader(panel, show, message) {
    if (!panel) return;
    let overlay = panel.querySelector(".panel-loader");
    if (!overlay && show) {
      overlay = document.createElement("div");
      overlay.className = "panel-loader";
      overlay.innerHTML =
        '<div class="ai-loader" role="status" aria-live="polite">' +
        '<div class="ai-loader-orbit" aria-hidden="true"></div>' +
        '<p class="ai-loader-title">Building your path</p>' +
        '<p class="ai-loader-stage"></p>' +
        '<ul class="ai-loader-steps"></ul>' +
        "</div>";
      panel.classList.add("is-loading-panel");
      panel.appendChild(overlay);
      const steps = overlay.querySelector(".ai-loader-steps");
      AI_STAGES.forEach(function (label, index) {
        const step = document.createElement("li");
        step.textContent = label;
        if (index === 0) step.classList.add("is-active");
        steps.appendChild(step);
      });
    }
    if (!overlay) return null;
    overlay.classList.toggle("is-visible", show);
    panel.classList.toggle("is-loading-panel", show);
    const stage = overlay.querySelector(".ai-loader-stage");
    if (stage && message) stage.textContent = message;
    return overlay;
  }

  function startAiLoader(panel) {
    const overlay = panelLoader(panel, true, AI_STAGES[0]);
    if (!overlay) return function () {};
    const steps = overlay.querySelectorAll(".ai-loader-steps li");
    const stage = overlay.querySelector(".ai-loader-stage");
    let index = 0;
    const timer = window.setInterval(function () {
      index = (index + 1) % AI_STAGES.length;
      steps.forEach(function (step, stepIndex) {
        step.classList.toggle("is-active", stepIndex === index);
        step.classList.toggle("is-done", stepIndex < index);
      });
      if (stage) stage.textContent = AI_STAGES[index];
    }, 1800);
    return function stop() {
      window.clearInterval(timer);
      panelLoader(panel, false);
    };
  }

  function bindFormSubmitLoading(form, buttonSelector) {
    if (!form) return;
    form.addEventListener("submit", function () {
      const button = buttonSelector
        ? form.querySelector(buttonSelector)
        : form.querySelector('[type="submit"]');
      setButtonLoading(button, true, "Searching…");
    });
  }

  function renderRecommendationPanel(data, options) {
    const body = document.getElementById("recommendation-panel-body");
    if (!body || !data) return;
    const resend = options && options.resendConfigured;
    const items = (data.items || []).map(function (item, index) {
      const rank = item.rank || index + 1;
      const meta = [item.category, item.difficulty].filter(Boolean).join(" · ");
      return (
        '<li><a href="/resource/' + escapeHtml(item.product_id) + "?from_rec=" + escapeHtml(data.id) + '">' +
        '<span class="rec-rank">' + rank + "</span>" +
        '<span class="rec-body"><strong>' + escapeHtml(item.title) + "</strong>" +
        (meta ? "<small>" + escapeHtml(meta) + "</small>" : "") +
        "<em>" + escapeHtml(item.reason) + "</em></span>" +
        '<span class="rec-arrow">↗</span></a></li>'
      );
    }).join("");

    const metadata = data.retrieval_metadata || {};
    const interests = (data.interest_snapshot || []).slice(0, 4).join(", ");
    const trace = data.trace_id
      ? '<li>Trace <code>' + escapeHtml(data.trace_id.slice(0, 12)) +
        '</code> · <a class="text-link light" href="/trace">Full trace ↗</a></li>'
      : "";

    body.innerHTML =
      '<p class="rec-summary">' + escapeHtml(data.summary) + "</p>" +
      '<div class="rec-next-box"><strong>Do this next</strong><span>' +
      escapeHtml(data.next_step) + "</span></div>" +
      '<ol class="rec-items">' + items + "</ol>" +
      '<div class="evidence-box"><strong>Why this changed</strong><ul>' +
      "<li>" + escapeHtml(data.trigger_event_count || 0) + " behavioral signals at generation time</li>" +
      (interests ? "<li>Interest focus: " + escapeHtml(interests) + "</li>" : "") +
      "<li>" + escapeHtml(metadata.catalog_match_count || 0) + " catalog matches · top score " +
      escapeHtml(metadata.top_score || 0) + "</li>" + trace + "</ul></div>" +
      '<div class="rec-actions">' +
      '<div class="recommendation-feedback" data-recommendation-id="' + escapeHtml(data.id) + '">' +
      "<span>Was this useful?</span>" +
      '<button type="button" data-feedback="useful">Yes</button>' +
      '<button type="button" data-feedback="not_relevant">Not relevant</button></div>' +
      (resend
        ? '<div class="recommendation-email" data-recommendation-id="' + escapeHtml(data.id) + '">' +
          '<button type="button" id="email-recommendation">Email me this path</button></div>'
        : "") +
      "</div>" +
      (data.created_at
        ? '<p class="meta-line light">Updated ' + escapeHtml(data.created_at.slice(0, 16).replace("T", " ")) +
          (data.expires_at
            ? " · expires " + escapeHtml(data.expires_at.slice(0, 16).replace("T", " "))
            : "") +
          "</p>"
        : "");

    const genBtn = document.getElementById("generate-recommendation");
    if (genBtn) {
      genBtn.textContent = "Refresh path";
      genBtn.dataset.force = "true";
    }
    if (window.SkillOrbitDashboard && window.SkillOrbitDashboard.bindFeedback) {
      window.SkillOrbitDashboard.bindFeedback();
      window.SkillOrbitDashboard.bindEmail();
    }
  }

  function renderRecommendationsDetail(data) {
    const body = document.getElementById("rec-detail-body");
    if (!body || !data) return;
    const items = (data.items || []).map(function (item, index) {
      const rank = item.rank || index + 1;
      return (
        '<li><a href="/resource/' + escapeHtml(item.product_id) + '">' +
        '<span class="rec-rank">' + rank + "</span>" +
        '<span class="rec-body"><strong>' + escapeHtml(item.title) + "</strong>" +
        '<em>' + escapeHtml(item.reason) + "</em></span>" +
        '<span class="rec-arrow">↗</span></a></li>'
      );
    }).join("");
    body.innerHTML =
      '<p class="rec-summary dark">' + escapeHtml(data.summary) + "</p>" +
      '<div class="rec-next-box dark"><strong>Next step</strong><span>' +
      escapeHtml(data.next_step) + "</span></div>" +
      '<ol class="rec-items rec-items-dark">' + items + "</ol>" +
      '<p class="meta-line">Model ' + escapeHtml(data.model || "mesh") +
      " · " + escapeHtml(data.trigger_event_count || 0) + " signals</p>";
  }

  window.SkillOrbitUI = {
    AI_STAGES: AI_STAGES,
    escapeHtml: escapeHtml,
    showToast: showToast,
    parseJsonResponse: parseJsonResponse,
    setButtonLoading: setButtonLoading,
    panelLoader: panelLoader,
    startAiLoader: startAiLoader,
    bindFormSubmitLoading: bindFormSubmitLoading,
    renderRecommendationPanel: renderRecommendationPanel,
    renderRecommendationsDetail: renderRecommendationsDetail,
  };
})();
