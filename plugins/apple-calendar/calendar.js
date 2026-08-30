function text(value) {
  return value === null || value === undefined ? "" : String(value);
}

function safe(read, fallback) {
  try {
    const value = read();
    return value === null || value === undefined ? fallback : value;
  } catch (error) {
    return fallback;
  }
}

function localDate(value) {
  const date = new Date(value);
  const year = String(date.getFullYear()).padStart(4, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dateFromInput(value, kind) {
  return kind === "all_day"
    ? new Date(`${value}T00:00:00`)
    : new Date(value);
}

function enumText(value) {
  return text(value)
    .replace(/^.*\./, "")
    .replace(/([a-z])([A-Z])/g, "$1_$2")
    .replace(/\s+/g, "_")
    .toLocaleLowerCase();
}

function colorHex(value) {
  if (!Array.isArray(value) || value.length < 3) return null;
  const components = value.slice(0, 3).map((component) => {
    const byte = Math.max(0, Math.min(255, Math.round(Number(component) / 257)));
    return byte.toString(16).padStart(2, "0");
  });
  return `#${components.join("")}`;
}

function calendarIdFromDisplay(display) {
  const marker = ".calendars.byId(";
  const start = display.lastIndexOf(marker);
  if (start === -1 || !display.endsWith(")")) {
    throw new Error("Calendar returned an unreadable calendar identifier.");
  }
  const literal = display.slice(start + marker.length, -1);
  const id = JSON.parse(literal);
  if (typeof id !== "string" || !id) {
    throw new Error("Calendar returned an empty calendar identifier.");
  }
  return id;
}

function calendarId(calendar) {
  return calendarIdFromDisplay(Automation.getDisplayString(calendar));
}

function exactCalendar(calendarApp, name) {
  const wanted = name.toLocaleLowerCase();
  const matches = calendarApp.calendars().filter(
    (calendar) => calendar.name().toLocaleLowerCase() === wanted,
  );
  if (matches.length === 0) {
    throw new Error(`No Calendar named “${name}” exists.`);
  }
  if (matches.length > 1) {
    throw new Error(`More than one Calendar is named “${name}”. Use calendar_id.`);
  }
  return matches[0];
}

function exactCalendarId(calendarApp, id) {
  const calendar = calendarApp.calendars.byId(id);
  try {
    if (calendarId(calendar) === id) return calendar;
  } catch (error) {}
  throw new Error(`No Calendar with ID “${id}” exists.`);
}

function selectedCalendar(calendarApp, input) {
  if (input.calendar_id) return exactCalendarId(calendarApp, input.calendar_id);
  if (input.calendar) return exactCalendar(calendarApp, input.calendar);
  throw new Error("An exact calendar or calendar_id is required.");
}

function ensureWritable(calendar) {
  if (!Boolean(calendar.writable())) {
    throw new Error(`Calendar “${calendar.name()}” is read-only.`);
  }
}

function findEvent(calendarApp, calendarId, eventId) {
  const calendar = exactCalendarId(calendarApp, calendarId);
  const event = calendar.events.byId(eventId);
  try {
    if (event.uid() === eventId) return { calendar, event };
  } catch (error) {}
  throw new Error(`No event with ID “${eventId}” exists in “${calendar.name()}”.`);
}

function serializeCalendar(calendar) {
  return {
    id: calendarId(calendar),
    name: text(calendar.name()),
    writable: Boolean(calendar.writable()),
    color: colorHex(safe(() => calendar.color(), null)),
  };
}

function serializeAlarm(alarm, type) {
  const triggerDate = safe(() => alarm.triggerDate(), null);
  return {
    type,
    trigger_interval: safe(() => Number(alarm.triggerInterval()), null),
    trigger_date: triggerDate ? new Date(triggerDate).toISOString() : null,
  };
}

function serializeAlarms(event) {
  const alarms = [];
  for (const alarm of safe(() => event.displayAlarms(), [])) {
    alarms.push(serializeAlarm(alarm, "display"));
  }
  for (const alarm of safe(() => event.mailAlarms(), [])) {
    alarms.push(serializeAlarm(alarm, "mail"));
  }
  for (const alarm of safe(() => event.soundAlarms(), [])) {
    alarms.push(serializeAlarm(alarm, "sound"));
  }
  for (const alarm of safe(() => event.openFileAlarms(), [])) {
    alarms.push(serializeAlarm(alarm, "open_file"));
  }
  return alarms;
}

function serializeAttendees(event) {
  return safe(() => event.attendees(), []).map((attendee) => ({
    name: text(safe(() => attendee.displayName(), "")),
    email: text(safe(() => attendee.email(), "")),
    status: enumText(safe(() => attendee.participationStatus(), "unknown")),
  }));
}

function eventDateRange(event) {
  const allDay = Boolean(event.alldayEvent());
  const startDate = new Date(event.startDate());
  const endDate = new Date(event.endDate());
  if (!allDay) {
    return {
      start: startDate.toISOString(),
      end: endDate.toISOString(),
      date_kind: "timed",
    };
  }
  const inclusiveEnd = new Date(endDate);
  inclusiveEnd.setDate(inclusiveEnd.getDate() - 1);
  if (inclusiveEnd.getTime() < startDate.getTime()) {
    inclusiveEnd.setTime(startDate.getTime());
  }
  return {
    start: localDate(startDate),
    end: localDate(inclusiveEnd),
    date_kind: "all_day",
  };
}

function serializeEvent(event, calendar) {
  const dates = eventDateRange(event);
  const stamp = safe(() => event.stampDate(), null);
  return {
    id: event.uid(),
    calendar: text(calendar.name()),
    calendar_id: calendarId(calendar),
    calendar_writable: Boolean(calendar.writable()),
    title: text(event.summary()),
    start: dates.start,
    end: dates.end,
    date_kind: dates.date_kind,
    notes: text(safe(() => event.description(), "")),
    location: text(safe(() => event.location(), "")),
    url: text(safe(() => event.url(), "")),
    recurrence: text(safe(() => event.recurrence(), "")) || null,
    status: enumText(safe(() => event.status(), "none")),
    sequence: safe(() => Number(event.sequence()), null),
    modified_at: stamp ? new Date(stamp).toISOString() : null,
    attendees: serializeAttendees(event),
    alarms: serializeAlarms(event),
  };
}

function ensureEditableEvent(event, calendar) {
  ensureWritable(calendar);
  if (text(safe(() => event.recurrence(), ""))) {
    throw new Error("Recurring events are read-only; change this series in Calendar.");
  }
  if (safe(() => event.attendees(), []).length > 0) {
    throw new Error(
      "Events with attendees are read-only to avoid sending invitation updates.",
    );
  }
}

function listCalendars(calendarApp) {
  return { calendars: calendarApp.calendars().map(serializeCalendar) };
}

function eventOverlapsRange(event, rangeStart, rangeEnd) {
  const eventStart = new Date(event.startDate()).getTime();
  const eventEnd = new Date(event.endDate()).getTime();
  return eventStart < rangeEnd.getTime() && eventEnd > rangeStart.getTime();
}

function searchEvents(calendarApp, input) {
  const calendars = input.calendar || input.calendar_id
    ? [selectedCalendar(calendarApp, input)]
    : calendarApp.calendars();
  const rangeStart = dateFromInput(input.range_start, input.range_kind);
  const rangeEnd = dateFromInput(input.range_end_exclusive, input.range_kind);
  const query = input.query ? input.query.toLocaleLowerCase() : null;
  const matches = [];
  const skippedCalendars = [];

  for (const calendar of calendars) {
    let events;
    try {
      events = calendar.events();
    } catch (error) {
      skippedCalendars.push({
        id: safe(() => calendarId(calendar), null),
        name: text(safe(() => calendar.name(), "Calendar")),
      });
      continue;
    }
    for (const event of events) {
      if (!eventOverlapsRange(event, rangeStart, rangeEnd)) continue;
      if (query) {
        const haystack = [
          text(safe(() => event.summary(), "")),
          text(safe(() => event.description(), "")),
          text(safe(() => event.location(), "")),
        ].join("\n").toLocaleLowerCase();
        if (!haystack.includes(query)) continue;
      }
      matches.push({
        event,
        calendar,
        sort: new Date(event.startDate()).getTime(),
      });
    }
  }

  matches.sort((left, right) => {
    return left.sort - right.sort;
  });
  const page = matches.slice(input.offset, input.offset + input.limit);
  const truncated = input.offset + page.length < matches.length;
  return {
    events: page.map((item) => serializeEvent(item.event, item.calendar)),
    count: page.length,
    truncated,
    next_offset: truncated ? input.offset + page.length : null,
    skipped_calendars: skippedCalendars,
  };
}

function addEvent(calendarApp, input) {
  const calendar = selectedCalendar(calendarApp, input);
  ensureWritable(calendar);
  const properties = {
    summary: input.title,
    startDate: dateFromInput(input.start, input.date_kind),
    endDate: dateFromInput(input.end_exclusive, input.date_kind),
    alldayEvent: input.date_kind === "all_day",
  };
  if (input.notes) properties.description = input.notes;
  if (input.location) properties.location = input.location;
  if (input.url) properties.url = input.url;
  const event = calendarApp.Event(properties);
  calendar.events.push(event);
  return { event: serializeEvent(event, calendar) };
}

function updateEvent(calendarApp, input) {
  const record = findEvent(calendarApp, input.calendar_id, input.id);
  ensureEditableEvent(record.event, record.calendar);
  const event = record.event;
  if (Object.prototype.hasOwnProperty.call(input, "title")) {
    event.summary = input.title;
  }
  if (Object.prototype.hasOwnProperty.call(input, "notes")) {
    event.description = input.notes;
  }
  if (Object.prototype.hasOwnProperty.call(input, "location")) {
    event.location = input.location;
  }
  if (Object.prototype.hasOwnProperty.call(input, "url")) {
    event.url = input.url;
  }
  if (Object.prototype.hasOwnProperty.call(input, "start")) {
    const currentKind = Boolean(event.alldayEvent()) ? "all_day" : "timed";
    if (currentKind !== input.date_kind) {
      throw new Error(
        `Cannot switch this event from ${currentKind} to ${input.date_kind}; create a replacement or change it in Calendar.`,
      );
    }
    event.startDate = dateFromInput(input.start, input.date_kind);
    event.endDate = dateFromInput(input.end_exclusive, input.date_kind);
  }
  return { event: serializeEvent(event, record.calendar) };
}

function run(argv) {
  const input = JSON.parse(argv[0]);
  const calendarApp = Application("Calendar");

  switch (input.action) {
    case "list_calendars":
      return JSON.stringify(listCalendars(calendarApp));
    case "search_events":
      return JSON.stringify(searchEvents(calendarApp, input));
    case "get_event": {
      const record = findEvent(calendarApp, input.calendar_id, input.id);
      return JSON.stringify({ event: serializeEvent(record.event, record.calendar) });
    }
    case "add_event":
      return JSON.stringify(addEvent(calendarApp, input));
    case "update_event":
      return JSON.stringify(updateEvent(calendarApp, input));
    case "delete_event": {
      const record = findEvent(calendarApp, input.calendar_id, input.id);
      ensureEditableEvent(record.event, record.calendar);
      const deleted = serializeEvent(record.event, record.calendar);
      calendarApp.delete(record.event);
      return JSON.stringify({ deleted });
    }
    default:
      throw new Error("Unsupported calendar action.");
  }
}
