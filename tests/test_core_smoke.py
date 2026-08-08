import unittest

from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from app.agent_graph import RecommendationGraphState
from app.auth import normalize_auth_session, post_auth_destination, SESSION_COOKIE, REFRESH_COOKIE
from app.catalog import CAREER_GOALS
from app.email_delivery import recommendation_email_html
from app.main import app


class CoreSmokeTests(unittest.TestCase):
    def test_recommendation_graph_has_explicit_stages(self):
        state = RecommendationGraphState(trace_id="smoke")
        for name in ("analyze", "retrieve", "evaluate", "generate", "validate", "persist"):
            started = state.start_stage(name)
            state.finish_stage(name, started)
        state.complete()
        self.assertEqual(state.status, "completed")
        self.assertEqual(
            [stage["name"] for stage in state.stages],
            ["analyze", "retrieve", "evaluate", "generate", "validate", "persist"],
        )

    def test_email_html_escapes_user_content(self):
        rendered = recommendation_email_html(
            {
                "summary": "Build <safe> systems",
                "next_step": "Practice & ship",
                "items": [{"title": "A < B", "reason": "Use > 1 example"}],
            },
            "learner&one",
        )
        self.assertIn("Build &lt;safe&gt; systems", rendered)
        self.assertIn("learner&amp;one", rendered)
        self.assertNotIn("A < B", rendered)

    def test_email_route_is_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/api/recommendations/{recommendation_id}/email", paths)

    def test_new_pages_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/learning-path", paths)
        self.assertIn("/recommendations", paths)
        self.assertIn("/admin/sync-health", paths)

    def test_post_auth_destination(self):
        self.assertEqual(post_auth_destination(None), "/onboarding")
        self.assertEqual(post_auth_destination({"onboarding_complete": False}), "/onboarding")
        self.assertEqual(post_auth_destination({"onboarding_complete": True}), "/dashboard")

    def test_career_goals_count(self):
        self.assertEqual(len(CAREER_GOALS), 5)

    def test_auth_cookie_names(self):
        self.assertEqual(SESSION_COOKIE, "skillorbit_session")
        self.assertEqual(REFRESH_COOKIE, "skillorbit_refresh")

    def test_normalize_auth_session_flattens_nested_session(self):
        body = {
            "user": {"id": "abc"},
            "session": {
                "access_token": "token",
                "refresh_token": "refresh",
            },
        }
        normalized = normalize_auth_session(body)
        self.assertEqual(normalized["access_token"], "token")
        self.assertEqual(normalized["refresh_token"], "refresh")
        self.assertEqual(normalized["user"]["id"], "abc")

    def test_trace_route_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/trace", paths)

    def test_weekly_digest_cron_route_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/api/cron/weekly-digest", paths)

    def test_weekly_digest_due_after_seven_days(self):
        from datetime import datetime, timedelta, timezone
        from app.digest import weekly_digest_due

        now = datetime(2026, 8, 8, tzinfo=timezone.utc)
        anchor = now - timedelta(days=8)
        self.assertTrue(
            weekly_digest_due(
                now=now,
                account_anchor=anchor,
                last_digest_sent=None,
                interval_days=7,
            )
        )
        self.assertFalse(
            weekly_digest_due(
                now=now,
                account_anchor=now - timedelta(days=3),
                last_digest_sent=None,
                interval_days=7,
            )
        )

    def test_weekly_digest_repeats_every_seven_days(self):
        from datetime import datetime, timedelta, timezone
        from app.digest import weekly_digest_due

        now = datetime(2026, 8, 8, tzinfo=timezone.utc)
        last = now - timedelta(days=8)
        anchor = now - timedelta(days=30)
        self.assertTrue(
            weekly_digest_due(
                now=now,
                account_anchor=anchor,
                last_digest_sent=last,
                interval_days=7,
            )
        )
        self.assertFalse(
            weekly_digest_due(
                now=now,
                account_anchor=anchor,
                last_digest_sent=now - timedelta(days=2),
                interval_days=7,
            )
        )

    def test_path_needs_refresh_when_missing_or_stale(self):
        from datetime import datetime, timedelta, timezone
        from app.digest import path_needs_refresh

        now = datetime(2026, 8, 8, tzinfo=timezone.utc)
        self.assertTrue(
            path_needs_refresh(
                recommendation=None,
                refresh_recommended=False,
                last_digest_sent=None,
            )
        )
        self.assertTrue(
            path_needs_refresh(
                recommendation={"created_at": (now - timedelta(days=2)).isoformat(), "expires_at": (now - timedelta(hours=1)).isoformat()},
                refresh_recommended=False,
                last_digest_sent=None,
            )
        )
        self.assertTrue(
            path_needs_refresh(
                recommendation={"created_at": (now - timedelta(days=2)).isoformat(), "expires_at": (now + timedelta(hours=5)).isoformat()},
                refresh_recommended=True,
                last_digest_sent=None,
            )
        )

    def test_path_ok_when_fresh_and_signals_unchanged(self):
        from datetime import datetime, timedelta, timezone
        from app.digest import path_needs_refresh

        now = datetime(2026, 8, 8, tzinfo=timezone.utc)
        self.assertFalse(
            path_needs_refresh(
                recommendation={
                    "created_at": (now - timedelta(days=1)).isoformat(),
                    "expires_at": (now + timedelta(hours=12)).isoformat(),
                },
                refresh_recommended=False,
                last_digest_sent=now - timedelta(days=8),
            )
        )

    def test_langgraph_evaluate_imports_correct_stage(self):
        from app.langgraph_agent import _import_stages
        from app.recommendations import _stage_evaluate

        stages = _import_stages()
        self.assertIs(stages[-1], _stage_evaluate)

    def test_langgraph_graph_compiles(self):
        from app.langgraph_agent import build_recommendation_graph
        graph = build_recommendation_graph()
        self.assertIsNotNone(graph)

    def test_expired_status_allowed(self):
        import inspect
        source = inspect.getsource(
            __import__("app.recommendations", fromlist=["update_recommendation_status"]).update_recommendation_status
        )
        self.assertIn("expired", source)

    def test_dashboard_template_renders_recommendation_items(self):
        templates = Environment(
            loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app" / "templates"),
            autoescape=True,
        )
        source = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text()
        self.assertIn("recommendation['items']", source)
        rendered = templates.get_template("dashboard.html").render(
            request=None,
            page_title="Dashboard",
            user={"email": "test@example.com"},
            profile={"career_goal": "AI Engineer", "onboarding_complete": True},
            recommendation={
                "id": "rec-1",
                "summary": "Start with Python foundations.",
                "next_step": "Open the tutorial.",
                "items": [
                    {
                        "product_id": "prod-1",
                        "title": "Python Tutorial",
                        "category": "Backend",
                        "difficulty": "beginner",
                        "reason": "Matches your goal.",
                        "rank": 1,
                    }
                ],
                "retrieval_metadata": {"catalog_match_count": 1, "top_score": 0.5},
                "trigger_event_count": 2,
                "created_at": "2026-08-07T12:00:00+00:00",
            },
            recommendation_error=None,
            activity=[],
            activity_error=None,
            interest_profile=None,
            interest_error=None,
            progress_stats={"path_percent": 10, "completed_count": 1},
            progress_rows=[],
            path_product_ids=[],
            learning_streak_days=2,
            weekly_minutes_logged=45,
            weekly_minutes_goal=300,
            mesh_dashboard_url="https://developers.meshapi.ai",
            previous_recommendation=None,
            recommendation_history=[],
            resend_configured=False,
            career_goals=CAREER_GOALS,
        )
        self.assertIn("Python Tutorial", rendered)


if __name__ == "__main__":
    unittest.main()
