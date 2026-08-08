import unittest

from app.interest import summarize_feedback_signals, apply_recommendation_feedback
from app.recommendations import (
    RETRIEVAL_POOL_SIZE,
    RETRIEVAL_FINAL_SIZE,
    _evaluate_candidates,
    moderate_retrieval_query,
    moderation_error_message,
)


class ModerationTests(unittest.TestCase):
    def test_allows_learning_query(self):
        allowed, reason = moderate_retrieval_query(
            "Career goal: AI Engineer\nInterested categories: Backend, Python"
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "allowed")

    def test_blocks_empty_query(self):
        allowed, reason = moderate_retrieval_query("short")
        self.assertFalse(allowed)
        self.assertEqual(reason, "empty_or_short")

    def test_blocks_off_topic_query(self):
        allowed, reason = moderate_retrieval_query("buy lottery tickets and pizza only")
        self.assertFalse(allowed)
        self.assertEqual(reason, "off_topic")

    def test_blocks_toxic_query(self):
        allowed, reason = moderate_retrieval_query(
            "career goal hate speech and random noise for testing"
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "toxic_content")

    def test_moderation_error_message_is_safe(self):
        message = moderation_error_message("off_topic")
        self.assertIn("learning", message.lower())


class FeedbackSignalTests(unittest.TestCase):
    def test_summarize_feedback_signals_counts_categories(self):
        events = [
            {
                "event_type": "recommendation_feedback",
                "metadata": {
                    "feedback": "not_relevant",
                    "categories": ["Backend", "Data"],
                },
            },
            {
                "event_type": "recommendation_feedback",
                "metadata": {
                    "feedback": "not_relevant",
                    "categories": ["Backend"],
                },
            },
            {
                "event_type": "recommendation_feedback",
                "metadata": {
                    "feedback": "useful",
                    "categories": ["AI"],
                },
            },
        ]
        penalties, boosts = summarize_feedback_signals(events)
        self.assertEqual(penalties["Backend"], 2)
        self.assertEqual(penalties["Data"], 1)
        self.assertEqual(boosts["AI"], 1)


class RerankTests(unittest.TestCase):
    def test_retrieval_pool_is_larger_than_final_selection(self):
        self.assertGreater(RETRIEVAL_POOL_SIZE, RETRIEVAL_FINAL_SIZE)

    def test_evaluate_deprioritizes_category_after_three_not_relevant(self):
        candidates = [
            {"id": "a", "title": "A", "category": "Backend", "career_goals": [], "difficulty": "Beginner"},
            {"id": "b", "title": "B", "category": "Data", "career_goals": [], "difficulty": "Beginner"},
        ]
        matches = [
            {"product_id": "a", "score": 0.92},
            {"product_id": "b", "score": 0.9},
        ]
        neutral = _evaluate_candidates(candidates, matches, None, {}, limit=1)
        penalized = _evaluate_candidates(
            candidates,
            matches,
            None,
            {"feedback_penalties": {"Backend": 3}},
            limit=1,
        )
        self.assertEqual(neutral[0]["id"], "a")
        self.assertEqual(penalized[0]["id"], "b")


class FeedbackLoopAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_recommendation_feedback_updates_weights(self):
        captured: dict = {}

        async def fake_get_profile(access_token, user_id):
            return {
                "user_id": user_id,
                "interest_snapshot": [],
                "category_weights": {"Backend": 4.0},
                "skill_weights": {},
                "search_terms": [],
                "signal_summary": "",
                "event_count": 0,
                "meaningful_event_count": 0,
                "refresh_recommended": False,
                "profile_version": 2,
            }

        async def fake_events(access_token, user_id, limit=120):
            return []

        async def fake_request(access_token, method, path, **kwargs):
            captured["json"] = kwargs.get("json")
            return [captured["json"]]

        with unittest.mock.patch("app.interest.get_interest_profile", fake_get_profile), unittest.mock.patch(
            "app.interest.recent_profile_events", fake_events
        ), unittest.mock.patch("app.interest._request", fake_request):
            result = await apply_recommendation_feedback(
                "token",
                "user-1",
                ["Backend"],
                "not_relevant",
            )

        self.assertEqual(result["penalties"]["Backend"], 1)
        self.assertEqual(captured["json"]["category_weights"]["Backend"], 3.0)
        self.assertTrue(captured["json"]["refresh_recommended"])


if __name__ == "__main__":
    unittest.main()
