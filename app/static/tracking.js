(function () {
  "use strict";

  const body = document.body;
  if (body.dataset.authenticated !== "true") return;

  const queue = [];
  const batchSize = 8;
  const flushInterval = 5000;
  let flushTimer = null;
  const resourceMatch = window.location.pathname.match(/^\/resource\/([0-9a-f-]{36})$/i);
  const resourceId = resourceMatch ? resourceMatch[1] : null;
  const startedAt = Date.now();
  const params = new URLSearchParams(window.location.search);
  const fromRecommendation = params.get("from_rec");

  function uuid() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (char) {
      const random = (Math.random() * 16) | 0;
      const value = char === "x" ? random : (random & 3) | 8;
      return value.toString(16);
    });
  }

  function enqueue(eventType, details) {
    queue.push(Object.assign({
      event_id: uuid(),
      event_type: eventType,
      occurred_at: new Date().toISOString()
    }, details || {}));
    if (queue.length >= batchSize) flush();
    else scheduleFlush();
  }

  function scheduleFlush() {
    if (!flushTimer) flushTimer = window.setTimeout(flush, flushInterval);
  }

  function maybePromptRefresh(data) {
    if (!data || !data.auto_generate_recommended) return;
    window.dispatchEvent(new CustomEvent("skillorbit:refresh-recommended", { detail: data }));
  }

  function flush() {
    if (flushTimer) {
      window.clearTimeout(flushTimer);
      flushTimer = null;
    }
    if (!queue.length) return;
    const events = queue.splice(0, queue.length);
    const bodyData = JSON.stringify({ events: events });

    function handleResponse(responsePromise) {
      responsePromise
        .then(function (res) {
          if (!res.ok) return null;
          return res.json();
        })
        .then(maybePromptRefresh)
        .catch(function () {});
    }

  if (navigator.sendBeacon) {
      const blob = new Blob([bodyData], { type: "application/json" });
      navigator.sendBeacon("/api/events", blob);
    }

    handleResponse(
      window.fetch("/api/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: bodyData,
        keepalive: true,
        credentials: "same-origin"
      })
    );
  }

  window.skillOrbitTrack = enqueue;

  enqueue("page_view", {
    metadata: { path: window.location.pathname }
  });

  const search = params.get("search");
  if (search) enqueue("catalog_search", { search_query: search.slice(0, 200) });
  if (params.get("category") || params.get("difficulty") || params.get("career_goal")) {
    enqueue("filter_applied", {
      metadata: {
        category: (params.get("category") || "").slice(0, 80),
        difficulty: (params.get("difficulty") || "").slice(0, 40),
        career_goal: (params.get("career_goal") || "").slice(0, 80)
      }
    });
  }

  if (resourceId) {
    enqueue("resource_view", { resource_id: resourceId });
    if (fromRecommendation) {
      enqueue("recommendation_opened", {
        resource_id: resourceId,
        metadata: { recommendation_id: fromRecommendation.slice(0, 36) }
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        enqueue("resource_dwell", {
          resource_id: resourceId,
          duration_seconds: Math.min(86400, Math.max(0, Math.round((Date.now() - startedAt) / 1000)))
        });
        flush();
      }
    });
  }

  document.addEventListener("click", function (event) {
    const link = event.target.closest("a[href^='/resource/']");
    if (!link) return;
    const match = link.getAttribute("href").match(/^\/resource\/([0-9a-f-]{36})/i);
    if (match) enqueue("resource_click", { resource_id: match[1] });
  });

  window.addEventListener("pagehide", flush);
})();
