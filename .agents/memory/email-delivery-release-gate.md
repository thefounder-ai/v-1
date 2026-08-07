---
name: Recommendation email release gate
description: Conditions that must be true before SkillOrbit email delivery is called live.
---

Recommendation email is configured and provider-ready: the delivery-log migration is present in Supabase and the Resend provider is securely connected with a verified sender. It should only be called live after one normal signed-in send proves both inbox delivery and the saved delivery row.

**Why:** The app intentionally records pending, sent, and failed delivery states; claiming success without an end-to-end send would hide an operational failure.

**How to apply:** Treat code, template, endpoint, migration, and provider as ready until a real authenticated send returns a provider message ID and the delivery row is readable under the learner's RLS policy.