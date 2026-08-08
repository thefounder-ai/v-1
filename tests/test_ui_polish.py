import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.catalog import CAREER_GOALS


class UiPolishTests(unittest.TestCase):
    def setUp(self):
        self.templates = Environment(
            loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app" / "templates"),
            autoescape=True,
        )

    def test_explore_shows_qdrant_badge(self):
        rendered = self.templates.get_template("explore.html").render(
            request=None,
            page_title="Explore",
            products=[],
            career_goals=CAREER_GOALS,
            career_goal=None,
            search="",
            category="",
            difficulty="",
            content_type="",
            categories=[],
            error=None,
        )
        self.assertIn("Semantic search · Qdrant", rendered)

    def test_dashboard_keyboard_hint_and_featured_stats(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text()
        self.assertIn('Press <kbd>G</kbd>', source)
        self.assertIn("stat-pill-featured", source)
        self.assertIn("stat-pill-streak", source)

    def test_empty_state_partial_renders_icon(self):
        rendered = self.templates.get_template("partials/empty_state.html").render()
        macro = self.templates.from_string(
            '{% from "partials/empty_state.html" import empty_state %}'
            '{{ empty_state("bookmark", "Title", "Body", "/explore", "Go") }}'
        ).render()
        self.assertIn("empty-illustration", macro)
        self.assertIn("Title", macro)

    def test_bookmarks_page_has_profile_boost_tooltip(self):
        rendered = self.templates.get_template("bookmarks.html").render(
            request=None,
            page_title="Bookmarks",
            products=[],
            error=None,
        )
        self.assertIn("Boosts your interest profile", rendered)

    def test_landing_has_demo_preview_frame(self):
        rendered = self.templates.get_template("landing.html").render(
            request=None,
            page_title="Landing",
            user=None,
        )
        self.assertIn("demo-preview-frame", rendered)
        self.assertIn("Day streak", rendered)

    def test_ui_toast_catalog_defined(self):
        ui_source = (Path(__file__).resolve().parents[1] / "app" / "static" / "ui.js").read_text()
        self.assertIn("profileSynced", ui_source)
        self.assertIn("pathGenerated", ui_source)
        self.assertIn("bookmarkSaved", ui_source)

    def test_app_nav_has_sidebar_backdrop(self):
        rendered = self.templates.get_template("partials/app-nav.html").render(
            active="dashboard",
            user={"email": "test@example.com"},
            profile={"role": "learner"},
        )
        self.assertIn("sidebar-backdrop", rendered)


if __name__ == "__main__":
    unittest.main()
