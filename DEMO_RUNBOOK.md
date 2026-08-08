# SkillOrbit demo runbook

This is the shortest reliable walkthrough for the SmartReco Build Challenge demo.

## Before the demo

1. Run `python scripts/competition_verify.py` (or `--ci` in GitHub Actions).
2. Confirm the app responds at `/health`.
3. Confirm Supabase migrations `001` through `016` have been run.
4. Index catalog vectors: `python scripts/bootstrap_qdrant.py`
5. Optional: seed demo activity — Admin → **Seed demo activity**, or `set DEMO_USER_EMAIL=...` then `python scripts/demo_seed.py --apply`
6. Sign in with a demo learner account and complete onboarding (AI Engineer goal).
7. Open `/explore` in a fresh browser tab (works without login).
8. Bookmark **`/demo`** for guided judge mode.

## 60-second script (judges)

1. **`/demo`** → **Auto-run demo** (admin seeds activity automatically) or manual steps.
2. **`/explore`** → search `production RAG` → show semantic Qdrant hits.
3. **`/dashboard`** → interest radar + live feed → **Generate path**.
4. **`/trace`** → copy Mesh trace ID + retrieval candidate table.
5. **Share path** → open public `/path/{id}` → **Export PDF**.

## Demo video outline (2–3 minutes)

Record in one take using this order for the submission link in README:

1. Landing → `/explore` semantic search (`production RAG`)
2. Sign in → dashboard stats (streak, weekly minutes) → **Generate path** (or press `G`)
3. `/trace` — pipeline stages, Qdrant scores, copy Mesh trace ID
4. Change behavior → **Refresh path** → **What changed** + causality timeline
5. **Share path** `/path/{id}` + optional admin dual-write (add resource → index)

## Three-minute story
### 1. Show intent-aware discovery

Search for:

```text
production RAG
```

Point out semantic Qdrant retrieval—not just keyword matches. Use filters (type, difficulty, career goal).

### 2. Create behavior signals

Open two relevant resources, pause briefly on one, bookmark one, and return to the dashboard.
The activity timeline shows the signals SkillOrbit observed.

### 3. Explain the learner profile

On `/dashboard`, review the **Interest radar** and stats (streak, weekly minutes vs goal).
Press **Sync signals** if needed.

### 4. Generate the grounded path

Press **Generate path** or **Refresh path** under **Next best step**.
Show:

- the one-sentence recommendation summary;
- the next best step;
- real catalog titles and difficulty labels;
- the reason attached to each resource;
- **Share path** and **Export PDF** (public `/path/{id}`, no PII);
- Useful / Not relevant feedback;
- **Email me this path** (Resend);
- link to **Full trace** on `/trace`.

Explain that Qdrant retrieves real resources first, then Mesh writes the explanation
only from those grounded candidates. The LangGraph pipeline stages are:

```text
analyze → retrieve → evaluate → moderate → generate → validate → persist
```

### 5. Show recommendation diff

Change behavior (search a different topic, open new resources), refresh path, and open
**What changed** on the dashboard or `/recommendations`.

### 6. Admin dual-write (optional)

Admin → **Seed demo activity** → add resource → **Index pending** → appears in explore search within seconds.

### 7. Scheduled weekly digest

- First automatic email **7 days** after onboarding, then every 7 days.
- If the user never clicked **Generate path**, the agent **creates the latest path automatically** before sending.
- Works even if the user never returns to the site (needs Render awake or external cron).
- Mention APScheduler (daily check at 09:00 IST) + `/api/cron/weekly-digest` + Resend `hello@plyndrox.app`.
- `email_deliveries.delivery_kind = weekly_digest` prevents duplicate sends.

## Recovery paths

- If no activity appears, browse one resource and wait for the page to become hidden
  or navigate away so events flush (fetch + sendBeacon).
- If Generate says the catalog is unavailable, check Qdrant and Mesh environment
  variables; do not replace the result with mock data.
- If login redirects to the wrong port, set the Supabase Site URL and redirect
  allow-list to the deployed app URL.
- If the recommendation is already present, the five-minute cooldown returns the
  cached grounded result instead of making another Mesh call.
- If email says it is not configured, the core path still works; connect Resend
  and add `RESEND_API_KEY` plus `RESEND_FROM_EMAIL` before retrying.
- If share link fails, confirm `SUPABASE_SERVICE_ROLE_KEY` is set on the server.

## Judge-facing proof points

- The catalog contains 80+ original modules and attributed links, not copied third-party
  course content.
- Behavioral events are authenticated, batched, non-blocking, and protected by RLS.
- Recommendations store trace IDs and retrieval metadata; `/trace` shows the full story.
- LangGraph orchestrates the agent without hardcoded recommendations.
- Anonymous users can browse `/explore` and shared `/path/{id}` cards; personalized features require login.
- `/demo` auto-run completes the core story in under three minutes for judges.
