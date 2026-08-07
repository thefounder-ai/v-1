---
name: SkillOrbit product direction
description: Durable product and architecture decisions for the SmartReco hackathon project.
---

SkillOrbit is intentionally a focused learning navigator, not a full LMS. Its differentiator is an explainable next-step recommendation that changes when the learner's real browsing intent changes.

**Why:** The challenge rewards grounded behavioral recommendations, efficient AI use, and a convincing end-to-end demo more than a large collection of shallow LMS features.

**How to apply:** Keep the MVP centered on catalog metadata, event tracking, semantic retrieval, recommendation evidence, and admin dual-write. Treat video hosting, payments, certificates, and heavy animation as out of scope unless the core loop is already reliable.

The catalog should combine original SkillOrbit learning modules with official/open resources represented by metadata and links, never copied full third-party content.

**Why:** This keeps the catalog useful while avoiding copyright risk and makes the resource source transparent to users and judges.

**How to apply:** Store source URL, provider, resource type, and license/attribution metadata for external items; embed summaries and metadata, not copied course bodies.

The planned production stack is FastAPI/Jinja2/Vanilla JavaScript on Render, Supabase Auth and PostgreSQL, Qdrant Cloud, Mesh API for every AI/embedding call, and Resend for optional email.

**Why:** This satisfies the Python and Mesh requirements while keeping relational data, auth, vector retrieval, hosting, and email responsibilities clear.

**How to apply:** If a technology changes, update `PLAN_DOT_TECH.md` and the README before implementation so a future agent can follow the new contract.