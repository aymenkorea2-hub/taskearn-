"""Button-by-button activity log, shown to admins only.

Every button a customer taps is stored as a short label. Labels are then
grouped into lines: a pause of GAP_MIN minutes starts a NEW line with its own
start date, one line holds at most STEPS_PER_LINE steps, and only the newest
LINES_SHOWN lines are displayed.

    24 Aug 09:12 UTC — deposit > BEP20 > main menu
    24 Aug 09:20 UTC — shop > Netflix Premium > place order

All timestamps are UTC, exactly like the rest of the bot.
"""
import re
from datetime import datetime, timedelta, timezone

GAP_MIN = 2              # a 2-minute pause opens a new line
STEPS_PER_LINE = 20      # max buttons kept in one line
LINES_SHOWN = 5          # max lines displayed on the user card
ACTIVE_MIN = 2           # "active" while the last tap is newer than this
KEEP_ROWS = 400          # per-user rows kept in the database

# Fixed callback data -> what the button says, in plain words.
STATIC_LABELS = {
    "main": "main menu",
    "shop": "shop",
    "deposit": "deposit",
    "dep:usdt": "BEP20",
    "dep:copy": "copy address",
    "dep:check": "check payment",
    "dep:txid": "I paid",
    "profile": "profile",
    "terms": "terms",
    "support": "support",
    "promo": "promo code",
}

# Prefixed callback data -> label template. {t} = product title, {n} = number.
PREFIX_LABELS = (
    ("prod:", "{t}"),
    ("order:", "place order"),
    ("qty:", "quantity {n}"),
    ("qtyc:", "custom quantity"),
    ("buybal:", "pay with balance"),
    ("buydir:", "pay with USDT"),
    ("promo:", "promo code"),
    ("req:start:", "order request"),
    ("notify:", "notify me"),
    ("unnotify:", "cancel notify"),
)

_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def label_for(data: str | None, product_title: str | None = None) -> str | None:
    """Human label for one callback, or None when it must not be logged."""
    key = (data or "").strip()
    if not key or key == "noop" or key.startswith("adm:"):
        return None            # admin panel and no-op taps are never logged
    if key in STATIC_LABELS:
        return STATIC_LABELS[key]
    for prefix, template in PREFIX_LABELS:
        if key.startswith(prefix):
            parts = key.split(":")
            number = parts[2] if len(parts) > 2 else (
                parts[1] if len(parts) > 1 else "")
            label = template.format(t=product_title or "product", n=number)
            return label.strip()[:64]
    return key.split(":")[0][:64]


def needs_product_title(data: str | None) -> int | None:
    """Product id whose title is needed to label this callback, if any."""
    key = (data or "").strip()
    if not key.startswith("prod:"):
        return None
    tail = key.split(":")[1] if ":" in key else ""
    return int(tail) if tail.isdigit() else None


def parse_ts(value) -> datetime | None:
    """SQLite UTC text (or a datetime) -> aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    match = _TS.match(str(value or ""))
    if not match:
        return None
    year, month, day, hour, minute, second = (int(x) for x in match.groups())
    return datetime(year, month, day, hour, minute, second,
                    tzinfo=timezone.utc)


def group_lines(rows, gap_min: int = GAP_MIN,
                steps_per_line: int = STEPS_PER_LINE,
                lines_shown: int = LINES_SHOWN) -> list[dict]:
    """Groups raw log rows into display lines, newest line first.

    `rows` are dict-like with "label" and "created_at" in any time order.
    """
    events = []
    for row in rows or []:
        moment = parse_ts(row["created_at"])
        label = str(row["label"] or "").strip()
        if moment is None or not label:
            continue
        events.append((moment, label))
    events.sort(key=lambda item: item[0])

    lines: list[dict] = []
    gap = timedelta(minutes=gap_min)
    previous = None
    for moment, label in events:
        new_line = (
            not lines
            or previous is None
            or moment - previous >= gap
            or len(lines[-1]["steps"]) >= steps_per_line
        )
        if new_line:
            lines.append({"started_at": moment, "last_at": moment,
                          "steps": [label]})
        else:
            lines[-1]["steps"].append(label)
            lines[-1]["last_at"] = moment
        previous = moment

    lines.reverse()                       # newest line on top
    return lines[:lines_shown]


def stamp(moment: datetime | None) -> str:
    return moment.strftime("%d %b %H:%M UTC") if moment else "—"


def render_line(line: dict) -> str:
    """'24 Aug 09:12 UTC : shop > Netflix > place order'"""
    return f"{stamp(line.get('started_at'))} : " + " > ".join(line["steps"])


def render_lines(rows, **kwargs) -> list[str]:
    return [render_line(line) for line in group_lines(rows, **kwargs)]


def idle_minutes(last_seen, now: datetime | None = None) -> int | None:
    """Whole minutes since the last button, or None if never seen."""
    moment = parse_ts(last_seen)
    if moment is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - moment).total_seconds() // 60))


def status_text(last_seen, now: datetime | None = None,
                active_min: int = ACTIVE_MIN) -> str:
    """'active \U0001f7e2' while in use, otherwise 'activity was 7 min'."""
    minutes = idle_minutes(last_seen, now)
    if minutes is None:
        return "never used the bot"
    if minutes < active_min:
        return "active \U0001f7e2"
    return f"activity was {minutes} min"
