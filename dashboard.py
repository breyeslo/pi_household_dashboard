#!/usr/bin/env python3
"""
Raspberry Pi + Pimoroni Inky e-ink dashboard.
https://github.com/breyeslo/pi_household_dashboard  ·  MIT License

    +--------------------------------------------------+
    | Home                          Mon, Sep 28  9:00 AM |
    +------------------------+-------------------------+
    |  WEATHER               |  NEXT 3 DAYS (calendar) |
    +------------------------+-------------------------+
    |  NEWS (RSS)            |  TO-DO (Google Keep)    |
    +------------------------+-------------------------+

    python dashboard.py                    # fetch everything and draw on the Inky
    python dashboard.py --preview out.png  # fetch and save a PNG instead (any computer)

Runs once and exits; systemd (see install.sh) runs it every hour.
If a source fails, the last good copy from cache.json is shown and marked "cached".
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import logging
from pathlib import Path

import feedparser
import requests
import yaml
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
CACHE_FILE = BASE / "cache.json"
TOKEN_FILE = BASE / "token.json"
LAST_RENDER = BASE / "last.png"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]
UA = "pi_household_dashboard/1.0"

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
ACCENT = (220, 0, 0)  # red on Impression / red wHAT; yellow on yellow boards

log = logging.getLogger("dashboard")


# --------------------------------------------------------------------------- utils
def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_config() -> dict:
    with open(BASE / "config.yaml") as f:
        return yaml.safe_load(f) or {}


def fmt_clock(t: dt.datetime, h24: bool) -> str:
    return t.strftime("%H:%M") if h24 else t.strftime("%I:%M %p").lstrip("0")


def fmt_event_time(t: dt.datetime, h24: bool) -> str:
    if h24:
        return t.strftime("%H:%M")
    s = t.strftime("%I:%M%p").lstrip("0").lower()  # 9:00am
    return s.replace(":00", "")                     # 9am, 2:30pm


WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Light rain", 63: "Rain",
    65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Showers",
    82: "Heavy showers", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorms", 96: "T-storms, hail", 99: "T-storms, hail",
}


# --------------------------------------------------------------------------- data sources
def fetch_weather(cfg: dict) -> dict:
    """Open-Meteo: free, no API key."""
    loc = cfg["location"]
    imperial = cfg.get("units", "imperial") == "imperial"
    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code,"
                   "wind_speed_10m,relative_humidity_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
        "timezone": "auto",
        "forecast_days": 3,
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params,
                     timeout=20, headers={"User-Agent": UA})
    r.raise_for_status()
    j = r.json()
    c, d = j["current"], j["daily"]
    return {
        "temp": c["temperature_2m"],
        "feels": c["apparent_temperature"],
        "code": c["weather_code"],
        "wind": c["wind_speed_10m"],
        "wind_unit": "mph" if imperial else "km/h",
        "humidity": c["relative_humidity_2m"],
        "daily": [
            {
                "date": d["time"][i],
                "hi": d["temperature_2m_max"][i],
                "lo": d["temperature_2m_min"][i],
                "code": d["weather_code"][i],
                "pop": d["precipitation_probability_max"][i],
            }
            for i in range(len(d["time"]))
        ],
    }


def fetch_news(cfg: dict) -> list[str]:
    """Top stories from each feed, interleaved (feed order = the editors' ranking)."""
    ncfg = cfg.get("news", {})
    feeds = ncfg.get("feeds", [])
    limit = ncfg.get("max_items", 6)
    per_feed = []
    for url in feeds:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": UA})
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            per_feed.append([e.get("title", "").strip() for e in parsed.entries[:limit]])
        except Exception as e:  # one bad feed shouldn't blank the panel
            log.warning("feed %s failed: %s", url, e)
    if not per_feed:
        raise RuntimeError("all news feeds failed")
    out, seen = [], set()
    for i in range(limit):
        for items in per_feed:
            if i < len(items) and items[i] and items[i].lower() not in seen:
                seen.add(items[i].lower())
                out.append(items[i])
    return out[:limit]


@functools.lru_cache(maxsize=1)
def google_creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "token.json missing: run setup_google.py on a computer with a browser, "
            "then copy token.json next to dashboard.py")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), GOOGLE_SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError("token.json has no refresh token: re-run setup_google.py")
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def _cal_window(ccfg: dict):
    days = ccfg.get("days", 3)
    now = dt.datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=days)
    buckets = {(start + dt.timedelta(days=i)).date(): [] for i in range(days)}
    return now, start, end, buckets


def _add_event(buckets, now, title, start, end, all_day):
    """start/end are dates (all-day, end exclusive) or aware datetimes."""
    if all_day:
        day = start
        while day < end:
            if day in buckets:
                buckets[day].append({"all_day": True, "start": "", "title": title})
            day += dt.timedelta(days=1)
    elif end >= now and start.date() in buckets:
        buckets[start.date()].append({"all_day": False, "start": start.isoformat(), "title": title})


def _sorted(buckets):
    return {
        day.isoformat(): sorted(evs, key=lambda x: (not x["all_day"], x["start"]))
        for day, evs in buckets.items()
    }


def fetch_calendar(cfg: dict) -> dict:
    """Returns {"YYYY-MM-DD": [ {all_day, start, title}, ... ], ...} for the next N days."""
    ccfg = cfg.get("calendar", {})
    if ccfg.get("source", "google") == "ical":
        return _calendar_ical(ccfg)
    return _calendar_google(ccfg)


def _calendar_ical(ccfg: dict) -> dict:
    """Any calendar's private/public iCal (.ics) link. No API keys, no OAuth."""
    import icalendar
    import recurring_ical_events

    now, start, end, buckets = _cal_window(ccfg)
    local = now.tzinfo
    urls = ccfg.get("ical_urls", [])
    if not urls:
        raise RuntimeError("calendar.source is 'ical' but calendar.ical_urls is empty")

    def as_local(v):
        if isinstance(v, dt.datetime):
            return (v if v.tzinfo else v.replace(tzinfo=local)).astimezone(local)
        return v

    for url in urls:
        url = url.replace("webcal://", "https://", 1)
        r = requests.get(url, timeout=30, headers={"User-Agent": UA})
        r.raise_for_status()
        cal = icalendar.Calendar.from_ical(r.content)
        for ev in recurring_ical_events.of(cal).between(start, end):
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            title = str(ev.get("SUMMARY", "(no title)"))
            s = as_local(ev.decoded("DTSTART"))
            e = as_local(ev.decoded("DTEND")) if ev.get("DTEND") else None
            all_day = not isinstance(s, dt.datetime)
            if e is None:
                e = s + dt.timedelta(days=1) if all_day else s
            _add_event(buckets, now, title, s, e, all_day)
    return _sorted(buckets)


def _calendar_google(ccfg: dict) -> dict:
    from googleapiclient.discovery import build

    svc = build("calendar", "v3", credentials=google_creds(), cache_discovery=False)
    now, start, end, buckets = _cal_window(ccfg)

    for cal_id in ccfg.get("calendar_ids", ["primary"]):
        res = svc.events().list(
            calendarId=cal_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100,
        ).execute()
        for ev in res.get("items", []):
            if ev.get("status") == "cancelled":
                continue
            title = ev.get("summary", "(no title)")
            s, e = ev["start"], ev["end"]
            if "date" in s:  # all-day, possibly multi-day
                _add_event(buckets, now, title, dt.date.fromisoformat(s["date"]),
                           dt.date.fromisoformat(e["date"]), True)
            else:
                _add_event(buckets, now, title,
                           dt.datetime.fromisoformat(s["dateTime"]).astimezone(),
                           dt.datetime.fromisoformat(e["dateTime"]).astimezone(), False)
    return _sorted(buckets)


def fetch_todo(cfg: dict) -> list[str]:
    tcfg = cfg.get("todo", {})
    source = tcfg.get("source", "keep")
    if source == "tasks":
        return _todo_google_tasks(tcfg)
    return _todo_google_keep(tcfg)


def _todo_google_keep(tcfg: dict) -> list[str]:
    """Google Keep has no official API for personal accounts; this uses gkeepapi."""
    import gkeepapi

    k = tcfg["keep"]
    keep = gkeepapi.Keep()
    keep.authenticate(k["email"], k["master_token"])
    wanted = k.get("note_title", "To Do").strip().lower()
    for note in keep.all():
        if note.trashed or note.title.strip().lower() != wanted:
            continue
        if isinstance(note, gkeepapi.node.List):  # checkbox note
            return [i.text.strip() for i in note.unchecked if i.text.strip()]
        return [ln.strip(" -*•\t") for ln in note.text.splitlines() if ln.strip(" -*•\t")]
    raise RuntimeError(f"no Keep note titled {k.get('note_title')!r}")


def _todo_google_tasks(tcfg: dict) -> list[str]:
    from googleapiclient.discovery import build

    svc = build("tasks", "v1", credentials=google_creds(), cache_discovery=False)
    res = svc.tasks().list(
        tasklist=tcfg.get("tasks", {}).get("tasklist", "@default"),
        showCompleted=False, showHidden=False, maxResults=50,
    ).execute()
    items = sorted(res.get("items", []), key=lambda t: t.get("position", ""))
    return [t["title"].strip() for t in items if t.get("title", "").strip()]


def gather(cfg: dict) -> dict:
    cache = load_json(CACHE_FILE)
    data = {"now": dt.datetime.now().astimezone(), "stale": set()}
    jobs = {"weather": fetch_weather, "news": fetch_news}
    if cfg.get("calendar", {}).get("enabled", True):
        jobs["calendar"] = fetch_calendar
    if cfg.get("todo", {}).get("enabled", True):
        jobs["todo"] = fetch_todo
    for key, fn in jobs.items():
        try:
            data[key] = fn(cfg)
            cache[key] = data[key]
            log.info("%s: ok", key)
        except Exception:
            log.exception("%s failed; falling back to cache", key)
            data[key] = cache.get(key)
            if data[key] is not None:
                data["stale"].add(key)
    CACHE_FILE.write_text(json.dumps(cache, indent=1))
    return data


# --------------------------------------------------------------------------- drawing
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF",
             "/Library/Fonts", str(Path.home() / "Library/Fonts"), "C:/Windows/Fonts"]


def _font(name: str, size: int):
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        log.warning("font %s not found, using Pillow default", name)
        return ImageFont.load_default()


def load_fonts(scale: float) -> dict:
    px = lambda n: max(8, round(n * scale))  # noqa: E731
    reg, bold = "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"
    return {
        "small": _font(reg, px(14)),
        "small_b": _font(bold, px(14)),
        "body": _font(reg, px(17)),
        "bold": _font(bold, px(17)),
        "head": _font(bold, px(18)),
        "clock": _font(bold, px(24)),
        "big": _font(bold, px(52)),
    }


def lh(font) -> int:
    return round(getattr(font, "size", 11) * 1.22)


def wrap(d: ImageDraw.ImageDraw, text: str, font, width: float, first_width: float | None = None):
    """Word-wrap to pixel widths. first_width lets line 1 be shorter (room for a prefix)."""
    lines, cur = [], ""
    limit = lambda: first_width if (first_width is not None and not lines) else width  # noqa: E731
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= limit():
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while d.textlength(word, font=font) > limit():  # hard-break very long words
            cut = len(word)
            while cut > 1 and d.textlength(word[:cut], font=font) > limit():
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def with_ellipsis(d, text: str, font, width: float) -> str:
    text = text.rstrip()
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1].rstrip()
    return text + "…"


class Column:
    """Draws wrapped text top-to-bottom inside a box and knows when it's full."""

    def __init__(self, d, x, y, width, bottom):
        self.d, self.x, self.y, self.w, self.bottom = d, x, y, width, bottom

    def room(self, font, lines: int = 1) -> bool:
        return self.y + lh(font) * lines <= self.bottom

    def gap(self, px: int):
        self.y += px

    def line(self, text, font, fill=BLACK, prefix="", prefix_font=None,
             prefix_fill=None, hang=True, indent=0, max_lines=2) -> bool:
        """Draw one item. hang=True indents wrapped lines under the text, not the prefix.
        Returns False if not even one line fits."""
        if not self.room(font):
            return False
        pfont = prefix_font or font
        pw = self.d.textlength(prefix, font=pfont) if prefix else 0
        full = self.w - indent
        body_w = full - pw if hang else full
        lines = wrap(self.d, text, font, body_w, first_width=full - pw)
        limit = max(1, min(max_lines, (self.bottom - self.y) // lh(font)))
        if len(lines) > limit:
            lines = lines[:limit]
            lines[-1] = with_ellipsis(self.d, lines[-1], font, full - pw if limit == 1 else body_w)
        if prefix:
            self.d.text((self.x + indent, self.y), prefix, font=pfont, fill=prefix_fill or fill)
        for i, ln in enumerate(lines):
            x = self.x + indent + (pw if (i == 0 or hang) else 0)
            self.d.text((x, self.y), ln, font=font, fill=fill)
            self.y += lh(font)
        return True


def panel_heading(d, box, title, F, pad, stale=False) -> int:
    x0, y0, x1, _ = box
    y = y0 + pad
    d.text((x0 + pad, y), title.upper(), font=F["head"], fill=ACCENT)
    if stale:
        d.text((x1 - pad, y + lh(F["head"]) - lh(F["small"])), "cached",
               font=F["small"], fill=BLACK, anchor="ra")
    y += lh(F["head"]) + 2
    d.line([(x0 + pad, y), (x1 - pad, y)], fill=ACCENT, width=max(1, pad // 5))
    return y + pad // 2 + 2


def unavailable(d, box, y, F, pad):
    Column(d, box[0] + pad, y, box[2] - box[0] - 2 * pad, box[3] - pad).line(
        "Couldn't load. Will retry next hour.", F["small"])


def draw_weather(d, box, w, F, pad, cfg, stale):
    x0, _, x1, y1 = box
    name = cfg.get("location", {}).get("name", "")
    y = panel_heading(d, box, f"Weather · {name}" if name else "Weather", F, pad, stale)
    if not w:
        return unavailable(d, box, y, F, pad)

    temp = f"{round(w['temp'])}°"
    d.text((x0 + pad, y), temp, font=F["big"], fill=BLACK)
    tx = x0 + pad + d.textlength(temp, font=F["big"]) + pad
    col = Column(d, tx, y + round(lh(F["big"]) * 0.08), x1 - pad - tx, y1 - pad)
    col.line(WMO.get(w["code"], "—"), F["bold"], max_lines=1)
    col.line(f"Feels like {round(w['feels'])}°", F["body"], max_lines=1)
    col.line(f"Wind {round(w['wind'])} {w['wind_unit']} · {round(w['humidity'])}% RH",
             F["small"], max_lines=1)
    y = max(col.y, y + lh(F["big"])) + pad // 2

    days = w["daily"][:3]
    cw = (x1 - x0 - 2 * pad) / max(1, len(days))
    for i, day in enumerate(days):
        date = dt.date.fromisoformat(day["date"])
        c = Column(d, x0 + pad + i * cw, y, cw - pad // 2, y1 - pad)
        c.line("Today" if i == 0 else date.strftime("%a"), F["bold"], fill=ACCENT, max_lines=1)
        c.line(f"{round(day['hi'])}° / {round(day['lo'])}°", F["body"], max_lines=1)
        c.line(WMO.get(day["code"], "—"), F["small"], max_lines=1)
        if day.get("pop") is not None:
            c.line(f"{day['pop']}% precip", F["small"], max_lines=1)


def draw_calendar(d, box, cal, F, pad, cfg, today, stale):
    x0, _, x1, y1 = box
    ndays = cfg.get("calendar", {}).get("days", 3)
    y = panel_heading(d, box, f"Next {ndays} days", F, pad, stale)
    if cal is None:
        return unavailable(d, box, y, F, pad)

    days = [(k, v) for k, v in cal.items() if dt.date.fromisoformat(k) >= today][:ndays]
    if not days:
        return unavailable(d, box, y, F, pad)
    h24 = cfg.get("clock_24h", False)
    cw = (x1 - x0 - 2 * pad) / len(days)
    for i, (iso, events) in enumerate(days):
        date = dt.date.fromisoformat(iso)
        cx = x0 + pad + i * cw
        if i:
            d.line([(cx - pad // 3, y), (cx - pad // 3, y1 - pad)], fill=BLACK, width=1)
        delta = (date - today).days
        label = {0: "Today", 1: "Tomorrow"}.get(delta, f"{date:%a} {date.day}")
        col = Column(d, cx, y, cw - pad // 2 - 2, y1 - pad)
        col.line(label, F["bold"], fill=ACCENT, max_lines=1)
        col.gap(2)
        if not events:
            col.line("Nothing scheduled", F["small"], max_lines=2)
            continue
        for n, ev in enumerate(events):
            left = len(events) - n
            if left > 1 and not col.room(F["small"], 2):
                col.line(f"+{left} more", F["small_b"], max_lines=1)
                break
            when = "All day" if ev["all_day"] else fmt_event_time(
                dt.datetime.fromisoformat(ev["start"]), h24)
            if not col.line(ev["title"], F["small"], prefix=when + " ",
                            prefix_font=F["small_b"], hang=False, max_lines=2):
                break
            col.gap(3)


def draw_list(d, box, title, items, F, pad, stale, bullet, font_key, max_lines):
    x0, _, x1, y1 = box
    y = panel_heading(d, box, title, F, pad, stale)
    if items is None:
        return unavailable(d, box, y, F, pad)
    col = Column(d, x0 + pad, y, x1 - x0 - 2 * pad, y1 - pad)
    font = F[font_key]
    if not items:
        col.line("All clear!", font)
        return
    for n, item in enumerate(items):
        left = len(items) - n
        if left > 1 and not col.room(font, 2):
            col.line(f"+{left} more", F["small_b"], max_lines=1)
            break
        if not col.line(item, font, prefix=bullet, prefix_fill=ACCENT, max_lines=max_lines):
            break
        col.gap(pad // 3)


def render(data: dict, size: tuple[int, int], cfg: dict) -> Image.Image:
    W, H = size
    scale = min(W / 800, H / 480)
    F = load_fonts(scale)
    pad = max(4, round(10 * scale))
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    # Header with clock + date in the top-right corner
    now = data["now"]
    header_h = round(lh(F["clock"]) + pad * 1.6)
    d.rectangle([0, 0, W, header_h], fill=BLACK)
    d.text((pad, header_h / 2), cfg.get("title", "Home"), font=F["clock"], fill=WHITE, anchor="lm")
    stamp = f"{now:%a, %b} {now.day}   {fmt_clock(now, cfg.get('clock_24h', False))}"
    d.text((W - pad, header_h / 2), stamp, font=F["clock"], fill=WHITE, anchor="rm")

    # Quadrants
    mx, my = W // 2, header_h + (H - header_h) // 2
    lw = max(1, round(2 * scale))
    d.line([(mx, header_h), (mx, H)], fill=BLACK, width=lw)
    d.line([(0, my), (W, my)], fill=BLACK, width=lw)
    stale = data["stale"]
    today = now.date()

    draw_weather(d, (0, header_h, mx, my), data.get("weather"), F, pad, cfg, "weather" in stale)
    draw_calendar(d, (mx, header_h, W, my), data.get("calendar"), F, pad, cfg, today,
                  "calendar" in stale)
    draw_list(d, (0, my, mx, H), "News", data.get("news"), F, pad, "news" in stale,
              "• ", "small", max_lines=2)
    draw_list(d, (mx, my, W, H), cfg.get("todo", {}).get("heading", "To do"),
              data.get("todo"), F, pad, "todo" in stale, "☐ ", "body", max_lines=2)
    return img


# --------------------------------------------------------------------------- output
def show_on_inky(display, img: Image.Image, cfg: dict):
    try:  # Inky Impression (multi-colour): takes RGB and dithers itself
        display.set_image(img, saturation=cfg.get("saturation", 0.6))
    except TypeError:  # pHAT / wHAT: needs a paletted image (0=white, 1=black, 2=colour)
        accent = [0, 0, 0] if getattr(display, "colour", "") == "black" else [255, 0, 0]
        pal = Image.new("P", (1, 1))
        pal.putpalette([255, 255, 255, 0, 0, 0] + accent + [0] * (768 - 9))
        display.set_image(img.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE))
    try:
        display.set_border(display.WHITE)
    except Exception:
        pass
    display.show()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", metavar="PNG", help="save to a PNG instead of drawing on the Inky")
    ap.add_argument("--size", default="800x480", help="preview size, e.g. 800x480 or 600x448")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    display = None
    if args.preview:
        size = tuple(int(v) for v in args.size.lower().split("x"))
    else:
        from inky.auto import auto
        display = auto(ask_user=False, verbose=True)
        size = tuple(display.resolution)

    rotate = cfg.get("rotate", 0)
    if rotate in (90, 270):
        size = (size[1], size[0])

    img = render(gather(cfg), size, cfg)
    img.save(LAST_RENDER)
    if rotate:
        img = img.rotate(rotate, expand=True)

    if display:
        show_on_inky(display, img, cfg)
        log.info("display updated (%dx%d)", *size)
    else:
        img.save(args.preview)
        log.info("preview saved to %s", args.preview)


if __name__ == "__main__":
    main()
