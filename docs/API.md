# API Reference

SkillOrbit REST API endpoints. Interactive OpenAPI docs available at `/docs` when running locally.

**Base URL:** `https://v-1-ora9.onrender.com` (production) or `http://localhost:5000` (local)

**Auth:** Session cookies set on login. API routes require authenticated session unless noted.

---

## System

### `GET /health`

Public health check. No auth required.

**Response:**

```json
{
  "status": "ok",
  "app": "SkillOrbit",
  "supabase": "configured",
  "vector": "configured",
  "mesh": "configured",
  "digest": "configured"
}
```

### `GET /api/cron/weekly-digest`

Trigger weekly digest batch. Requires `?secret=CRON_SECRET`.

**Response:**

```json
{
  "checked": 12,
  "sent": 2,
  "skipped": 10,
  "failed": 0
}
```

---

## Authentication

| Method | Path | Description |
|---|---|---|
| `GET` | `/login` | Login page |
| `GET` | `/signup` | Signup page |
| `POST` | `/auth/login` | Sign in (form: email, password) |
| `POST` | `/auth/signup` | Register (form: email, password) |
| `POST` | `/auth/logout` | Clear session |
| `GET` | `/onboarding` | Onboarding form |
| `POST` | `/onboarding` | Save career goal + weekly minutes |

---

## Behavioral events

### `POST /api/events`

Ingest a batch of behavioral events. Called by `tracking.js` — not intended for manual use.

**Request body:**

```json
{
  "events": [
    {
      "event_id": "uuid",
      "event_type": "resource_view",
      "resource_id": "uuid",
      "occurred_at": "2026-08-08T10:00:00Z",
      "duration_seconds": 45,
      "metadata": {}
    }
  ]
}
```

**Event types:**

| Type | Description |
|---|---|
| `page_view` | Any authenticated page load |
| `catalog_search` | Search query on explore |
| `filter_applied` | Filter change on explore |
| `resource_view` | Open `/resource/{id}` |
| `resource_click` | Click on resource card |
| `resource_dwell` | Time spent on resource page |
| `bookmark_added` | Save to library |
| `recommendation_opened` | Click from recommendation |
| `recommendation_feedback` | Useful / Not relevant |
| `learning_goal_updated` | Onboarding or goal change |

**Limits:** 1–50 events per batch. Metadata max 12 fields, 4000 bytes.

**Response:**

```json
{
  "stored": 3,
  "auto_generate_recommended": true
}
```

### `GET /api/events/stream`

Server-sent events stream for live activity panel. Auth required.

### `POST /api/interest-profile/refresh`

Manually refresh interest profile from recent events. Auth required.

---

## Recommendations

### `POST /api/recommendations/generate`

Run the LangGraph recommendation pipeline.

**Response:**

```json
{
  "id": "uuid",
  "summary": "You are building strong Data/ML Foundations...",
  "next_step": "Start with the Data/ML Foundations Path...",
  "items": [
    {
      "product_id": "uuid",
      "title": "Data/ML Foundations Path",
      "reason": "Covers essential data skills...",
      "difficulty": "Beginner"
    }
  ],
  "trace_id": "mesh-trace-id",
  "retrieval_metadata": {
    "top_score": 0.61,
    "match_count": 20,
    "mean_score": 0.49
  },
  "change_explanation": "Shifted toward ML evaluation..."
}
```

**Trigger policy:** Returns cached result if within 5-minute cooldown and recommendation is fresh (< 24h).

### `POST /api/recommendations/{id}/feedback`

Submit feedback on a recommendation.

**Form fields:** `feedback` = `useful` | `not_relevant`

### `POST /api/recommendations/{id}/email`

Send recommendation to user's email via Resend. Requires `RESEND_API_KEY`.

---

## Progress

### `POST /api/progress/{product_id}`

Mark a resource as complete or in-progress.

**Form fields:** `status` = `completed` | `in_progress` | `not_started`

---

## Admin

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/products` | Catalog list |
| `GET` | `/admin/products/new` | Add product form |
| `POST` | `/admin/products/new` | Create product (+ Qdrant index) |
| `GET` | `/admin/products/{id}/edit` | Edit form |
| `POST` | `/admin/products/{id}/edit` | Update product (+ re-index) |
| `POST` | `/admin/products/{id}/deactivate` | Soft-delete |
| `POST` | `/admin/products/{id}/reactivate` | Re-activate |
| `POST` | `/admin/products/index` | Batch-index pending vectors |
| `GET` | `/admin/sync-health` | SQL ↔ Qdrant sync status |
| `POST` | `/api/admin/demo-seed` | Seed demo activity |

Admin routes require `profiles.role = 'admin'`.

---

## Public pages (HTML)

| Path | Auth | Description |
|---|---|---|
| `/` | No | Landing page |
| `/explore` | No | Semantic catalog browse |
| `/resource/{id}` | No | Resource detail |
| `/path/{id}` | No | Public shared recommendation |
| `/path/{id}/print` | No | PDF-friendly export |
| `/dashboard` | Yes | Learner dashboard |
| `/trace` | Yes | Pipeline observability |
| `/demo` | Yes (admin for auto-run) | Judge demo mode |
| `/bookmarks` | Yes | Saved library |
| `/learning-path` | Yes | Structured path |
| `/recommendations` | Yes | Recommendation history |

---

## Database tables (Supabase)

| Table | Purpose |
|---|---|
| `profiles` | User profile, career goal, role |
| `products` | Catalog (title, description, category, price) |
| `activity_events` | Behavioral event log |
| `interest_profiles` | Aggregated learner signals |
| `recommendations` | Stored AI paths + trace metadata |
| `recommendation_items` | Individual resources in a path |
| `user_progress` | Completion tracking per resource |
| `email_deliveries` | Email send log (manual + weekly digest) |

Full schema: see [Architecture — Database](../ARCHITECTURE.md#11-database-schema).

---

## External services

| Service | Used for | SDK / client |
|---|---|---|
| Mesh API | LLM chat + embeddings | `openai` SDK (`base_url=mesh`) |
| Qdrant | Vector search + RAG | `qdrant-client` |
| Supabase | Auth + PostgreSQL | `httpx` REST |
| Resend | Email delivery | `httpx` REST |

---

Next: [Deployment](./DEPLOYMENT.md) · [Setup](./SETUP.md)
