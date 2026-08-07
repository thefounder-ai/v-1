---
name: Mesh and Qdrant integration
description: Non-obvious constraints learned while wiring Mesh embeddings and Qdrant semantic retrieval.
---

Mesh is OpenAI-compatible, but a default OpenAI embedding model name is not guaranteed to work through the gateway. Discover available models from Mesh `/v1/models` and choose an embedding-capable model before indexing.

**Why:** The gateway can expose many models without exposing every model under the expected default name, so assuming `text-embedding-3-small` can fail even when the API key and endpoint are valid.

**How to apply:** Treat the embedding model as explicit configuration and smoke-test one embedding before bulk indexing.

Qdrant payload filters require a payload index for the filtered field. Creating a collection and upserting points is not enough for a filtered semantic query.

**Why:** Qdrant can reject a filtered query with “Index required” even when vectors and payloads exist.

**How to apply:** Create the required payload index (for example, boolean `is_active`) during collection setup before running filtered retrieval.