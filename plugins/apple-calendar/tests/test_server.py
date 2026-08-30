import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN / "server.py"
BRIDGE_PATH = PLUGIN / "calendar.js"

spec = importlib.util.spec_from_file_location("apple_calendar_server", SERVER_PATH)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class NormalizationTests(unittest.TestCase):
    def test_search_timed_range_and_pagination(self):
        payload = server.normalize_arguments(
            "search_events",
            {
                "start": "2026-09-01T09:00:00-04:00",
                "end": "2026-09-02T09:00:00-04:00",
                "query": " review ",
                "calendar_id": "work-id",
                "offset": 25,
                "limit": 10,
            },
        )
        self.assertEqual(payload["action"], "search_events")
        self.assertEqual(payload["range_kind"], "timed")
        self.assertEqual(payload["range_start"], "2026-09-01T09:00:00-04:00")
        self.assertEqual(payload["range_end_exclusive"], "2026-09-02T09:00:00-04:00")
        self.assertEqual(payload["query"], "review")
        self.assertEqual(payload["calendar_id"], "work-id")
        self.assertEqual(payload["offset"], 25)
        self.assertEqual(payload["limit"], 10)

    def test_all_day_end_is_normalized_to_native_exclusive_end(self):
        payload = server.normalize_arguments(
            "add_event",
            {
                "calendar": "Projects",
                "title": "Launch window",
                "start": "2026-09-04",
                "end": "2026-09-06",
            },
        )
        self.assertEqual(payload["date_kind"], "all_day")
        self.assertEqual(payload["end"], "2026-09-06")
        self.assertEqual(payload["end_exclusive"], "2026-09-07")
        self.assertEqual(payload["calendar"], "Projects")

    def test_add_event_requires_an_explicit_calendar(self):
        with self.assertRaisesRegex(server.UserError, "calendar or calendar_id"):
            server.normalize_arguments(
                "add_event",
                {
                    "title": "Review",
                    "start": "2026-09-01T15:00:00-04:00",
                    "end": "2026-09-01T16:00:00-04:00",
                },
            )

    def test_exact_event_actions_require_both_ids(self):
        for tool in ("get_event", "delete_event", "update_event"):
            with self.subTest(tool=tool):
                with self.assertRaisesRegex(server.UserError, "calendar_id"):
                    server.normalize_arguments(tool, {"id": "event-id"})

    def test_update_can_clear_optional_text_and_url(self):
        payload = server.normalize_arguments(
            "update_event",
            {
                "calendar_id": "calendar-id",
                "id": "event-id",
                "notes": "",
                "location": "",
                "url": "",
            },
        )
        self.assertEqual(payload["notes"], "")
        self.assertEqual(payload["location"], "")
        self.assertEqual(payload["url"], "")

    def test_update_requires_start_and_end_together(self):
        with self.assertRaisesRegex(server.UserError, "updated together"):
            server.normalize_arguments(
                "update_event",
                {
                    "calendar_id": "calendar-id",
                    "id": "event-id",
                    "start": "2026-09-01T15:00:00-04:00",
                },
            )

    def test_update_requires_a_change(self):
        with self.assertRaisesRegex(server.UserError, "at least one field"):
            server.normalize_arguments(
                "update_event", {"calendar_id": "calendar-id", "id": "event-id"}
            )

    def test_rejects_naive_mixed_and_backwards_dates(self):
        invalid_ranges = [
            ("2026-09-01T15:00:00", "2026-09-01T16:00:00", "UTC offset"),
            ("2026-09-01", "2026-09-02T16:00:00-04:00", "both be dates"),
            ("2026-09-02", "2026-09-01", "on or before"),
            (
                "2026-09-01T16:00:00-04:00",
                "2026-09-01T15:00:00-04:00",
                "before end",
            ),
        ]
        for start, end, message in invalid_ranges:
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(server.UserError, message):
                    server.normalize_arguments(
                        "search_events", {"start": start, "end": end}
                    )

    def test_rejects_ambiguous_calendar_selector(self):
        with self.assertRaisesRegex(server.UserError, "either calendar or calendar_id"):
            server.normalize_arguments(
                "search_events",
                {
                    "start": "2026-09-01",
                    "end": "2026-09-01",
                    "calendar": "Work",
                    "calendar_id": "work-id",
                },
            )

    def test_rejects_unknown_arguments_and_boolean_integer(self):
        with self.assertRaisesRegex(server.UserError, "unknown argument"):
            server.normalize_arguments("list_calendars", {"surprise": True})
        with self.assertRaisesRegex(server.UserError, "limit must be an integer"):
            server.normalize_arguments(
                "search_events",
                {"start": "2026-09-01", "end": "2026-09-01", "limit": True},
            )

    def test_url_validation(self):
        good = server.normalize_arguments(
            "update_event",
            {
                "calendar_id": "calendar-id",
                "id": "event-id",
                "url": "https://example.com/review?id=1",
            },
        )
        self.assertEqual(good["url"], "https://example.com/review?id=1")
        for value in ("calendar://event", "https://user:pass@example.com"):
            with self.subTest(value=value):
                with self.assertRaises(server.UserError):
                    server.normalize_arguments(
                        "update_event",
                        {"calendar_id": "c", "id": "e", "url": value},
                    )


class ProtocolTests(unittest.TestCase):
    def test_initialize_and_tool_list(self):
        initialized = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(
            initialized["result"]["protocolVersion"], server.PROTOCOL_VERSION
        )
        self.assertEqual(initialized["result"]["serverInfo"]["version"], "0.1.1")

        listed = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        tools = listed["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "list_calendars",
                "search_events",
                "get_event",
                "add_event",
                "update_event",
                "delete_event",
            ],
        )
        for tool in tools:
            self.assertFalse(tool["annotations"]["openWorldHint"])
        delete = next(tool for tool in tools if tool["name"] == "delete_event")
        self.assertTrue(delete["annotations"]["destructiveHint"])
        self.assertFalse(delete["annotations"]["idempotentHint"])

    @mock.patch.object(server, "invoke_calendar")
    def test_tool_call_returns_structured_content(self, invoke):
        invoke.return_value = {
            "event": {"id": "event-id", "title": "Project review", "calendar": "Work"}
        }
        result = server.call_tool(
            {
                "name": "get_event",
                "arguments": {"calendar_id": "work-id", "id": "event-id"},
            }
        )
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["event"]["id"], "event-id")
        self.assertIn("Project review", result["content"][0]["text"])
        invoke.assert_called_once_with(
            {"action": "get_event", "calendar_id": "work-id", "id": "event-id"}
        )

    def test_validation_failure_is_a_tool_error(self):
        result = server.call_tool(
            {
                "name": "search_events",
                "arguments": {"start": "tomorrow", "end": "Friday"},
            }
        )
        self.assertTrue(result["isError"])
        self.assertIn("ISO 8601 date-time", result["content"][0]["text"])

    def test_json_lines_server_round_trip(self):
        requests = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        )
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input=requests + "\n",
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertEqual(len(responses[1]["result"]["tools"]), 6)


class JavaScriptBridgeTests(unittest.TestCase):
    def run_bridge_harness(self, expression):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        harness = "function run(argv) { return JSON.stringify(" + expression + "); }"
        completed = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", source, "-e", harness],
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        return json.loads(completed.stdout)

    def test_timed_and_all_day_serialization(self):
        result = self.run_bridge_harness(
            "{"
            "timed: eventDateRange({"
            "alldayEvent: () => false,"
            "startDate: () => new Date('2026-09-01T13:00:00Z'),"
            "endDate: () => new Date('2026-09-01T14:30:00Z')}),"
            "all_day: eventDateRange({"
            "alldayEvent: () => true,"
            "startDate: () => new Date(2026, 8, 4),"
            "endDate: () => new Date(2026, 8, 7)})}"
        )
        self.assertEqual(result["timed"]["start"], "2026-09-01T13:00:00.000Z")
        self.assertEqual(result["timed"]["end"], "2026-09-01T14:30:00.000Z")
        self.assertEqual(result["all_day"]["start"], "2026-09-04")
        self.assertEqual(result["all_day"]["end"], "2026-09-06")

    def test_calendar_id_parser_handles_the_native_specifier(self):
        result = self.run_bridge_harness(
            "calendarIdFromDisplay("
            "'Application(\\\"Calendar\\\").calendars.byId(\\\"calendar-id\\\")')"
        )
        self.assertEqual(result, "calendar-id")

    def test_event_overlap_uses_half_open_ranges(self):
        result = self.run_bridge_harness(
            "(() => {"
            "const event = (start, end) => ({"
            "startDate: () => new Date(start), endDate: () => new Date(end)});"
            "const start = new Date('2026-09-01T13:00:00Z');"
            "const end = new Date('2026-09-01T14:00:00Z');"
            "return {"
            "inside: eventOverlapsRange(event('2026-09-01T13:15:00Z',"
            "'2026-09-01T13:30:00Z'), start, end),"
            "starts_at_end: eventOverlapsRange(event('2026-09-01T14:00:00Z',"
            "'2026-09-01T15:00:00Z'), start, end),"
            "ends_at_start: eventOverlapsRange(event('2026-09-01T12:00:00Z',"
            "'2026-09-01T13:00:00Z'), start, end),"
            "spans: eventOverlapsRange(event('2026-09-01T12:00:00Z',"
            "'2026-09-01T15:00:00Z'), start, end)};"
            "})()"
        )
        self.assertEqual(
            result,
            {"inside": True, "starts_at_end": False, "ends_at_start": False, "spans": True},
        )

    def test_calendar_dates_are_read_in_bulk(self):
        result = self.run_bridge_harness(
            "(() => {"
            "let eventReads = 0; let startReads = 0; let endReads = 0;"
            "const collection = () => { eventReads += 1; return [{id: 1}, {id: 2}]; };"
            "collection.startDate = () => { startReads += 1; return ["
            "new Date('2026-09-01T13:00:00Z'), new Date('2026-09-02T13:00:00Z')]; };"
            "collection.endDate = () => { endReads += 1; return ["
            "new Date('2026-09-01T14:00:00Z'), new Date('2026-09-02T14:00:00Z')]; };"
            "const records = calendarEventsWithDates({events: collection});"
            "return {event_reads: eventReads, start_reads: startReads, end_reads: endReads,"
            "count: records.length, first_id: records[0].event.id};"
            "})()"
        )
        self.assertEqual(
            result,
            {
                "event_reads": 1,
                "start_reads": 1,
                "end_reads": 1,
                "count": 2,
                "first_id": 1,
            },
        )

    def test_edit_safety_blocks_recurring_and_invited_events(self):
        result = self.run_bridge_harness(
            "(() => {"
            "const calendar = {writable: () => true, name: () => 'Work'};"
            "const message = (event) => { try { ensureEditableEvent(event, calendar);"
            "return null; } catch (error) { return error.message; } };"
            "return {"
            "recurring: message({recurrence: () => 'FREQ=WEEKLY', attendees: () => []}),"
            "invited: message({recurrence: () => '', attendees: () => [{}]}),"
            "editable: message({recurrence: () => '', attendees: () => []})};"
            "})()"
        )
        self.assertIn("Recurring events are read-only", result["recurring"])
        self.assertIn("Events with attendees are read-only", result["invited"])
        self.assertIsNone(result["editable"])


if __name__ == "__main__":
    unittest.main()
