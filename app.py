"""
Bin Collection ICS Calendar Feed
Serves a .ics calendar at /bins.ics with collection events and 7pm-evening-before reminders.
"""

from flask import Flask, Response
from icalendar import Calendar, Event, Alarm, vText, vDatetime
from datetime import datetime, timedelta, date, time
import requests
from dateutil.parser import parse as dateparse
import hashlib
import re

app = Flask(__name__)

API_URL = "https://api.southglos.gov.uk/wastecomp/GetCollectionDetails?uprn=540378"

# Colour/category map (purely cosmetic for calendar apps that support it)
SERVICE_COLOURS = {
    "Refuse": "RED",
    "Food": "GREEN",
    "Recycling": "BLUE",
    "Garden": "CYAN",
}

MONTHS_AHEAD = 6


def parse_interval_days(schedule_description: str):
    """
    Return repeat interval in days from a schedule description string.
    Returns 7 for weekly, 14 for fortnightly, None if unrecognised.
    """
    desc = schedule_description.lower()
    if "fortnight" in desc or "every 2 week" in desc or "every other week" in desc:
        return 14
    if "every week" in desc or "weekly" in desc or "week" in desc:
        return 7
    # Fallback: look for "every N weeks"
    m = re.search(r"every\s+(\d+)\s+week", desc)
    if m:
        return int(m.group(1)) * 7
    return None


def make_uid(service: str, event_date: date) -> str:
    key = f"{service}-{event_date.isoformat()}@southglos-bins"
    return hashlib.md5(key.encode()).hexdigest() + "@bins"


def build_calendar(services: list[dict]) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Bin Collection Feed//southglos//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("X-WR-CALNAME", "Bin Collections")
    cal.add("X-WR-TIMEZONE", "Europe/London")

    today = date.today()
    end_date = today + timedelta(days=MONTHS_AHEAD * 30)

    for service in services:
        name = service.get("hso_servicename", "Unknown")
        next_collection_str = service.get("hso_nextcollection")
        schedule_desc = service.get("hso_scheduledescription", "")

        if not next_collection_str:
            continue

        try:
            next_dt = dateparse(next_collection_str).date()
        except (ValueError, TypeError):
            continue

        interval = parse_interval_days(schedule_desc)

        # Build list of dates
        collection_dates = []
        current = next_dt
        while current <= end_date:
            if current >= today:
                collection_dates.append(current)
            if interval is None:
                break  # Only one known date, no repeat info
            current += timedelta(days=interval)

        for cdate in collection_dates:
            event = Event()
            event.add("summary", f"♻ {name} collection")
            event.add("dtstart", cdate)
            event.add("dtend", cdate + timedelta(days=1))
            event.add("description", vText(
                f"Service: {name}\nSchedule: {schedule_desc}"
            ))
            event.add("uid", make_uid(name, cdate))

            # VALARM: 7pm the evening before
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", vText(f"Put out {name} bin tonight!"))
            # Trigger: day before at 19:00 local
            # We express as absolute time offset: -17h from midnight = 19:00 prev day
            alarm.add("trigger", timedelta(hours=-17))
            event.add_component(alarm)

            cal.add_component(event)

    return cal.to_ical()


@app.route("/bins.ics")
def bins_ics():
    try:
        resp = requests.get(API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        services = data.get("value", [])
    except Exception as e:
        return Response(f"Error fetching bin data: {e}", status=502, mimetype="text/plain")

    ical_bytes = build_calendar(services)

    return Response(
        ical_bytes,
        status=200,
        mimetype="text/calendar",
        headers={
            "Content-Disposition": "inline; filename=bins.ics",
            "Cache-Control": "no-cache",
        },
    )


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=10000, debug=False)
