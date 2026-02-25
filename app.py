"""
Bin Collection ICS Calendar Feed + Visual Webpage
Serves a .ics calendar at /bins.ics with merged same-day collection events,
and a visual webpage at / showing upcoming collections.
"""

from flask import Flask, Response, render_template_string
from icalendar import Calendar, Event, Alarm, vText
from datetime import datetime, timedelta, date
import requests
from dateutil.parser import parse as dateparse
import hashlib
import re
from collections import defaultdict
import os

app = Flask(__name__)

UPRN = os.environ.get("UPRN")
if not UPRN:
    raise RuntimeError("UPRN environment variable is not set")
API_URL = f"https://api.southglos.gov.uk/wastecomp/GetCollectionDetails?uprn={UPRN}"

MONTHS_AHEAD = 6

# Emoji and colour for each service
SERVICE_META = {
    "Refuse":    {"emoji": "🗑️",  "colour": "#E72696", "label": "Refuse"},
    "Food":      {"emoji": "🟢",  "colour": "#85FFBE", "label": "Food"},
    "Recycling": {"emoji": "♻️",  "colour": "#F99B1D", "label": "Recycling"},
    "Garden":    {"emoji": "🌿",  "colour": "#FACF31", "label": "Garden"},
}


def parse_interval_days(schedule_description: str):
    desc = schedule_description.lower()
    if "fortnight" in desc or "every 2 week" in desc or "every other week" in desc:
        return 14
    if "every week" in desc or "weekly" in desc or "week" in desc:
        return 7
    m = re.search(r"every\s+(\d+)\s+week", desc)
    if m:
        return int(m.group(1)) * 7
    return None


def make_uid(services_key: str, event_date: date) -> str:
    key = f"{services_key}-{event_date.isoformat()}@southglos-bins"
    return hashlib.md5(key.encode()).hexdigest() + "@bins"


def get_collections(services: list[dict]) -> dict:
    """
    Returns a dict of {date: [service_name, ...]} sorted by date.
    """
    today = date.today()
    end_date = today + timedelta(days=MONTHS_AHEAD * 30)
    day_map = defaultdict(list)

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

        current = next_dt
        while current <= end_date:
            if current >= today:
                day_map[current].append(name)
            if interval is None:
                break
            current += timedelta(days=interval)

    return dict(sorted(day_map.items()))


def build_calendar(day_map: dict) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Bin Collection Feed//southglos//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("X-WR-CALNAME", "Bin Collections")
    cal.add("X-WR-TIMEZONE", "Europe/London")

    for cdate, service_names in day_map.items():
        # Sort services in preferred order
        ORDER = ["Garden", "Refuse", "Recycling", "Food"]
        service_names = sorted(set(service_names), key=lambda s: ORDER.index(s) if s in ORDER else 99)

        # Build a human-readable summary
        if len(service_names) == 1:
            summary = f"{service_names[0]} collection"
        elif len(service_names) == 2:
            summary = f"{service_names[0]} & {service_names[1]} collection"
        else:
            summary = f"{', '.join(service_names[:-1])} & {service_names[-1]} collection"

        # Leading dot: black for refuse, green for garden, nothing otherwise
        if "Refuse" in service_names:
            dot = "⚫ "
        elif "Garden" in service_names:
            dot = "🟢 "
        else:
            dot = ""
        summary = f"{dot}{summary}"

        event = Event()
        event.add("summary", summary)
        event.add("dtstart", cdate)
        event.add("dtend", cdate + timedelta(days=1))
        event.add("description", vText(
            f"Collections: {', '.join(service_names)}"
        ))
        event.add("uid", make_uid("-".join(service_names), cdate))

        # VALARM: 10pm the evening before
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", vText(f"Put out bins tonight! ({', '.join(service_names)})"))
        alarm.add("trigger", timedelta(hours=-2))
        event.add_component(alarm)

        cal.add_component(event)

    return cal.to_ical()


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Bin Collections</title>
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;900&family=Barlow:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --mint:   #85FFBE;
      --pink:   #E72696;
      --orange: #F99B1D;
      --yellow: #FACF31;
      --black:  #111111;
      --white:  #ffffff;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--white);
      color: var(--black);
      font-family: 'Barlow', sans-serif;
      padding: 2rem;
      max-width: 600px;
      margin: 0 auto;
    }
    h1 {
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 900;
      font-size: 2.5rem;
      text-transform: uppercase;
      margin-bottom: 0.3rem;
    }
    .subtitle {
      font-size: 0.95rem;
      color: #888;
      margin-bottom: 2rem;
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .collection {
      border: 3px solid var(--black);
      padding: 1.2rem 1.5rem;
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      gap: 1.5rem;
    }
    .collection.next {
      border-color: var(--pink);
      background: #fff0f8;
    }
    .next-label {
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 700;
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--pink);
      margin-bottom: 0.5rem;
    }
    .collection-date {
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 900;
      font-size: 1.4rem;
      min-width: 90px;
      line-height: 1.1;
    }
    .collection-date .day {
      font-size: 2rem;
    }
    .collection-services {
      flex: 1;
    }
    .collection-services .summary {
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 700;
      font-size: 1.2rem;
      text-transform: uppercase;
    }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-top: 0.5rem;
    }
    .tag {
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 700;
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 1px;
      padding: 0.2rem 0.7rem;
      border: 2px solid var(--black);
    }
    .tag-Refuse    { background: #333333; color: #ffffff; }
    .tag-Food      { background: #ffd6ec; color: #111111; }
    .tag-Recycling { background: #ffe8c0; color: #111111; }
    .tag-Garden    { background: #85FFBE; color: #111111; }

    .ics-link {
      display: inline-block;
      margin-top: 2rem;
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 700;
      font-size: 1rem;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--black);
      border: 3px solid var(--black);
      padding: 0.8rem 1.5rem;
      text-decoration: none;
      transition: background 0.15s;
    }
    .ics-link:hover { background: var(--mint); }
    .error {
      color: var(--pink);
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 700;
      font-size: 1.2rem;
      text-transform: uppercase;
    }
  </style>
</head>
<body>
  <h1>Bin Collections</h1>
  <p class="subtitle">Ted's House — upcoming schedule</p>

  {% if error %}
    <p class="error">{{ error }}</p>
  {% else %}
    {% for i, (cdate, services) in enumerate(collections.items()) %}
      {% if i < 8 %}
        {% if i == 0 %}<div class="next-label">↓ Next collection</div>{% endif %}
        <div class="collection {% if i == 0 %}next{% endif %}">
          <div class="collection-date">
            <div class="month">{{ cdate.strftime('%b') }}</div>
            <div class="day">{{ cdate.strftime('%-d') }}</div>
            <div class="weekday">{{ cdate.strftime('%a') }}</div>
          </div>
          <div class="collection-services">
            <div class="summary">
              {% if services|length == 1 %}
                {{ services[0] }} collection
              {% elif services|length == 2 %}
                {{ services[0] }} &amp; {{ services[1] }} collection
              {% else %}
                {{ services[:-1]|join(', ') }} &amp; {{ services[-1] }} collection
              {% endif %}
            </div>
            <div class="tags">
              {% for s in services %}
                <span class="tag tag-{{ s }}">{{ s }}</span>
              {% endfor %}
            </div>
          </div>
        </div>
      {% endif %}
    {% endfor %}
    <a class="ics-link" href="/bins.ics">📅 Subscribe to calendar feed</a>
  {% endif %}
</body>
</html>
"""


@app.route("/")
def index():
    try:
        resp = requests.get(API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        services = data.get("value", [])
        day_map = get_collections(services)
        # Convert to sorted list of (date, [services]) for template
        ORDER = ["Garden", "Refuse", "Recycling", "Food"]
        collections = {k: sorted(set(v), key=lambda s: ORDER.index(s) if s in ORDER else 99) for k, v in day_map.items()}
        return render_template_string(HTML_TEMPLATE, collections=collections, error=None, enumerate=enumerate)
    except Exception as e:
        return render_template_string(HTML_TEMPLATE, collections={}, error=str(e), enumerate=enumerate)


@app.route("/bins.ics")
def bins_ics():
    try:
        resp = requests.get(API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        services = data.get("value", [])
        day_map = get_collections(services)
    except Exception as e:
        return Response(f"Error fetching bin data: {e}", status=502, mimetype="text/plain")

    ical_bytes = build_calendar(day_map)

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
