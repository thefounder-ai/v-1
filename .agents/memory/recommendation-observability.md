---
name: Recommendation observability
description: Architecture decision for tracing SkillOrbit recommendation runs.
---

Use an explicit lightweight recommendation state object instead of adding a framework dependency to the core request path. The stable stages are analyze, retrieve, evaluate, generate, validate, and persist.

**Why:** The hackathon needs an explainable agent flow and debuggable failures, but a new orchestration dependency would increase setup and deployment risk without improving the current product.

**How to apply:** Keep stage metadata bounded and secret-free. Persist trace and retrieval quality metadata with recommendation history, and emit request-scoped JSON logs for server diagnosis.