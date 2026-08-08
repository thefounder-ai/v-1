# Setup Guide

Complete instructions to run SkillOrbit locally from scratch.

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Backend runtime |
| Supabase account | Free tier OK | Auth + PostgreSQL |
| Qdrant Cloud | Free tier OK | Vector search |
| Mesh API key | `rsk_...` | LLM + embeddings (mandatory) |
| Resend (optional) | — | Email delivery |

---

## 1. Clone and install

```bash
git clone https://github.com/thefounder-ai/v-1.git
cd v-1
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. Environment variables

Copy the example file and fill in your keys:

```bash
copy .env.example .env        # Windows
cp .env.example .env          # macOS / Linux
```

### Required variables

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
QDRANT_URL=https://your-cluster.region.aws.cloud.qdrant.io
QDRANT_API_KEY=your-qdrant-key
MESH_API_KEY=rsk_your-mesh-key
APP_PUBLIC_URL=http://localhost:5000
```

### Optional (enable extra features)

```env
SUPABASE_SERVICE_ROLE_KEY=...   # weekly digest, demo seed, share paths
RESEND_API_KEY=re_...           # email delivery
RESEND_FROM_EMAIL=SkillOrbit <hello@yourdomain.com>
CRON_SECRET=long-random-string   # external cron trigger
DEMO_USER_EMAIL=demo@example.com
```

See [`.env.example`](../.env.example) for the full annotated list.

---

## 3. Supabase setup

### 3.1 Create project

1. Go to [supabase.com](https://supabase.com) → New project.
2. Enable **Email** provider under Authentication → Providers.
3. Under Authentication → URL Configuration, set:
   - Site URL: `http://localhost:5000` (or your deployed URL)
   - Redirect URLs: `http://localhost:5000/**`

### 3.2 Run migrations

Open **SQL Editor** and run each file in order:

```
supabase/migrations/001_profiles.sql
supabase/migrations/002_catalog.sql
...
supabase/migrations/016_recommendation_change_explanation.sql
```

All 16 migrations must run successfully. Migrations `010`–`014` seed the catalog (~79 resources).

### 3.3 Create admin user

1. Sign up through the app at `/signup`.
2. In Supabase → Table Editor → `profiles`, set `role = 'admin'` for your user.

---

## 4. Qdrant setup

1. Create a cluster at [cloud.qdrant.io](https://cloud.qdrant.io).
2. Copy cluster URL and API key to `.env`.
3. Collection `skillorbit_products` is created automatically on first index.

### Index the catalog

```bash
python scripts/bootstrap_qdrant.py
```

Expected output: `78` or `79` products indexed. Re-run after adding products via admin.

---

## 5. Mesh API

1. Create an account at [developers.meshapi.ai](https://developers.meshapi.ai).
2. Generate an API key starting with `rsk_`.
3. Add to `.env` as `MESH_API_KEY`.

All LLM and embedding calls go through Mesh — no direct OpenAI keys needed.

Default models (configurable in `.env`):

| Variable | Default |
|---|---|
| `MESH_EMBEDDING_MODEL` | `cohere/embed-english-v3` |
| `MESH_CHAT_MODEL` | `openai/gpt-4.1-mini` |

---

## 6. Start the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 5000
```

Open http://localhost:5000

### Health check

```bash
curl http://localhost:5000/health
```

Expected:

```json
{
  "status": "ok",
  "mesh": "configured",
  "vector": "configured",
  "supabase": "configured"
}
```

---

## 7. First-run checklist

| Step | Action | Verify |
|---|---|---|
| 1 | Sign up at `/signup` | Redirects to onboarding |
| 2 | Complete onboarding | Choose career goal |
| 3 | Browse `/explore` | Semantic search works |
| 4 | Open 2–3 resources | Activity appears on dashboard |
| 5 | Dashboard → Generate path | Recommendation with trace ID |
| 6 | Open `/trace` | Pipeline stages visible |
| 7 | Admin → `/admin/products` | Catalog list loads |

---

## 8. Demo seed (optional)

Pre-populate activity for judge demos:

```bash
set DEMO_USER_EMAIL=your-demo@email.com
python scripts/demo_seed.py --apply
```

Or use Admin → **Seed demo activity** in the UI (requires service role key).

---

## 9. Run tests

```bash
# Full submission verify
python scripts/competition_verify.py --ci

# Unit tests
python -m unittest discover -s tests -v

# Authenticated E2E (server must be running)
python scripts/local_e2e.py
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Login redirects to wrong URL | Fix Supabase Site URL in auth settings |
| Explore shows no results | Run `bootstrap_qdrant.py`; check Qdrant env vars |
| Generate path fails | Verify `MESH_API_KEY`; check `/health` |
| No activity on dashboard | Browse resources while logged in; wait 5s or navigate away |
| Email not sending | Set `RESEND_API_KEY` + verified sender domain |
| Admin pages 403 | Set `profiles.role = 'admin'` in Supabase |

---

Next: [User Guide](./USER_GUIDE.md) · [Deployment](./DEPLOYMENT.md)
