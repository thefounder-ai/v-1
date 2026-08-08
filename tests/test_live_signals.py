import unittest
from datetime import datetime, timezone

from app.activity import count_meaningful_events, format_live_event
from app.live_signals import SignalBus
from app.main import _parse_since_timestamp, _sse_message, app


class LiveSignalTests(unittest.TestCase):
    def test_events_stream_route_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/api/events/stream", paths)

    def test_parse_since_timestamp_defaults_to_recent(self):
        parsed = _parse_since_timestamp(None)
        self.assertLess(
            (datetime.now(timezone.utc) - parsed).total_seconds(),
            10,
        )

    def test_sse_message_format(self):
        message = _sse_message("stats", {"meaningful_event_count": 3})
        self.assertIn("event: stats", message)
        self.assertIn('"meaningful_event_count": 3', message)

    def test_format_live_event_for_search(self):
        row = format_live_event({
            "event_id": "abc",
            "event_type": "catalog_search",
            "search_query": "fastapi",
            "occurred_at": "2026-08-08T10:00:00+00:00",
        })
        self.assertEqual(row["detail"], "fastapi")
        self.assertTrue(row["meaningful"])

    def test_count_meaningful_events(self):
        events = [
            {"event_type": "page_view"},
            {"event_type": "catalog_search"},
            {"event_type": "resource_dwell"},
        ]
        self.assertEqual(count_meaningful_events(events), 2)


class SignalBusAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_bus_publish_and_receive(self):
        bus = SignalBus()
        queue = bus.subscribe("user-1")
        await bus.publish("user-1", {"accepted": 2})
        payload = await queue.get()
        self.assertEqual(payload["accepted"], 2)
        bus.unsubscribe("user-1", queue)


if __name__ == "__main__":
    unittest.main()
