#!/usr/bin/env python3
"""Dependency-free MCP server for native Apple Calendar."""

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.1.1"
BRIDGE = Path(__file__).with_name("calendar.js")


def _annotations(title, *, read_only, destructive=False, idempotent=True):
    return {
        "title": title,
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
        "openWorldHint": False,
    }


ID_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 1024,
    "description": "Exact native event ID returned by a read tool.",
}
CALENDAR_ID_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 1024,
    "description": "Exact native calendar ID returned by list_calendars.",
}
CALENDAR_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 256,
    "description": "Exact Calendar name. Duplicate names are rejected.",
}
DATE_TIME_SCHEMA = {
    "type": "string",
    "description": (
        "Use YYYY-MM-DD for an all-day value, or an ISO 8601 date-time "
        "with an explicit UTC offset, such as 2026-09-01T16:00:00-04:00."
    ),
}


TOOLS = [
    {
        "name": "list_calendars",
        "title": "List Apple Calendars",
        "description": "List native calendars with exact IDs, colors, and writability.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": _annotations("List Apple Calendars", read_only=True),
    },
    {
        "name": "search_events",
        "title": "Search Apple Calendar Events",
        "description": (
            "Read events that overlap an explicit date or date-time range. "
            "Optionally search title, notes, and location or select one exact calendar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": DATE_TIME_SCHEMA,
                "end": {
                    **DATE_TIME_SCHEMA,
                    "description": (
                        "Inclusive end date for all-day searches, or exclusive end "
                        "date-time for timed searches. Must match start's kind."
                    ),
                },
                "query": {
                    "type": "string",
                    "maxLength": 512,
                    "description": "Optional case-insensitive title, notes, or location text.",
                },
                "calendar": CALENDAR_SCHEMA,
                "calendar_id": CALENDAR_ID_SCHEMA,
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10000,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        "annotations": _annotations("Search Apple Calendar Events", read_only=True),
    },
    {
        "name": "get_event",
        "title": "Get Apple Calendar Event",
        "description": "Read one event by its exact native calendar ID and event ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"calendar_id": CALENDAR_ID_SCHEMA, "id": ID_SCHEMA},
            "required": ["calendar_id", "id"],
            "additionalProperties": False,
        },
        "annotations": _annotations("Get Apple Calendar Event", read_only=True),
    },
    {
        "name": "add_event",
        "title": "Add Apple Calendar Event",
        "description": (
            "Create one timed or all-day event in an explicitly selected writable "
            "calendar. For all-day events, end is inclusive."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar": CALENDAR_SCHEMA,
                "calendar_id": CALENDAR_ID_SCHEMA,
                "title": {"type": "string", "minLength": 1, "maxLength": 512},
                "start": DATE_TIME_SCHEMA,
                "end": DATE_TIME_SCHEMA,
                "notes": {"type": "string", "maxLength": 8192},
                "location": {"type": "string", "maxLength": 1024},
                "url": {"type": "string", "maxLength": 2048},
            },
            "required": ["title", "start", "end"],
            "additionalProperties": False,
        },
        "annotations": _annotations(
            "Add Apple Calendar Event", read_only=False, idempotent=False
        ),
    },
    {
        "name": "update_event",
        "title": "Update Apple Calendar Event",
        "description": (
            "Update one non-recurring event without attendees by exact calendar ID "
            "and event ID. Start and end must be changed together."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": CALENDAR_ID_SCHEMA,
                "id": ID_SCHEMA,
                "title": {"type": "string", "minLength": 1, "maxLength": 512},
                "start": DATE_TIME_SCHEMA,
                "end": DATE_TIME_SCHEMA,
                "notes": {"type": "string", "maxLength": 8192},
                "location": {"type": "string", "maxLength": 1024},
                "url": {"type": "string", "maxLength": 2048},
            },
            "required": ["calendar_id", "id"],
            "additionalProperties": False,
        },
        "annotations": _annotations("Update Apple Calendar Event", read_only=False),
    },
    {
        "name": "delete_event",
        "title": "Delete Apple Calendar Event",
        "description": (
            "Permanently delete one non-recurring event without attendees by exact "
            "calendar ID and event ID."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"calendar_id": CALENDAR_ID_SCHEMA, "id": ID_SCHEMA},
            "required": ["calendar_id", "id"],
            "additionalProperties": False,
        },
        "annotations": _annotations(
            "Delete Apple Calendar Event",
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


class UserError(Exception):
    pass


def _arguments(arguments, allowed):
    if not isinstance(arguments, dict):
        raise UserError("arguments must be an object")
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise UserError(
            "unknown argument%s: %s"
            % ("" if len(unknown) == 1 else "s", ", ".join(unknown))
        )
    return arguments


def _text(value, field, maximum, *, required=False, allow_empty=False):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise UserError("%s must be a string" % field)
    value = value.strip()
    if required and not value and not allow_empty:
        raise UserError("%s cannot be empty" % field)
    if "\x00" in value:
        raise UserError("%s cannot contain a null byte" % field)
    if len(value) > maximum:
        raise UserError("%s must be at most %d characters" % (field, maximum))
    return value if value or allow_empty else None


def _date_or_time(value, field):
    value = _text(value, field, 64, required=True)
    if len(value) == 10:
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError:
            raise UserError("%s must be a real date in YYYY-MM-DD format" % field)
        return parsed.isoformat(), "all_day", parsed
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UserError("%s must be an ISO 8601 date-time" % field)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UserError("%s must include an explicit UTC offset" % field)
    return parsed.isoformat(timespec="seconds"), "timed", parsed


def _date_range(start_value, end_value):
    start, start_kind, start_parsed = _date_or_time(start_value, "start")
    end, end_kind, end_parsed = _date_or_time(end_value, "end")
    if start_kind != end_kind:
        raise UserError("start and end must both be dates or both be date-times")
    if start_kind == "all_day":
        if start_parsed > end_parsed:
            raise UserError("start must be on or before end")
        end_exclusive = (end_parsed + dt.timedelta(days=1)).isoformat()
    else:
        if start_parsed >= end_parsed:
            raise UserError("start must be before end")
        end_exclusive = end
    return {
        "start": start,
        "end": end,
        "end_exclusive": end_exclusive,
        "date_kind": start_kind,
    }


def _calendar_target(arguments, payload, *, required=False):
    if "calendar" in arguments and "calendar_id" in arguments:
        raise UserError("use either calendar or calendar_id, not both")
    if "calendar" in arguments:
        payload["calendar"] = _text(
            arguments["calendar"], "calendar", 256, required=True
        )
    elif "calendar_id" in arguments:
        payload["calendar_id"] = _text(
            arguments["calendar_id"], "calendar_id", 1024, required=True
        )
    elif required:
        raise UserError("calendar or calendar_id is required")


def _exact_target(arguments):
    return {
        "calendar_id": _text(
            arguments.get("calendar_id"), "calendar_id", 1024, required=True
        ),
        "id": _text(arguments.get("id"), "id", 1024, required=True),
    }


def _integer(arguments, field, minimum, maximum, *, default=None):
    value = arguments.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserError(
            "%s must be an integer from %d to %d" % (field, minimum, maximum)
        )
    if not minimum <= value <= maximum:
        raise UserError(
            "%s must be an integer from %d to %d" % (field, minimum, maximum)
        )
    return value


def _url(value, *, allow_empty=False):
    value = _text(value, "url", 2048, required=True, allow_empty=allow_empty)
    if value == "" and allow_empty:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UserError("url must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise UserError("url cannot include credentials")
    return value


def _optional_text(arguments, field, maximum, payload):
    if field in arguments:
        payload[field] = _text(
            arguments[field], field, maximum, allow_empty=True
        )


def normalize_arguments(name, arguments):
    if name == "list_calendars":
        _arguments(arguments, set())
        return {"action": name}

    if name == "search_events":
        arguments = _arguments(
            arguments,
            {"start", "end", "query", "calendar", "calendar_id", "offset", "limit"},
        )
        date_range = _date_range(arguments.get("start"), arguments.get("end"))
        payload = {
            "action": name,
            "range_start": date_range["start"],
            "range_end": date_range["end"],
            "range_end_exclusive": date_range["end_exclusive"],
            "range_kind": date_range["date_kind"],
            "query": _text(arguments.get("query"), "query", 512),
            "offset": _integer(arguments, "offset", 0, 10000, default=0),
            "limit": _integer(arguments, "limit", 1, 200, default=50),
        }
        _calendar_target(arguments, payload)
        return payload

    if name in {"get_event", "delete_event"}:
        arguments = _arguments(arguments, {"calendar_id", "id"})
        return {"action": name, **_exact_target(arguments)}

    if name == "add_event":
        arguments = _arguments(
            arguments,
            {
                "calendar",
                "calendar_id",
                "title",
                "start",
                "end",
                "notes",
                "location",
                "url",
            },
        )
        payload = {
            "action": name,
            "title": _text(arguments.get("title"), "title", 512, required=True),
            **_date_range(arguments.get("start"), arguments.get("end")),
        }
        _calendar_target(arguments, payload, required=True)
        if "notes" in arguments:
            payload["notes"] = _text(arguments["notes"], "notes", 8192)
        if "location" in arguments:
            payload["location"] = _text(arguments["location"], "location", 1024)
        if "url" in arguments:
            payload["url"] = _url(arguments["url"])
        return payload

    if name == "update_event":
        arguments = _arguments(
            arguments,
            {"calendar_id", "id", "title", "start", "end", "notes", "location", "url"},
        )
        payload = {"action": name, **_exact_target(arguments)}
        if "title" in arguments:
            payload["title"] = _text(
                arguments["title"], "title", 512, required=True
            )
        if ("start" in arguments) != ("end" in arguments):
            raise UserError("start and end must be updated together")
        if "start" in arguments:
            payload.update(_date_range(arguments["start"], arguments["end"]))
        _optional_text(arguments, "notes", 8192, payload)
        _optional_text(arguments, "location", 1024, payload)
        if "url" in arguments:
            payload["url"] = _url(arguments["url"], allow_empty=True)
        if len(payload) == 3:
            raise UserError("update_event requires at least one field to change")
        return payload

    raise UserError("unknown tool")


def invoke_calendar(payload):
    try:
        completed = subprocess.run(
            [
                "/usr/bin/osascript",
                "-l",
                "JavaScript",
                str(BRIDGE),
                json.dumps(payload, ensure_ascii=False),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise UserError("Apple Calendar did not respond within 180 seconds")
    except OSError as error:
        raise UserError("could not launch Apple Calendar automation: %s" % error)

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        detail = detail.splitlines()[-1] if detail else "unknown automation error"
        raise UserError("Apple Calendar rejected the request: %s" % detail)

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise UserError("Apple Calendar returned an unreadable response")
    if not isinstance(result, dict):
        raise UserError("Apple Calendar returned an invalid response")
    return result


def _tool_result(text, structured=None, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if structured is not None:
        result["structuredContent"] = structured
    if is_error:
        result["isError"] = True
    return result


def call_tool(params):
    if not isinstance(params, dict) or params.get("name") not in TOOL_NAMES:
        raise UserError("unknown tool")
    name = params["name"]
    try:
        payload = normalize_arguments(name, params.get("arguments", {}))
        result = invoke_calendar(payload)
    except UserError as error:
        return _tool_result(str(error), is_error=True)

    if name == "list_calendars":
        message = "Found %d calendars." % len(result.get("calendars", []))
    elif name == "search_events":
        message = "Found %d matching events." % len(result.get("events", []))
    elif name == "get_event":
        event = result.get("event", {})
        message = '“%s” is on “%s”.' % (
            event.get("title", "Event"),
            event.get("calendar", "Calendar"),
        )
    elif name == "add_event":
        event = result.get("event", {})
        message = 'Added “%s” to “%s”.' % (
            event.get("title", "Event"),
            event.get("calendar", "Calendar"),
        )
    elif name == "update_event":
        message = 'Updated “%s”.' % result.get("event", {}).get("title", "event")
    else:
        message = 'Deleted “%s”.' % result.get("deleted", {}).get("title", "event")
    return _tool_result(message, result)


def _response(message_id, result):
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def handle_message(message):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")

    method = message.get("method")
    message_id = message.get("id")
    if message_id is None:
        return None

    if method == "initialize":
        return _response(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "apple-calendar", "version": SERVER_VERSION},
                "instructions": (
                    "Reads and manages native Apple Calendar. List calendars first, "
                    "then use exact calendar and event IDs for single-event actions. "
                    "Resolve relative dates before calling. Recurring and attendee-bearing "
                    "events are read-only."
                ),
            },
        )
    if method == "ping":
        return _response(message_id, {})
    if method == "tools/list":
        return _response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        try:
            return _response(message_id, call_tool(message.get("params")))
        except UserError as error:
            return _error(message_id, -32602, str(error))
    return _error(message_id, -32601, "Method not found")


def main():
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
            response = handle_message(message)
        except json.JSONDecodeError:
            response = _error(None, -32700, "Parse error")
        except Exception as error:  # Keep one bad request from killing the process.
            print("apple-calendar server error: %s" % error, file=sys.stderr)
            response = _error(None, -32603, "Internal error")
        if response is not None:
            print(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )


if __name__ == "__main__":
    main()
