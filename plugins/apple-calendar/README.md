# Apple Calendar

A zero-dependency MCP server for reading and safely managing the native macOS
Calendar app.

## Tools

| Tool | Effect |
| --- | --- |
| `list_calendars` | Read calendar names, native IDs, colors, and writability |
| `search_events` | Read and paginate events in an explicit date range |
| `get_event` | Read one event by exact calendar and event IDs |
| `add_event` | Create one timed or all-day event in an explicit calendar |
| `update_event` | Edit one exact non-recurring event without attendees |
| `delete_event` | Permanently delete one exact non-recurring event without attendees |

Reads include attendees, alarms, recurrence, status, sequence, modification
time, notes, location, URL, dates, and calendar identity. Mutations use exact
native IDs returned by read tools. Adds require an explicit writable calendar.

Recurring events and events with attendees are read-only. The server exposes no
bulk delete, calendar delete, attendee writer, or recurring-series writer.

All-day values use `YYYY-MM-DD` and treat `end` as inclusive. Timed values must
include an explicit UTC offset. Event updates cannot change between timed and
all-day.

## Verify

```bash
cd plugins/apple-calendar
/usr/bin/python3 -m unittest discover -s tests -v
python3 -m py_compile server.py
osacompile -l JavaScript -o /tmp/apple-calendar.scpt calendar.js
```

Tests do not access Calendar. The first live use may trigger a macOS Automation
permission prompt. Allow the calling app to control Calendar. The permission
and Calendar data remain on the Mac unless the calling client transmits returned
data.
