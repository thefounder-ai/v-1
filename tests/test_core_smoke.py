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
        self.assertIs(stages[6], _stage_evaluate)

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

    def test_pipeline_timings_from_stages(self):
        from app.recommendations import pipeline_timings_from_stages

        stages = [
            {"name": "retrieve", "duration_ms": 120},
            {"name": "generate", "duration_ms": 890},
            {"name": "validate", "duration_ms": 40},
        ]
        timings = pipeline_timings_from_stages(stages)
        self.assertEqual(timings["retrieve_ms"], 120)
        self.assertEqual(timings["generate_ms"], 890)
        self.assertEqual(timings["validate_ms"], 40)
        self.assertEqual(timings["total_ms"], 1050)

    def test_annotate_retrieval_candidates_marks_selected_and_rejected(self):
        from app.recommendations import annotate_retrieval_candidates

        retrieval: dict = {}
        matches = [
            {"product_id": "p1", "score": 0.91},
            {"product_id": "p2", "score": 0.82},
            {"product_id": "p3", "score": 0.74},
        ]
        candidates = [
            {"id": "p1", "title": "Python Basics", "category": "Backend", "difficulty": "beginner"},
            {"id": "p2", "title": "FastAPI Guide", "category": "Backend", "difficulty": "intermediate"},
            {"id": "p3", "title": "SQL Intro", "category": "Data", "difficulty": "beginner"},
        ]
        selected = [candidates[0], candidates[2]]
        annotate_retrieval_candidates(retrieval, matches, candidates, selected)

        self.assertEqual(len(retrieval["candidates"]), 3)
        self.assertTrue(retrieval["candidates"][0]["selected"])
        self.assertFalse(retrieval["candidates"][1]["selected"])
        self.assertTrue(retrieval["candidates"][2]["selected"])
        self.assertEqual(retrieval["selected_count"], 2)
        self.assertEqual(retrieval["rejected_count"], 1)
        self.assertEqual(retrieval["candidates"][0]["title"], "Python Basics")
        self.assertEqual(retrieval["candidates"][0]["score"], 0.91)

    def test_finalize_retrieval_metadata_includes_pipeline_stages(self):
        from app.agent_graph import RecommendationGraphState
        from app.recommendations import finalize_retrieval_metadata

        state = RecommendationGraphState(trace_id="trace-1")
        state.stages = [
            {"name": "retrieve", "status": "completed", "duration_ms": 120, "match_count": 5},
            {"name": "generate", "status": "completed", "duration_ms": 800, "model": "mesh"},
        ]
        state.retrieval = {"catalog_match_count": 5, "top_score": 0.88}
        metadata = finalize_retrieval_metadata(state)

        self.assertIn("pipeline_timings", metadata)
        self.assertIn("pipeline_stages", metadata)
        self.assertEqual(metadata["pipeline_stages"][0]["name"], "retrieve")
        self.assertEqual(metadata["pipeline_timings"]["retrieve_ms"], 120)
        self.assertEqual(metadata["pipeline_timings"]["total_ms"], 920)

    def test_format_signal_events_for_timeline(self):
        from app.recommendations import format_signal_events

        events = [
            {
                "event_type": "catalog_search",
                "search_query": "python async",
                "occurred_at": "2026-08-08T10:00:00+00:00",
            },
            {
                "event_type": "bookmark_added",
                "resource_id": "p1",
                "occurred_at": "2026-08-08T10:05:00+00:00",
            },
        ]
        products = {
            "p1": {"title": "FastAPI Patterns", "category": "Backend"},
        }
        formatted = format_signal_events(events, products)
        self.assertEqual(len(formatted), 2)
        self.assertEqual(formatted[0]["kind"], "signal")
        self.assertEqual(formatted[0]["detail"], "python async")
        self.assertEqual(formatted[1]["detail"], "FastAPI Patterns")

    def test_build_causality_timeline_orders_signals_then_stages(self):
        from app.recommendations import build_causality_timeline

        signals = [
            {
                "kind": "signal",
                "label": "Searched catalog",
                "detail": "rust",
                "occurred_at": "2026-08-08T09:00:00+00:00",
            }
        ]
        stages = [
            {"name": "analyze", "status": "completed", "duration_ms": 12, "completed_at": "2026-08-08T09:00:01+00:00"},
            {"name": "retrieve", "status": "completed", "duration_ms": 88, "completed_at": "2026-08-08T09:00:02+00:00"},
        ]
        timeline = build_causality_timeline(signals, stages)
        self.assertEqual(len(timeline), 3)
        self.assertEqual(timeline[0]["kind"], "signal")
        self.assertEqual(timeline[1]["name"], "analyze")
        self.assertEqual(timeline[2]["name"], "retrieve")

    def test_fallback_change_explanation_uses_catalog_titles_only(self):
        from app.recommendations import fallback_change_explanation

        explanation = fallback_change_explanation(
            {"signal_summary": "interest in Backend"},
            [{"label": "Searched catalog", "detail": "fastapi"}],
            {
                "summary": "Old path",
                "items": [{"title": "Python Basics"}],
            },
            [{"title": "FastAPI Guide"}, {"title": "SQL Intro"}],
        )
        self.assertIn("FastAPI Guide", explanation)
        self.assertIn("fastapi", explanation.lower())

    def test_trace_template_renders_retrieval_candidates(self):
        templates = Environment(
            loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app" / "templates"),
            autoescape=True,
        )
        rendered = templates.get_template("trace.html").render(
            request=None,
            page_title="Trace",
            error=None,
            mesh_dashboard_url="https://developers.meshapi.ai",
            recommendation={
                "summary": "Build Python foundations.",
                "model": "mesh-model",
                "trace_id": "trace-abc-123",
                "trigger_event_count": 4,
                "retrieval_query": "python backend beginner",
                "retrieval_metadata": {
                    "catalog_match_count": 3,
                    "top_score": 0.91,
                    "mean_score": 0.82,
                    "selected_count": 2,
                    "pipeline_timings": {"retrieve_ms": 100, "generate_ms": 800, "total_ms": 900},
                    "candidates": [
                        {
                            "rank": 1,
                            "product_id": "p1",
                            "title": "Python Tutorial",
                            "category": "Backend",
                            "difficulty": "beginner",
                            "score": 0.91,
                            "selected": True,
                        },
                        {
                            "rank": 2,
                            "product_id": "p2",
                            "title": "FastAPI",
                            "category": "Backend",
                            "difficulty": "intermediate",
                            "score": 0.84,
                            "selected": False,
                        },
                    ],
                },
                "graph_stages": [
                    {"name": "retrieve", "status": "completed", "duration_ms": 100},
                    {"name": "generate", "status": "completed", "duration_ms": 800},
                ],
                "items": [
                    {
                        "rank": 1,
                        "title": "Python Tutorial",
                        "reason": "Matches your goal.",
                        "retrieval_score": 0.91,
                    }
                ],
            },
            recommendation_history=[],
        )
        self.assertIn("Qdrant candidates", rendered)
        self.assertIn("Python Tutorial", rendered)
        self.assertIn("is-selected", rendered)
        self.assertIn("is-rejected", rendered)
        self.assertIn("Copy trace ID", rendered)

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
