import unittest
from unittest.mock import AsyncMock, patch

from app.path_health import compute_interest_drift, compute_path_health


class PathHealthTests(unittest.TestCase):
    def test_compute_path_health_scores_fresh_recommendation(self):
        health = compute_path_health(
            {
                "retrieval_metadata": {"top_score": 0.88, "mean_score": 0.72},
            },
            {
                "refresh_recommended": False,
                "last_event_at": "2026-08-08T10:00:00+00:00",
            },
            {"path_percent": 40},
        )
        self.assertGreaterEqual(health["score"], 60)
        self.assertEqual(len(health["factors"]), 3)
        self.assertIn(health["label"], {"Excellent", "Healthy", "Fair"})

    def test_compute_path_health_without_recommendation(self):
        health = compute_path_health(None, None, None)
        self.assertEqual(health["score"], 0)
        self.assertEqual(health["label"], "No path")

    def test_compute_path_health_drops_when_refresh_recommended(self):
        fresh = compute_path_health(
            {"retrieval_metadata": {"top_score": 0.8, "mean_score": 0.7}},
            {"refresh_recommended": False, "last_event_at": "2026-08-08T10:00:00+00:00"},
            {"path_percent": 20},
        )
        stale = compute_path_health(
            {"retrieval_metadata": {"top_score": 0.8, "mean_score": 0.7}},
            {"refresh_recommended": True, "last_event_at": "2026-08-08T10:00:00+00:00"},
            {"path_percent": 20},
        )
        self.assertGreater(fresh["score"], stale["score"])


class PathHealthAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_compute_interest_drift_uses_last_recommendation_snapshot(self):
        drift = await compute_interest_drift(
            "token",
            "user-1",
            {"career_goal": "AI Engineer"},
            {"category_weights": {"Backend": 4.5, "Data": 2.0}},
            last_recommendation={
                "retrieval_metadata": {
                    "category_weights_snapshot": {"Backend": 2.0, "DevOps": 1.0},
                }
            },
        )
        self.assertEqual(drift["baseline_label"], "Last path")
        backend = next(row for row in drift["categories"] if row["category"] == "Backend")
        self.assertEqual(backend["delta"], 2.5)

    @patch("app.recommendations.semantic_product_matches", new_callable=AsyncMock)
    @patch("app.recommendations.list_products_by_ids", new_callable=AsyncMock)
    async def test_generic_baseline_path_returns_items(self, mock_products, mock_matches):
        from app.recommendations import generic_baseline_path

        mock_matches.return_value = [
            {"product_id": "p1", "score": 0.91},
            {"product_id": "p2", "score": 0.84},
        ]
        mock_products.return_value = [
            {"id": "p1", "title": "Python Basics", "category": "Backend", "difficulty": "Beginner"},
            {"id": "p2", "title": "SQL Intro", "category": "Data", "difficulty": "Beginner"},
        ]
        baseline = await generic_baseline_path(
            {"career_goal": "Backend Developer", "current_level": "Beginner"},
            limit=2,
        )
        self.assertEqual(len(baseline["items"]), 2)
        self.assertEqual(baseline["items"][0]["title"], "Python Basics")
        self.assertEqual(baseline["source"], "generic_retrieval")


if __name__ == "__main__":
    unittest.main()
