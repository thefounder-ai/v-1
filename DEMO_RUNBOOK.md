# SkillOrbit demo runbook

This is the shortest reliable walkthrough for the SmartReco Build Challenge demo.

## Before the demo

1. Confirm the app responds at `/health`.
2. Confirm Supabase migrations `001` through `014` have been run.
3. Index catalog vectors: `python scripts/bootstrap_qdrant.py`
4. Optional: seed demo activity — `set DEMO_USER_EMAIL=...` then `python scripts/demo_seed.py --apply`
5. Sign in with a demo learner account and complete onboarding (AI Engineer goal).
6. Open `/explore` in a fresh browser tab (works without login).

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
- Useful / Not relevant feedback;
- **Email me this path** (Resend);
- link to **Full trace** on `/trace`.

Explain that Qdrant retrieves real resources first, then Mesh writes the explanation
only from those grounded candidates. The LangGraph pipeline stages are:

```text
analyze → retrieve → evaluate → generate → validate → persist
```

### 5. Show recommendation diff

Change behavior (search a different topic, open new resources), refresh path, and open
**What changed** on the dashboard or `/recommendations`.

### 6. Admin dual-write (optional)

Admin → add resource → **Index pending** → appears in explore search within seconds.

### 7. Scheduled digest

Mention APScheduler daily job (08:00 UTC) + Resend with `email_deliveries` duplicate protection.
Requires `SUPABASE_SERVICE_ROLE_KEY` on the server.

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

## Judge-facing proof points

- The catalog contains 80+ original modules and attributed links, not copied third-party
  course content.
- Behavioral events are authenticated, batched, non-blocking, and protected by RLS.
- Recommendations store trace IDs and retrieval metadata; `/trace` shows the full story.
- LangGraph orchestrates the agent without hardcoded recommendations.
- Anonymous users can browse `/explore`; only personalized features require login.
