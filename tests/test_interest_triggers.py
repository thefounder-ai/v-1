import unittest
from datetime import datetime, timezone, timedelta

from app.interest import build_interest_profile
from app.triggers import (
    is_recommendation_fresh,
    recommendation_expires_at,
    should_auto_generate,
    within_cooldown,
)


class InterestProfileTests(unittest.TestCase):
    def test_meaningful_events_do_not_fire_on_page_view_only(self):
        events = [{"event_type": "page_view", "metadata": {"path": "/explore"}}]
        profile = build_interest_profile(events, {}, {"career_goal": "AI Engineer"})
        self.assertEqual(profile["meaningful_event_count"], 0)
        self.assertFalse(profile["refresh_recommended"])

    def test_search_and_dwell_trigger_refresh(self):
        events = [
            {"event_type": "catalog_search", "search_query": "RAG production"},
            {"event_type": "resource_view", "resource_id": "a"},
            {"event_type": "resource_dwell", "resource_id": "a", "duration_seconds": 150},
        ]
        products = {
            "a": {"category": "Generative AI", "skills": ["RAG"], "career_goals": []},
        }
        profile = build_interest_profile(events, products, {"career_goal": "AI Engineer"})
        self.assertGreaterEqual(profile["meaningful_event_count"], 3)
        self.assertTrue(profile["refresh_recommended"])
        self.assertIn("Generative AI", profile["interest_snapshot"])


class TriggerPolicyTests(unittest.TestCase):
    def test_expires_at_is_in_the_future(self):
        expiry = datetime.fromisoformat(recommendation_expires_at().replace("Z", "+00:00"))
        self.assertGreater(expiry, datetime.now(timezone.utc))

    def test_stale_recommendation_is_not_fresh(self):
        expired = {
            "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        self.assertFalse(is_recommendation_fresh(expired))

    def test_auto_generate_when_profile_ready_and_no_fresh_rec(self):
        profile = {"refresh_recommended": True}
        self.assertTrue(should_auto_generate(profile, None))

    def test_cooldown_blocks_regeneration(self):
        now = datetime.now(timezone.utc).isoformat()
        latest = {"created_at": now, "expires_at": recommendation_expires_at()}
        self.assertTrue(within_cooldown(latest))


if __name__ == "__main__":
    unittest.main()
