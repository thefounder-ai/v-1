import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from app.main import app
from app.recommendations import sanitize_recommendation_for_share


class DemoShareTests(unittest.TestCase):
    def test_demo_routes_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/demo", paths)
        self.assertIn("/api/admin/demo-seed", paths)
        self.assertIn("/path/{recommendation_id}", paths)
        self.assertIn("/path/{recommendation_id}/print", paths)

    def test_sanitize_recommendation_strips_pii(self):
        public = sanitize_recommendation_for_share({
            "id": "rec-1",
            "user_id": "user-secret",
            "summary": "Learn RAG.",
            "next_step": "Start with vectors.",
            "items": [{
                "rank": 1,
                "product_id": "prod-1",
                "title": "RAG Guide",
                "category": "AI",
                "difficulty": "intermediate",
                "reason": "Matches your search.",
            }],
            "trace_id": "trace-abc",
            "interest_snapshot": ["RAG", "vectors"],
            "retrieval_query": "production rag for user",
            "retrieval_metadata": {
                "catalog_match_count": 5,
                "top_score": 0.91,
                "mean_score": 0.82,
                "candidates": [{"title": "secret"}],
                "pipeline_timings": {"total_ms": 900},
            },
            "trigger_event_count": 7,
            "created_at": "2026-08-08T10:00:00+00:00",
            "model": "mesh",
        })
        self.assertNotIn("user_id", public)
        self.assertNotIn("interest_snapshot", public)
        self.assertNotIn("retrieval_query", public)
        self.assertNotIn("product_id", public["items"][0])
        self.assertNotIn("candidates", public["retrieval_metadata"])
        self.assertEqual(public["items"][0]["title"], "RAG Guide")
        self.assertEqual(public["trace_id"], "trace-abc")
        self.assertEqual(public["retrieval_metadata"]["top_score"], 0.91)

    def test_path_share_template_renders_without_user(self):
        templates = Environment(
            loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app" / "templates"),
            autoescape=True,
        )
        rendered = templates.get_template("path-share.html").render(
            request=None,
            page_title="Shared path",
            error=None,
            app_public_url="https://example.com",
            mesh_dashboard_url="https://developers.meshapi.ai",
            path={
                "id": "rec-1",
                "summary": "Build production RAG.",
                "next_step": "Index your corpus.",
                "items": [{
                    "rank": 1,
                    "title": "RAG Patterns",
                    "category": "AI",
                    "difficulty": "advanced",
                    "reason": "Grounded pick.",
                }],
                "trace_id": "trace-xyz",
                "trigger_event_count": 4,
                "created_at": "2026-08-08T10:00:00+00:00",
                "model": "mesh",
                "retrieval_metadata": {"catalog_match_count": 3, "top_score": 0.88},
            },
        )
        self.assertIn("Build production RAG", rendered)
        self.assertIn("trace-xyz", rendered)
        self.assertIn("Export PDF", rendered)
        self.assertNotIn("user@", rendered)

    def test_demo_template_lists_steps(self):
        from app.demo_service import DEMO_STEPS

        templates = Environment(
            loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app" / "templates"),
            autoescape=True,
        )
        rendered = templates.get_template("demo.html").render(
            request=None,
            page_title="Demo",
            user={"email": "judge@example.com"},
            profile={"role": "learner"},
            demo_steps=DEMO_STEPS,
            is_admin=False,
            app_public_url="https://example.com",
            mesh_dashboard_url="https://developers.meshapi.ai",
        )
        self.assertIn("Auto-run demo", rendered)
        self.assertIn("Semantic discovery", rendered)
        self.assertGreaterEqual(len(DEMO_STEPS), 5)

    def test_get_recommendation_for_share_uses_service_role(self):
        from app.config import settings
        from app.recommendations import get_recommendation_for_share

        recommendation = {
            "id": "rec-1",
            "summary": "Summary",
            "next_step": "Next",
            "trace_id": "t1",
            "retrieval_metadata": {},
            "trigger_event_count": 1,
            "items": [{"product_id": "p1", "rank": 1, "reason": "why"}],
        }

        async def run() -> dict:
            with patch.object(
                type(settings),
                "supabase_service_role_key",
                new_callable=PropertyMock,
                return_value="service-key",
            ), patch("app.recommendations.recommendation_api_payload", new_callable=AsyncMock) as payload_mock:
                from app.config import settings as app_settings
                payload_mock.return_value = {
                    **recommendation,
                    "items": [{
                        "rank": 1,
                        "title": "Title",
                        "category": "AI",
                        "difficulty": "beginner",
                        "reason": "why",
                    }],
                }
                with patch("httpx.AsyncClient") as client_cls:
                    client = AsyncMock()
                    client_cls.return_value.__aenter__.return_value = client
                    rec_response = MagicMock()
                    rec_response.is_error = False
                    rec_response.json.return_value = [recommendation]
                    items_response = MagicMock()
                    items_response.is_error = False
                    items_response.json.return_value = recommendation["items"]
                    client.get.side_effect = [rec_response, items_response]
                    self.assertEqual(app_settings.supabase_service_role_key, "service-key")
                    return await get_recommendation_for_share("rec-1")

        import asyncio
        result = asyncio.run(run())
        self.assertEqual(result["id"], "rec-1")
        self.assertEqual(result["items"][0]["title"], "Title")


if __name__ == "__main__":
    unittest.main()
