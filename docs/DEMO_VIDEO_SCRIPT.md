# Demo Video Script (2–3 minutes)

Use this script while screen recording. Target length: **2 min 30 sec**.

**Before you start:**
- Close extra tabs and notifications
- Use Chrome at 1920×1080 or 1440×900
- Zoom browser to 100%
- Have admin account ready (or use `/demo` auto-run)
- Optional: run `python scripts/demo_seed.py --apply` first

**Recording tools:** OBS, Loom, Windows Game Bar (`Win + G`), or ShareX

---

## Script

### INTRO (0:00 – 0:15) — Landing page

**Show:** https://v-1-ora9.onrender.com

**Say:**
> "This is SkillOrbit — a behavioral AI recommendation engine for career learning. It watches how you browse, retrieves real courses from Qdrant, and generates personalized paths through Mesh API. Nothing is hallucinated."

**Do:**
- Scroll briefly to **Product preview** gallery — click Dashboard tab
- Point at **How it works** flow (5 steps)

---

### EXPLORE (0:15 – 0:35) — No login needed

**Show:** `/explore`

**Say:**
> "Anyone can browse 79 resources with semantic search — powered by Qdrant and Mesh embeddings, not keyword matching."

**Do:**
1. Search: `production RAG`
2. Show results appearing
3. Click one resource card
4. Point at **YOUR ACTIVITY** panel on the right (if logged in) OR mention tracking

---

### SIGN IN + DASHBOARD (0:35 – 1:10)

**Show:** Login → Dashboard

**Say:**
> "After sign-up and onboarding, SkillOrbit builds an interest profile from batched, non-blocking events — searches, dwell time, bookmarks."

**Do:**
1. Go to `/dashboard` (or sign in first)
2. Point at stats: **Day streak**, **Weekly minutes**, **Signals**
3. Point at **Path intelligence** score ring
4. Click **Generate path** (or **Refresh path** if already exists)
5. Wait 10–15 seconds for AI response
6. Read the **summary** and **Next best step** aloud briefly
7. Scroll through grounded resource list

---

### TRACE (1:10 – 1:40) — Judge favorite

**Show:** `/trace`

**Say:**
> "Every recommendation runs through a 7-stage LangGraph pipeline — analyze, retrieve, evaluate, moderate, generate, validate, persist. Full observability for judges."

**Do:**
1. Show pipeline stages with timings
2. Scroll to **Qdrant candidates** table
3. Point at scores: Selected vs Rejected
4. Copy **Mesh trace ID** (mention Mesh dashboard)

---

### BEHAVIOR CHANGE (1:40 – 2:00) — Optional but strong

**Show:** Dashboard again

**Say:**
> "When behavior changes, the path updates — with a clear diff explaining what changed."

**Do:**
1. Go `/explore`, search different topic (e.g. `async python`)
2. Open 1–2 resources
3. Back to `/dashboard` → **Refresh path**
4. Show **What changed** section

---

### ADMIN + DUAL-WRITE (2:00 – 2:20) — If admin account

**Show:** `/admin/sync-health` or `/admin/products`

**Say:**
> "Admin CRUD dual-writes to Supabase and Qdrant. Sync health shows zero drift — 79 resources, all synced."

**Do:**
- Show sync health: Total 79, Synced 79, Pending 0
- OR show admin catalog list

---

### OUTRO (2:20 – 2:30)

**Show:** Landing page or `/demo`

**Say:**
> "SkillOrbit — built for SmartReco 2026. Behavioral tracking, LangGraph agent, grounded RAG, weekly email digest, and full trace observability. GitHub and docs in the README."

**Do:**
- Show `/demo` page briefly OR landing **Judge demo** button
- End recording

---

## Quick 60-second cut (if time limit)

| Time | Action |
|---|---|
| 0:00 | Landing → Product preview |
| 0:10 | `/explore` → search `production RAG` |
| 0:25 | `/dashboard` → Generate path |
| 0:45 | `/trace` → pipeline + Qdrant scores |
| 0:55 | Share path `/path/{id}` |

---

## If something breaks during recording

| Problem | Fix on camera |
|---|---|
| Cold start slow | Say "Render free tier waking up" — wait 30s |
| Generate fails | Use pre-seeded account; Admin → Seed demo activity |
| Empty explore | Mention Qdrant semantic search architecture |
| Already have path | Use **Refresh path** instead of Generate |

---

## After recording

1. Upload to YouTube or Loom (unlisted or public)
2. Add link to **hackathon dashboard** submission form
3. Optional: add link in README under Live table

---

## Checklist before upload

```
[ ] Audio clear (or add captions)
[ ] No passwords visible on screen
[ ] Generate path shown successfully
[ ] /trace with Qdrant scores visible
[ ] Under 3 minutes
[ ] Link added to hackathon dashboard
```
