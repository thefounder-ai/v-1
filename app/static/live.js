(function () {
  "use strict";

  if (!document.body.classList.contains("page-dashboard")) return;

  const ui = window.SkillOrbitUI;
  const signalCountEl = document.getElementById("meaningful-signal-count");
  const feedEl = document.getElementById("live-activity-feed");
  const countdownEl = document.getElementById("path-expiry-countdown");
  const seenFeedIds = new Set();
  if (feedEl) {
    feedEl.querySelectorAll("[data-event-id]").forEach(function (node) {
      seenFeedIds.add(node.dataset.eventId);
    });
  }
  let visitToastShown = false;

  function timelineIcon(eventType) {
    if (eventType === "resource_view") return "↗";
    if (eventType === "catalog_search") return "⌕";
    if (eventType === "bookmark_added") return "★";
    if (eventType === "recommendation_opened") return "◎";
    if (eventType === "learning_goal_updated") return "◆";
    return "•";
  }

  function prependLiveEvent(row) {
    if (!feedEl || !row || !row.event_id || seenFeedIds.has(row.event_id)) return;
    seenFeedIds.add(row.event_id);
    const item = document.createElement("li");
    item.className = "live-signal-item";
    item.innerHTML =
      '<span class="timeline-icon">' + timelineIcon(row.event_type) + "</span>" +
      "<div><strong>" + escapeHtml(row.label) + "</strong>" +
      "<small>" + escapeHtml(row.detail || "") + "</small></div>";
    feedEl.prepend(item);
    while (feedEl.children.length > 12) {
      feedEl.removeChild(feedEl.lastElementChild);
    }
  }

  function escapeHtml(value) {
    if (window.SkillOrbitUI && window.SkillOrbitUI.escapeHtml) {
      return window.SkillOrbitUI.escapeHtml(value);
    }
    return String(value || "");
  }

  function updateCountdown() {
    if (!countdownEl) return;
    const expiresAt = countdownEl.dataset.expiresAt;
    if (!expiresAt) return;
    const expiry = new Date(expiresAt);
    const now = new Date();
    const diffMs = expiry.getTime() - now.getTime();
    const strong = countdownEl.querySelector("strong");
    if (!strong) return;
    if (diffMs <= 0) {
      strong.textContent = "expired — refresh recommended";
      countdownEl.classList.add("is-expired");
      return;
    }
    const hours = Math.floor(diffMs / 3600000);
    const minutes = Math.floor((diffMs % 3600000) / 60000);
    strong.textContent = hours > 0 ? hours + "h " + minutes + "m" : minutes + "m";
    countdownEl.classList.remove("is-expired");
  }

  const lastVisit = localStorage.getItem("skillorbit_last_visit_at");
  localStorage.setItem("skillorbit_last_visit_at", new Date().toISOString());
  const since = lastVisit || new Date(Date.now() - 3600000).toISOString();
  const source = new EventSource("/api/events/stream?since=" + encodeURIComponent(since));

  source.addEventListener("signal", function (event) {
    try {
      prependLiveEvent(JSON.parse(event.data));
    } catch (error) {
      return;
    }
  });

  source.addEventListener("stats", function (event) {
    try {
      const data = JSON.parse(event.data);
      if (signalCountEl && typeof data.meaningful_event_count === "number") {
        signalCountEl.textContent = String(data.meaningful_event_count);
      }
      if (data.refresh_recommended) {
        window.dispatchEvent(new CustomEvent("skillorbit:refresh-recommended", { detail: data }));
      }
    } catch (error) {
      return;
    }
  });

  source.addEventListener("visit", function (event) {
    if (visitToastShown || !ui) return;
    try {
      const data = JSON.parse(event.data);
      const count = data.new_since_visit || 0;
      if (count > 0) {
        ui.showToast(ui.toastMessage("newSignals", count), "info");
        visitToastShown = true;
      }
    } catch (error) {
      return;
    }
  });

  source.addEventListener("ingest", function () {
    if (signalCountEl) {
      const current = parseInt(signalCountEl.textContent || "0", 10) || 0;
      signalCountEl.textContent = String(current + 1);
    }
  });

  updateCountdown();
  window.setInterval(updateCountdown, 60000);
  window.SkillOrbitLive = { updateCountdown: updateCountdown };
  window.addEventListener("pagehide", function () {
    source.close();
  });
})();
