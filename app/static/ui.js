(function () {
  "use strict";

  const AI_STAGES = [
    "Analyzing your learning signals",
    "Retrieving grounded resources from Qdrant",
    "Generating your path with Mesh AI",
    "Validating catalog matches",
  ];

  const TOAST = {
    profileSynced: "Interest profile synced.",
    pathGenerated: "Learning path ready.",
    pathCached: "Showing your latest path.",
    pathRefreshed: "Learning path refreshed.",
    pathAdded: "Added to your learning path.",
    progressSaved: "Progress saved.",
    feedbackSaved: "Feedback saved — thanks.",
    emailSent: "Path emailed to you.",
    bookmarkSaved: "Saved — boosts your interest profile.",
    copied: "Copied to clipboard.",
    copyFailed: "Couldn't copy to clipboard.",
    clipboardUnavailable: "Clipboard not available.",
    searchStarted: "Searching catalog…",
    demoSeeded: "Demo activity seeded.",
    newSignals: function (count) {
      return count + " new signal" + (count === 1 ? "" : "s") + " since last visit.";
    },
  };

  function toastMessage(key, arg) {
    const value = TOAST[key];
    if (typeof value === "function") return value(arg);
    return value || key;
  }

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

  function formatPipelineTimingBadge(metadata, light) {
    const timings = (metadata && metadata.pipeline_timings) || {};
    if (!timings.total_ms) return "";
    const cls = "pipeline-timing-badge" + (light ? " light" : "");
    return (
      '<div class="' + cls + '">' +
      "<span>Retrieve <strong>" + escapeHtml(timings.retrieve_ms || 0) + "ms</strong></span>" +
      "<span>Generate <strong>" + escapeHtml(timings.generate_ms || 0) + "ms</strong></span>" +
      "<span>Total <strong>" + escapeHtml(timings.total_ms || 0) + "ms</strong></span>" +
      "</div>"
    );
  }

  function bindCopyButtons(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-copy-target]").forEach(function (button) {
      if (button.dataset.copyBound === "true") return;
      button.dataset.copyBound = "true";
      button.addEventListener("click", function () {
        const target = document.getElementById(button.dataset.copyTarget);
        const value = button.dataset.copyFull || (target ? target.textContent : "");
        if (!value) return;
        function onSuccess() {
          showToast(toastMessage("copied"), "success");
          button.textContent = "Copied ✓";
          window.setTimeout(function () {
            button.textContent = button.dataset.defaultLabel || "Copy";
          }, 1600);
        }
        if (!button.dataset.defaultLabel) {
          button.dataset.defaultLabel = button.textContent.trim();
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(value).then(onSuccess).catch(function () {
            showToast(toastMessage("copyFailed"), "error");
          });
        } else {
          showToast(toastMessage("clipboardUnavailable"), "error");
        }
      });
    });
  }

  function renderCausalityTimelineHtml(timeline) {
    if (!timeline || !timeline.length) return "";
    const items = timeline.map(function (step) {
      const icon = step.kind === "signal" ? "◎" : "▸";
      const detail = step.detail
        ? "<p>" + escapeHtml(step.detail) + "</p>"
        : "";
      let meta = "";
      if (step.kind === "stage") {
        meta = escapeHtml(step.status || "completed");
        if (step.duration_ms) meta += " · " + escapeHtml(step.duration_ms) + "ms";
      } else if (step.occurred_at) {
        meta = escapeHtml(String(step.occurred_at).slice(0, 16).replace("T", " "));
      }
      return (
        '<li class="causality-step causality-' + escapeHtml(step.kind) + '">' +
        '<span class="causality-icon" aria-hidden="true">' + icon + "</span>" +
        '<div class="causality-body"><strong>' + escapeHtml(step.label) + "</strong>" +
        detail + '<small>' + meta + "</small></div></li>"
      );
    }).join("");
    return '<ol class="causality-timeline">' + items + "</ol>";
  }

  function renderPathIntelligence(data) {
    const target = document.getElementById("path-intelligence-dynamic");
    if (!target) return;
    const intel = data && data.path_intelligence;
    if (!intel || !data.items || !data.items.length) {
      target.innerHTML = "";
      return;
    }
    const health = intel.path_health || {};
    const factors = (health.factors || []).map(function (factor) {
      return (
        '<div class="health-factor"><span>' + escapeHtml(factor.name) + "</span>" +
        "<strong>" + escapeHtml(factor.score) + "/" + escapeHtml(factor.max) + "</strong>" +
        "<small>" + escapeHtml(factor.detail) + "</small></div>"
      );
    }).join("");
    const genericItems = (intel.generic_baseline && intel.generic_baseline.items || []).map(function (item) {
      const meta = [item.category, item.difficulty].filter(Boolean).join(" · ");
      return "<li><strong>" + escapeHtml(item.title) + "</strong>" +
        (meta ? "<small>" + escapeHtml(meta) + "</small>" : "") + "</li>";
    }).join("");
    const personalItems = (intel.personalized_items || data.items || []).map(function (item) {
      const meta = [item.category, item.difficulty].filter(Boolean).join(" · ");
      return "<li><strong>" + escapeHtml(item.title) + "</strong>" +
        (meta ? "<small>" + escapeHtml(meta) + "</small>" : "") + "</li>";
    }).join("");
    const drift = intel.interest_drift || {};
    const maxWeight = drift.max_weight || 1;
    const driftRows = (drift.categories || []).map(function (row) {
      const deltaClass = row.delta > 0 ? "is-up" : row.delta < 0 ? "is-down" : "";
      const deltaPrefix = row.delta > 0 ? "+" : "";
      return (
        '<div class="drift-row"><span class="drift-label">' + escapeHtml(row.category) + "</span>" +
        '<div class="drift-bars">' +
        '<div class="drift-bar drift-bar-previous" style="width:' +
        Math.min(100, (row.previous / maxWeight) * 100) + '%"></div>' +
        '<div class="drift-bar drift-bar-current" style="width:' +
        Math.min(100, (row.current / maxWeight) * 100) + '%"></div>' +
        "</div><span class=\"drift-delta " + deltaClass + "\">" +
        deltaPrefix + escapeHtml(row.delta) + "</span></div>"
      );
    }).join("");
    target.innerHTML =
      '<section class="dash-panel dash-intelligence-panel" id="path-intelligence-panel">' +
      '<div class="dash-panel-head"><div><h2>Path intelligence</h2>' +
      '<p class="panel-sub">Generic baseline vs your grounded path</p></div>' +
      '<div class="health-score-card"><div class="health-score-ring" style="--health-score:' +
      escapeHtml(health.score || 0) + '"><strong>' + escapeHtml(health.score || 0) + "</strong></div>" +
      '<div><span class="health-score-label">' + escapeHtml(health.label || "") +
      '</span><small>Path health</small></div></div></div>' +
      '<div class="health-factor-grid">' + factors + "</div>" +
      '<div class="counterfactual-grid"><article class="counterfactual-card">' +
      '<p class="aside-label">Generic popular path</p>' +
      '<small class="counterfactual-meta">' + escapeHtml((intel.generic_baseline && intel.generic_baseline.source || "").replace(/_/g, " ")) +
      '</small><ol class="counterfactual-list">' + genericItems + "</ol></article>" +
      '<article class="counterfactual-card counterfactual-card-personal">' +
      '<p class="aside-label">Your personalized path</p>' +
      '<small class="counterfactual-meta">' + escapeHtml(intel.overlap_count || 0) + " overlap · " +
      escapeHtml(data.trigger_event_count || 0) + " signals</small>" +
      '<ol class="counterfactual-list">' + personalItems + "</ol></article></div>" +
      (driftRows
        ? '<div class="drift-panel"><p class="aside-label">Interest drift · now vs ' +
          escapeHtml((drift.baseline_label || "baseline").toLowerCase()) + '</p>' +
          '<div class="drift-chart">' + driftRows + "</div>" +
          '<div class="drift-legend"><span><i class="legend-previous"></i> ' +
          escapeHtml(drift.baseline_label || "Baseline") + '</span><span><i class="legend-current"></i> Now</span></div></div>'
        : "") +
      "</section>";
  }

  function renderChangeInsight(data, targetId) {
    const target = document.getElementById(targetId || "change-insight-dynamic");
    if (!target) return;
    if (!data || !data.change_explanation) {
      target.innerHTML = "";
      target.classList.add("hidden");
      return;
    }
    const timeline = data.causality_timeline || (data.retrieval_metadata && data.retrieval_metadata.causality_timeline) || [];
    target.classList.remove("hidden");
    target.innerHTML =
      '<section class="dash-panel dash-diff-panel">' +
      '<div class="dash-panel-head"><h2>Why it changed</h2>' +
      '<a class="text-link" href="/recommendations">Full diff <span>↗</span></a></div>' +
      '<div class="change-explanation-box"><p class="aside-label">Grounded explanation</p>' +
      '<p class="change-explanation-text">' + escapeHtml(data.change_explanation) + "</p></div>" +
      (timeline.length
        ? '<div class="causality-panel"><p class="aside-label">Causality timeline</p>' +
          renderCausalityTimelineHtml(timeline) + "</div>"
        : "") +
      "</section>";
  }

    function renderRecommendationPanel(data, options) {
    const body = document.getElementById("recommendation-panel-body");
    if (!body || !data) return;
    const panel = document.getElementById("recommendation-panel");
    if (panel) panelLoader(panel, false);
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
    const timingBadge = formatPipelineTimingBadge(metadata, true);
    const trace = data.trace_id
      ? '<li class="trace-copy-row">Trace <code id="panel-trace-id">' + escapeHtml(data.trace_id.slice(0, 12)) +
        '</code> <button type="button" class="button-copy" data-copy-target="panel-trace-id" data-copy-full="' +
        escapeHtml(data.trace_id) + '">Copy</button> · <a class="text-link light" href="/trace">Full trace ↗</a></li>'
      : "";

    body.innerHTML =
      '<p class="rec-summary">' + escapeHtml(data.summary) + "</p>" +
      '<div class="rec-next-box"><strong>Do this next</strong><span>' +
      escapeHtml(data.next_step) + "</span></div>" +
      '<ol class="rec-items">' + items + "</ol>" +
      '<div class="evidence-box"><strong>Grounded evidence</strong>' + timingBadge + "<ul>" +
      "<li>" + escapeHtml(data.trigger_event_count || 0) + " behavioral signals at generation time</li>" +
      (interests ? "<li>Interest focus: " + escapeHtml(interests) + "</li>" : "") +
      "<li>" + escapeHtml(metadata.catalog_match_count || 0) + " catalog matches · top score " +
      escapeHtml(metadata.top_score || 0) + "</li>" + trace + "</ul></div>" +
      '<div class="rec-actions">' +
      '<div class="rec-share-actions">' +
      '<a class="button button-small button-light" href="/path/' + escapeHtml(data.id) + '" target="_blank" rel="noreferrer">Share path</a>' +
      '<a class="button button-small button-light" href="/path/' + escapeHtml(data.id) + '/print" target="_blank" rel="noreferrer">Export PDF</a>' +
      "</div>" +
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
        ? '<p class="meta-line light">Updated ' + escapeHtml(data.created_at.slice(0, 16).replace("T", " ")) + "</p>"
        : "") +
      (data.expires_at
        ? '<p class="expiry-countdown light" id="path-expiry-countdown" data-expires-at="' +
          escapeHtml(data.expires_at) + '">Path refreshes in <strong>—</strong></p>'
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
    bindCopyButtons(body);
    renderChangeInsight(data);
    renderPathIntelligence(data);
    if (window.SkillOrbitLive && window.SkillOrbitLive.updateCountdown) {
      window.SkillOrbitLive.updateCountdown();
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
    const timeline = data.causality_timeline || (data.retrieval_metadata && data.retrieval_metadata.causality_timeline) || [];
    const explanation = data.change_explanation
      ? '<div class="change-explanation-box"><p class="aside-label">Why it changed</p>' +
        '<p class="change-explanation-text">' + escapeHtml(data.change_explanation) + "</p></div>"
      : "";
    const timelineBlock = timeline.length
      ? '<div class="causality-panel"><p class="aside-label">Causality timeline</p>' +
        renderCausalityTimelineHtml(timeline) + "</div>"
      : "";
    body.innerHTML =
      explanation +
      '<p class="rec-summary dark">' + escapeHtml(data.summary) + "</p>" +
      '<div class="rec-next-box dark"><strong>Next step</strong><span>' +
      escapeHtml(data.next_step) + "</span></div>" +
      '<ol class="rec-items rec-items-dark">' + items + "</ol>" +
      timelineBlock +
      '<p class="meta-line">Model ' + escapeHtml(data.model || "mesh") +
      " · " + escapeHtml(data.trigger_event_count || 0) + " signals</p>";
    const diffPanel = document.getElementById("change-diff-panel");
    if (diffPanel && data.change_explanation) {
      let explanationBox = diffPanel.querySelector("#change-explanation-box");
      if (!explanationBox) {
        explanationBox = document.createElement("div");
        explanationBox.id = "change-explanation-box";
        explanationBox.className = "change-explanation-box";
        diffPanel.insertBefore(explanationBox, diffPanel.querySelector(".rec-diff-grid"));
      }
      explanationBox.innerHTML =
        '<p class="aside-label">Why it changed</p>' +
        '<p class="change-explanation-text">' + escapeHtml(data.change_explanation) + "</p>";
      let timelinePanel = diffPanel.querySelector("#causality-panel");
      if (timeline.length) {
        if (!timelinePanel) {
          timelinePanel = document.createElement("div");
          timelinePanel.id = "causality-panel";
          timelinePanel.className = "causality-panel";
          diffPanel.appendChild(timelinePanel);
        }
        timelinePanel.innerHTML =
          '<p class="aside-label">Causality timeline</p>' + renderCausalityTimelineHtml(timeline);
      }
    }
  }

  window.SkillOrbitUI = {
    AI_STAGES: AI_STAGES,
    TOAST: TOAST,
    toastMessage: toastMessage,
    escapeHtml: escapeHtml,
    showToast: showToast,
    parseJsonResponse: parseJsonResponse,
    setButtonLoading: setButtonLoading,
    panelLoader: panelLoader,
    startAiLoader: startAiLoader,
    bindFormSubmitLoading: bindFormSubmitLoading,
    formatPipelineTimingBadge: formatPipelineTimingBadge,
    bindCopyButtons: bindCopyButtons,
    renderCausalityTimelineHtml: renderCausalityTimelineHtml,
    renderChangeInsight: renderChangeInsight,
    renderPathIntelligence: renderPathIntelligence,
    renderRecommendationPanel: renderRecommendationPanel,
    renderRecommendationsDetail: renderRecommendationsDetail,
  };
})();
