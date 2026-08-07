---
name: GitHub push history
description: Safe GitHub publishing constraints for this repository.
---

GitHub workflow files require a token with workflow write permission; a contents-only token can push application files but will reject any push that creates or updates `.github/workflows/*`.

**Why:** GitHub rejected a normal push at the final ref update when the token lacked the workflow scope, so the application snapshot had to be published separately and the full local history restored without the optional CI workflow file.

**How to apply:** Preserve the local phase-by-phase history when publishing. Verify remote commit count and tree after push, and never use force push unless the remote ref is the known temporary snapshot created during the same operation.