# Security

## Boundary

Apple Calendar MCP exposes three read tools and three mutation tools. Read tools
can list calendar metadata and return event contents. Adds target one explicitly
selected writable calendar. Updates and deletes target one exact native calendar
ID and event ID returned by a read tool.

The server does not expose bulk deletion, calendar deletion, attendee changes,
or recurring-series changes. Recurring events and events with attendees are
read-only. The delete tool is marked destructive; add and delete are marked
non-idempotent; read tools are marked read-only.

The server invokes a fixed JXA file through `/usr/bin/osascript`. User input is
serialized as JSON and passed as a separate process argument. It is never shell
interpolated or executed as source code. Inputs reject unknown fields, null
bytes, oversized strings, ambiguous calendar selectors, invalid dates, unsafe
URLs, and exact-target mutations without native IDs.

## Data access

Read tools can return event titles, notes, locations, URLs, dates, alarms,
attendees, recurrence rules, status, modification metadata, calendar names, and
native IDs to the calling MCP client. Local Codex use requires no API key and
sends nothing to this project.

macOS Automation permission and Calendar data remain on the Mac unless the
calling client or a user-configured tunnel transmits returned data. When a
remote MCP tunnel is used, that tunnel and client are part of the data boundary.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/henryvn27/apple-calendar-mcp/security/advisories/new).
Include the affected version, reproduction steps, and expected impact. Do not
open a public issue for an unpatched vulnerability.

## Credentials

The plugin requires no credentials for local Codex use. Keep credentials for
any optional remote MCP tunnel outside this repository.
