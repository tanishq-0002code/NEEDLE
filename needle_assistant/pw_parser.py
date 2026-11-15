from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from . import windows_api as win

STUDY_URL = "https://pw.live/study-v2/study"
EDGE_PROCESS = "msedge.exe"
CONTENT_LEFT = 230
INTERACTIVE_TYPES = {"ButtonControl", "HyperlinkControl", "ListItemControl"}

_SUBJECT_ALIASES = {
    "math": "maths",
    "mathematics": "maths",
    "maths": "maths",
    "phy": "physics",
    "physics": "physics",
    "chem": "chemistry",
    "chemistry": "chemistry",
    "physical chemistry": "chemistry",
    "organic chemistry": "chemistry",
    "inorganic chemistry": "chemistry",
    "bio": "biology",
    "biology": "biology",
    "eng": "english",
    "english": "english",
    "stats": "statistics",
    "statistics": "statistics",
    "reasoning": "reasoning",
    "computer": "computer science",
    "computer science": "computer science",
    "sst": "social science",
    "social science": "social science",
    "science": "science",
    "hindi": "hindi",
    "economics": "economics",
    "accountancy": "accountancy",
    "business studies": "business studies",
}

_SUBJECT_RE = re.compile(
    r"\b(physical\s+chemistry|organic\s+chemistry|inorganic\s+chemistry|"
    r"computer\s+science|social\s+science|business\s+studies|mathematics|maths|math|"
    r"physics|chemistry|chem|biology|bio|english|eng|statistics|stats|reasoning|"
    r"science|hindi|economics|accountancy)\b",
    re.I,
)

_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_DATE_RE = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+\d{2,4})?)\b",
    re.I,
)
_ACTION_RE = re.compile(
    r"\b(?:watch(?:\s+again)?|join(?:\s+now)?|watch\s+live|attend|"
    r"start(?:\s+class)?|resume|continue|play|replay|open)\b",
    re.I,
)
_LIVE_ACTION_RE = re.compile(
    r"\b(?:join(?:\s+now)?|watch\s+live|attend|start(?:\s+class)?)\b", re.I
)
_STATUS_RE = re.compile(
    r"\b(?:live|live\s+now|ongoing|upcoming|completed|finished|ended|recorded|replay)\b",
    re.I,
)
_BATCH_RE = re.compile(r"Select\s+batch,\s*current\s*:\s*(.+)$", re.I)

_ACTION_ONLY = {
    "watch", "watch again", "join", "join now", "watch live", "attend",
    "start", "start class", "resume", "continue", "play", "replay", "open",
    "view", "back", "subjects", "apply", "reset", "close", "next", "previous",
    "show more", "view full schedule", "know more", "dismiss xp banner",
    "close tooltip", "notes & more", "mark as incomplete", "attempt",
    "attempt dpp", "download app", "change cohort", "open filters",
    "close filters", "notifications", "favorites", "downloads",
}

_NAV_LABELS = {
    "study", "study study", "pi", "pi pi", "library", "library library",
    "batches", "batches batches", "power batch", "power batch power batch",
    "test series", "test series test series", "my test", "my test my test",
    "scholarship", "scholarship scholarship", "pw centres", "pw store",
    "career pass", "pw books", "all classes", "all tests", "my doubts",
    "digital books", "community", "catchup", "preparation meter",
    "upgrade your batch plan", "batch options", "pw cash balance",
    "download app", "change cohort", "refer earn", "physics wallah home",
    "skip to main content", "back", "subjects", "view full schedule",
    "show more", "apply", "reset", "open filters", "close filters",
    "notifications", "favorites", "downloads",
}


class StudyError(RuntimeError):
    """Raised when the Physics Wallah dashboard cannot be reached or read."""


@dataclass
class PageControl:
    type: str
    name: str
    value: str
    rect: tuple[int, int, int, int]
    depth: int
    offscreen: bool = False
    enabled: bool = True
    control: Any = field(default=None, repr=False, compare=False)

    @property
    def label(self) -> str:
        return " ".join(part for part in (self.name, self.value) if part).strip()


@dataclass
class StudyEvent:
    id: str
    status: str
    subject: str
    title: str
    time_text: str
    start_minutes: int | None
    end_minutes: int | None
    date_text: str
    teacher: str
    recorded: bool
    batch: str
    text: str
    control_name: str
    control_type: str
    rect: tuple[int, int, int, int]
    depth: int
    section: str
    actions: list[str] = field(default_factory=list)

    @property
    def is_live(self) -> bool:
        return self.status == "live"


@dataclass
class StudySnapshot:
    batch: str = ""
    page_title: str = ""
    live: list[StudyEvent] = field(default_factory=list)
    upcoming: list[StudyEvent] = field(default_factory=list)
    recorded: list[StudyEvent] = field(default_factory=list)
    completed: list[StudyEvent] = field(default_factory=list)
    all_events: list[StudyEvent] = field(default_factory=list)
    raw_text: str = ""
    captured_at: datetime = field(default_factory=datetime.now)

    @property
    def has_live(self) -> bool:
        return bool(self.live)


def _norm(value: str) -> str:
    return " ".join(str(value or "").replace("\u200b", " ").replace("\xa0", " ").split())


def _area(rect: tuple[int, int, int, int]) -> int:
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _inside(
    inner: tuple[int, int, int, int],
    outer: tuple[int, int, int, int],
    margin: int = 0,
) -> bool:
    return (
        inner[0] >= outer[0] - margin
        and inner[1] >= outer[1] - margin
        and inner[2] <= outer[2] + margin
        and inner[3] <= outer[3] + margin
    )


def _overlap_x(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def to_minutes(hour: int, minute: int, suffix: str) -> int:
    hour = hour % 12
    if suffix.lower() == "pm":
        hour += 12
    return hour * 60 + minute


def parse_time_range(text: str) -> tuple[str, int | None, int | None]:
    """Return a spoken time label plus start/end minutes for a card."""
    value = _norm(text)
    matches = list(_TIME_RE.finditer(value))
    if not matches:
        return "", None, None
    starts: list[tuple[int, int, str]] = []
    for match in matches:
        minute = int(match.group(2) or 0)
        if minute > 59:
            continue
        starts.append((int(match.group(1)), minute, match.group(3)))
    if not starts:
        return "", None, None
    start = to_minutes(*starts[0])
    end: int | None = None
    if len(starts) >= 2:
        between = value[matches[0].end():matches[1].start()]
        if re.fullmatch(r"\s*(?:-|–|—|to|until|till)\s*", between, re.I):
            candidate = to_minutes(*starts[1])
            if candidate <= start:
                candidate += 24 * 60
            if candidate != start:
                end = candidate
                label = f"{_format_minutes(start)} to {_format_minutes(end)}"
                return label, start, end
    return _format_minutes(start), start, None


def _format_minutes(minutes: int) -> str:
    minutes %= 24 * 60
    hour = minutes // 60
    minute = minutes % 60
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def detect_subject(text: str) -> str:
    match = _SUBJECT_RE.search(_norm(text))
    if not match:
        return ""
    raw = _norm(match.group(1)).lower()
    return _SUBJECT_ALIASES.get(raw, raw)


def normalize_subject(value: str) -> str:
    text = _norm(value).lower()
    text = re.sub(
        r"\b(?:the|a|an|class|classes|lecture|lectures|recording|recorded|"
        r"video|stream|session|today|now|please|my|me)\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" .,!?:;-")
    return _SUBJECT_ALIASES.get(text, text)


def normalize_kind(value: str) -> str:
    text = _norm(value).lower()
    if "record" in text or text in {"recording", "video", "replay", "watch"}:
        return "recorded"
    if "live" in text or text in {"stream", "join"}:
        return "live"
    if "upcoming" in text or text == "next":
        return "upcoming"
    if "completed" in text or "finished" in text:
        return "completed"
    if text in {"class", "lecture", "session", "course"}:
        return "class"
    return "auto"


def _section_for_heading(label: str) -> str:
    value = _norm(label).lower()
    if not value:
        return ""
    if value.startswith("upcoming"):
        return "upcoming"
    if value.startswith("live"):
        return "live"
    if value.startswith("recorded"):
        return "recorded"
    if value.startswith("completed") or value.startswith("previous"):
        return "completed"
    if value.startswith(("weekly schedule", "today's schedule", "todays schedule",
                         "schedule", "timetable")):
        return "schedule"
    return ""


def _visible(control: PageControl, window_rect: tuple[int, int, int, int] | None) -> bool:
    left, top, right, bottom = control.rect
    if control.offscreen or right <= left or bottom <= top:
        return False
    if left < CONTENT_LEFT and right < CONTENT_LEFT + 30:
        return False
    if window_rect and window_rect[2] > window_rect[0]:
        wl, wt, wr, wb = window_rect
        if left >= wr or right <= wl:
            return False
        if bottom <= wt:
            return False
    return True


def _visible_clickable(
    control: PageControl, window_rect: tuple[int, int, int, int] | None
) -> bool:
    """Like _visible, but keeps the left sidebar so nav buttons can be clicked."""
    left, top, right, bottom = control.rect
    if control.offscreen or right <= left or bottom <= top:
        return False
    if window_rect and window_rect[2] > window_rect[0]:
        wl, wt, wr, wb = window_rect
        if left >= wr or right <= wl:
            return False
        if bottom <= wt:
            return False
    return True


def _find_page_title(controls: list[PageControl]) -> str:
    best = ""
    best_area = 0
    for control in controls:
        if control.type != "DocumentControl":
            continue
        label = _norm(control.name)
        if not label:
            continue
        area = _area(control.rect)
        if area >= best_area:
            best, best_area = label, area
    return best


def _find_batch(controls: list[PageControl]) -> str:
    for control in controls:
        match = _BATCH_RE.search(_norm(control.name))
        if match:
            return match.group(1).strip()
    return ""


def _find_headings(controls: list[PageControl]) -> list[tuple[int, str, str]]:
    headings: list[tuple[int, str, str]] = []
    seen: set[tuple[str, int]] = set()
    for control in controls:
        if control.type != "TextControl":
            continue
        label = _norm(control.name)
        section = _section_for_heading(label)
        if not section:
            continue
        top = control.rect[1]
        key = (section, top // 12)
        if key in seen:
            continue
        seen.add(key)
        headings.append((top, section, label))
    headings.sort(key=lambda item: item[0])
    return headings


def _build_regions(
    headings: list[tuple[int, str, str]]
) -> list[tuple[int, float, str, str]]:
    regions: list[tuple[int, float, str, str]] = []
    for index, (top, section, label) in enumerate(headings):
        end = float(headings[index + 1][0]) if index + 1 < len(headings) else float("inf")
        if end > top:
            regions.append((top, end, section, label))
    return regions


def _region_for_y(
    top: int, regions: list[tuple[int, float, str, str]]
) -> tuple[int, float, str, str] | None:
    for start, end, section, label in regions:
        if start <= top < end:
            return start, end, section, label
    return None


def _child_texts(card: PageControl, controls: list[PageControl]) -> list[str]:
    rows: list[tuple[int, int, str]] = []
    for control in controls:
        if control is card or control.depth <= card.depth:
            continue
        if not _inside(control.rect, card.rect, margin=4):
            continue
        if control.type not in {"TextControl", "ImageControl"}:
            continue
        label = _norm(control.name or control.value)
        if label:
            rows.append((control.rect[1], control.rect[0], label))
    rows.sort()
    out: list[str] = []
    for _, _, label in rows:
        if label not in out:
            out.append(label)
    return out


def _sibling_time(card: PageControl, controls: list[PageControl]) -> str:
    best = ""
    best_gap = 10_000
    for control in controls:
        if control is card or control.type != "TextControl":
            continue
        label = _norm(control.name)
        if not label or not _TIME_RE.search(label):
            continue
        if not _overlap_x(control.rect, card.rect):
            continue
        if control.rect[3] <= card.rect[1] + 8:
            gap = card.rect[1] - control.rect[3]
            if 0 <= gap <= 70 and gap < best_gap:
                best, best_gap = label, gap
    return best


def _extract_teacher(text: str) -> str:
    match = re.search(
        r"\b(?:by|faculty|teacher|mentor)\s*[:\-]?\s*"
        r"([A-Z][A-Za-z.\-']{1,30}(?:\s+[A-Z][A-Za-z.\-']{1,30}){0,2})",
        text,
    )
    if match:
        candidate = _norm(match.group(1))
        if candidate.lower() not in {"the", "live", "class", "lecture", "maths", "physics"}:
            return candidate
    return ""


def _extract_date(text: str) -> str:
    match = _DATE_RE.search(text)
    return _norm(match.group(1)) if match else ""


def _clean_title(
    card: PageControl, child_texts: list[str], time_text: str, subject: str
) -> str:
    text = _norm(card.name)
    text = re.sub(
        r"^(?:live|recorded|lecture|class|upcoming|completed)\s*[:\-]\s*",
        "",
        text,
        flags=re.I,
    )
    if time_text:
        for label in (time_text, time_text.replace(" to ", " - ")):
            text = text.replace(label, " ")
    text = _TIME_RE.sub(" ", text)
    if subject:
        text = re.sub(
            rf"^\s*{re.escape(subject)}\s*(?:[,:\-–—]+)\s*", "", text, flags=re.I
        )
        text = re.sub(
            rf"^\s*{re.escape(subject)}\s+(?=\D)", "", text, flags=re.I
        )
    text = re.sub(r"\s*[,;]+\s*$", "", text)
    text = re.sub(r"\s*[-,]?\s*(?:upcoming|completed|live\s+now|live|ongoing)\s*$",
                  "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -–—:,")
    if text and text.lower() not in {
        "lecture", "class", "live", "recorded", "upcoming", "completed", "watch",
    }:
        return text[:120]
    for label in sorted(child_texts, key=len, reverse=True):
        low = label.lower()
        if low in _ACTION_ONLY or low in {
            "upcoming", "completed", "live", "recorded", "ongoing", "lecture",
            "class", "maths", "physics", "chemistry", "biology", "english",
        }:
            continue
        if subject and low.strip() == subject:
            continue
        if _TIME_RE.fullmatch(label):
            continue
        if len(label) > 3:
            return label[:120]
    return _norm(card.name)[:120]


def _event_actions(card: PageControl, controls: list[PageControl]) -> list[str]:
    actions: list[str] = []
    for control in controls:
        if control is card or control.depth <= card.depth:
            continue
        if control.type not in INTERACTIVE_TYPES:
            continue
        if not _inside(control.rect, card.rect, margin=4):
            continue
        label = _norm(control.name or control.value)
        low = label.lower()
        if not label or low in {"view full schedule", "show more"}:
            continue
        if "mark as incomplete" in low or "notes & more" in low or low.startswith("attempt"):
            continue
        if _ACTION_RE.search(label) and label not in actions:
            actions.append(label)
    return actions


def _status_for(
    text: str,
    section: str,
    start_minutes: int | None,
    end_minutes: int | None,
    now: datetime,
) -> str:
    low = text.lower()
    if _LIVE_ACTION_RE.search(low) or re.search(
        r"\b(?:live\s+now|currently\s+live|ongoing)\b", low
    ):
        return "live"
    if re.search(r"\b(?:completed|finished|ended)\b", low):
        return "completed"
    if re.search(r"\bupcoming\b", low):
        return "upcoming"
    if section == "live":
        return "live"
    if section == "recorded":
        return "recorded"
    if section == "completed":
        return "completed"
    if section == "upcoming":
        return "upcoming"
    if re.search(r"\brecorded\b", low):
        return "recorded"
    if section == "schedule":
        if start_minutes is not None:
            now_minutes = now.hour * 60 + now.minute
            if end_minutes is not None and now_minutes > end_minutes:
                return "completed"
        return "upcoming"
    return "upcoming"


def _event_id(event: StudyEvent) -> str:
    raw = "|".join(
        (event.subject, event.title, event.time_text, event.status, str(event.rect))
    )
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _build_event(
    card: PageControl,
    section: str,
    child_texts: list[str],
    sibling_time: str,
    combined: str,
    batch: str,
    now: datetime,
) -> StudyEvent | None:
    subject_text = " ".join(child_texts + [card.name])
    subject = detect_subject(subject_text) or detect_subject(combined)
    time_text, start_minutes, end_minutes = parse_time_range(
        " ".join(part for part in (sibling_time, combined) if part)
    )
    status = _status_for(combined, section, start_minutes, end_minutes, now)
    title = _clean_title(card, child_texts, time_text, subject)
    if not title:
        return None
    recorded = bool(re.search(r"\brecorded\b|\breplay\b", combined, re.I)) or section == "recorded"
    actions: list[str] = []
    event = StudyEvent(
        id="",
        status=status,
        subject=subject,
        title=title,
        time_text=time_text,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        date_text=_extract_date(combined),
        teacher=_extract_teacher(combined),
        recorded=recorded,
        batch=batch,
        text=_norm(combined)[:600],
        control_name=_norm(card.name),
        control_type=card.type,
        rect=card.rect,
        depth=card.depth,
        section=section,
        actions=actions,
    )
    event.id = _event_id(event)
    return event


def _event_key(event: StudyEvent) -> str:
    return "|".join(
        (
            event.subject.lower(),
            re.sub(r"\W+", "", event.title.lower())[:80],
            event.time_text,
            event.status,
        )
    )


def _dedupe_events(events: list[StudyEvent]) -> list[StudyEvent]:
    ranked = sorted(
        events,
        key=lambda event: (
            -len(event.actions),
            -_area(event.rect),
            event.start_minutes if event.start_minutes is not None else 24 * 60,
        ),
    )
    kept: list[StudyEvent] = []
    seen: set[str] = set()
    for event in ranked:
        key = _event_key(event)
        if key in seen:
            continue
        duplicate = False
        for other in kept:
            if not _inside(event.rect, other.rect) and not _inside(other.rect, event.rect):
                continue
            if key == _event_key(other):
                duplicate = True
                break
        if duplicate:
            continue
        seen.add(key)
        kept.append(event)
    return kept


def _sort_events(events: list[StudyEvent]) -> list[StudyEvent]:
    priority = {"live": 0, "upcoming": 1, "recorded": 2, "completed": 3}
    return sorted(
        events,
        key=lambda event: (
            priority.get(event.status, 4),
            event.start_minutes if event.start_minutes is not None else 24 * 60 + 1,
            event.title.lower(),
        ),
    )


def _nearby_texts(button: PageControl, controls: list[PageControl]) -> list[str]:
    rows: list[tuple[int, str]] = []
    for control in controls:
        if control.type != "TextControl":
            continue
        if control is button or not _overlap_x(control.rect, button.rect):
            continue
        gap = button.rect[1] - control.rect[3]
        if 0 <= gap <= 240 or _inside(control.rect, button.rect, margin=120):
            label = _norm(control.name)
            if label:
                rows.append((gap, label))
    rows.sort()
    return [label for _, label in rows]


def _merge_live_buttons(
    controls: list[PageControl],
    events: list[StudyEvent],
    batch: str,
    now: datetime,
) -> list[StudyEvent]:
    """Catch live cards whose only actionable element is a Join button."""
    extra: list[StudyEvent] = []
    for control in controls:
        if control.type not in INTERACTIVE_TYPES:
            continue
        label = _norm(control.name or control.value)
        if not label or not _LIVE_ACTION_RE.search(label):
            continue
        if label.lower() in {"view full schedule", "show more"}:
            continue
        if any(
            _inside(control.rect, event.rect, margin=8)
            and event.status == "live"
            for event in events
        ):
            continue
        nearby = _nearby_texts(control, controls)
        text = _norm(" ".join(nearby + [label]))
        subject = detect_subject(text)
        time_text, start_minutes, end_minutes = parse_time_range(text)
        title = ""
        for candidate in sorted(nearby, key=len, reverse=True):
            low = candidate.lower()
            if low in _ACTION_ONLY or low in {
                "live", "live now", "upcoming", "completed", "ongoing",
                "lecture", "class", "recorded",
            }:
                continue
            if subject and low.strip() == subject:
                continue
            if _TIME_RE.fullmatch(candidate):
                continue
            if len(candidate) > 3:
                title = candidate[:120]
                break
        if not title:
            title = "Live class"
        event = StudyEvent(
            id="",
            status="live",
            subject=subject,
            title=title,
            time_text=time_text,
            start_minutes=start_minutes,
            end_minutes=end_minutes,
            date_text=_extract_date(text),
            teacher=_extract_teacher(text),
            recorded=False,
            batch=batch,
            text=text[:600],
            control_name=label,
            control_type=control.type,
            rect=control.rect,
            depth=control.depth,
            section="live",
            actions=[label],
        )
        event.id = _event_id(event)
        extra.append(event)
    return extra


def parse_controls(
    controls: list[PageControl],
    page_title: str = "",
    batch: str = "",
    now: datetime | None = None,
    window_rect: tuple[int, int, int, int] | None = None,
) -> StudySnapshot:
    """Turn a flattened UIA tree into a structured dashboard snapshot."""
    now = now or datetime.now()
    visible = [control for control in controls if _visible(control, window_rect)]
    title = page_title or _find_page_title(visible)
    batch = batch or _find_batch(visible)
    regions = _build_regions(_find_headings(visible))
    events: list[StudyEvent] = []
    for control in visible:
        if control.type not in INTERACTIVE_TYPES:
            continue
        label = _norm(control.name or control.value)
        if not label:
            continue
        low = label.lower()
        if low in _NAV_LABELS or low in _ACTION_ONLY:
            continue
        if low.startswith(("view full schedule", "show more")):
            continue
        if _area(control.rect) < 3000 or control.rect[2] - control.rect[0] < 100:
            continue
        region = _region_for_y(control.rect[1], regions)
        section = region[2] if region else ""
        child_texts = _child_texts(control, visible)
        sibling = _sibling_time(control, visible)
        combined = _norm(" ".join([label] + child_texts + ([sibling] if sibling else [])))
        if section not in {"live", "upcoming", "recorded", "completed", "schedule"}:
            if not (
                _TIME_RE.search(combined)
                or re.search(
                    r"\b(?:live|upcoming|recorded|completed|join|watch|replay)\b",
                    combined,
                    re.I,
                )
            ):
                continue
        if not re.search(
            r"\b(?:lecture|class|live|recorded|upcoming|completed|ongoing|join|watch|replay)\b",
            combined,
            re.I,
        ):
            continue
        event = _build_event(
            control, section, child_texts, sibling, combined, batch, now
        )
        if event is not None:
            event.actions = _event_actions(control, visible)
            events.append(event)
    events.extend(_merge_live_buttons(visible, events, batch, now))
    events = _sort_events(_dedupe_events(events))
    live = [event for event in events if event.status == "live"]
    upcoming = [event for event in events if event.status == "upcoming"]
    completed = [event for event in events if event.status == "completed"]
    recorded = [
        event for event in events
        if event.recorded or event.status == "recorded"
    ]
    raw = " | ".join(
        _norm(control.name) for control in visible if _norm(control.name)
    )[:20000]
    return StudySnapshot(
        batch=batch,
        page_title=title,
        live=live,
        upcoming=upcoming,
        recorded=recorded,
        completed=completed,
        all_events=events,
        raw_text=raw,
    )


def _event_line(event: StudyEvent, include_time: bool = True) -> str:
    title = _norm(event.title)
    subject = _norm(event.subject).title()
    if subject and title.lower().startswith(subject.lower()):
        label = title
    elif subject and title:
        label = f"{subject} — {title}"
    else:
        label = subject or title or "Class"
    label = re.sub(r"\s+", " ", label).strip(" -–—,:")
    if len(label) > 110:
        label = label[:107].rstrip() + "..."
    if include_time and event.time_text:
        label += f" at {event.time_text}"
    if event.teacher:
        label += f" with {event.teacher}"
    low = title.lower()
    if event.status == "completed" and "completed" not in low:
        label += " (completed)"
    elif event.recorded and "record" not in low and "replay" not in low:
        label += " (recorded)"
    return label


def format_status(snapshot: StudySnapshot) -> str:
    parts: list[str] = []
    if snapshot.batch:
        parts.append(f"Batch {snapshot.batch}.")
    if snapshot.live:
        parts.append(
            "Live right now: "
            + "; ".join(_event_line(event) for event in snapshot.live[:3])
            + "."
        )
    else:
        parts.append("No class is live right now.")
    if snapshot.upcoming:
        parts.append(f"Next up: {_event_line(snapshot.upcoming[0])}.")
        rest = snapshot.upcoming[1:6]
        if rest:
            parts.append(
                "Also upcoming: "
                + "; ".join(_event_line(event) for event in rest)
                + "."
            )
    elif not snapshot.live:
        parts.append("I don't see any upcoming classes on the dashboard.")
    if snapshot.completed:
        parts.append(
            "Recently completed: "
            + "; ".join(_event_line(event) for event in snapshot.completed[:2])
            + "."
        )
    listed = {event.id for event in snapshot.upcoming + snapshot.completed}
    extra_recorded = [event for event in snapshot.recorded if event.id not in listed]
    if extra_recorded:
        parts.append(
            "Recorded: "
            + "; ".join(_event_line(event) for event in extra_recorded[:3])
            + "."
        )
    if snapshot.page_title and "weekly schedule" in snapshot.page_title.lower():
        parts.append("Showing the weekly schedule.")
    if not snapshot.all_events:
        parts.append("The dashboard may still be loading, so I couldn't read any classes.")
    return " ".join(parts)


def format_schedule(snapshot: StudySnapshot) -> str:
    events = _sort_events(_dedupe_events(list(snapshot.all_events)))
    header = "Weekly schedule" + (
        f" for {snapshot.batch}" if snapshot.batch else ""
    ) + "."
    if not events:
        return header + " I couldn't find any classes in it."
    lines: list[str] = [header]
    for event in events[:25]:
        status = event.status
        if event.recorded and status not in {"live", "recorded"}:
            status = f"{status}, recorded"
        when = event.time_text or "Time not shown"
        lines.append(f"{when} — {_event_line(event, include_time=False)} ({status}).")
    if len(events) > 25:
        lines.append(f"And {len(events) - 25} more.")
    return " ".join(lines)


def _event_matches_subject(event: StudyEvent, subject: str) -> bool:
    if not subject:
        return True
    event_subject = normalize_subject(event.subject)
    if event_subject and (
        event_subject == subject
        or subject in event_subject
        or event_subject in subject
    ):
        return True
    haystack = normalize_subject(f"{event.title} {event.text}")
    return subject in haystack


def find_event(
    snapshot: StudySnapshot, subject: str = "", kind: str = "auto"
) -> StudyEvent | None:
    subject = normalize_subject(subject)
    kind = normalize_kind(kind)
    events = [event for event in snapshot.all_events if _event_matches_subject(event, subject)]
    if not events:
        return None
    if kind == "live":
        matches = [event for event in events if event.status == "live"]
    elif kind == "recorded":
        matches = [event for event in events if event.recorded or event.status == "recorded"]
    elif kind == "completed":
        matches = [event for event in events if event.status == "completed"]
    elif kind == "upcoming":
        matches = [event for event in events if event.status == "upcoming"]
    elif kind == "class":
        matches = [
            event for event in events
            if event.status in {"live", "upcoming"}
        ]
    else:
        matches = [
            event for event in events
            if event.status in {"live", "upcoming", "recorded", "completed"}
        ]
    return matches[0] if matches else None


@contextmanager
def _uia_context():
    try:
        import comtypes
        import uiautomation as auto
    except Exception as exc:  # pragma: no cover - depends on Windows packages
        raise StudyError("UI Automation is unavailable on this system.") from exc
    initialized = False
    try:
        comtypes.CoInitialize()
        initialized = True
        auto.SetGlobalSearchTimeout(1.0)
        yield auto
    finally:
        if initialized:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def _collect_with_auto(
    auto,
    hwnd: int,
    max_nodes: int = 30000,
    max_depth: int = 40,
    keep_control: bool = False,
) -> list[PageControl]:
    try:
        root = auto.ControlFromHandle(hwnd)
    except Exception:
        return []
    if root is None:
        return []
    rows: list[PageControl] = []
    seen = 0
    try:
        for control, depth in auto.WalkControl(
            root, includeTop=True, maxDepth=max_depth
        ):
            seen += 1
            if seen > max_nodes:
                break
            try:
                control_type = control.ControlTypeName or ""
                name = str(control.Name or "")
                value = ""
                try:
                    pattern = control.GetValuePattern()
                    if pattern is not None:
                        value = str(pattern.Value or "")
                except Exception:
                    value = ""
                if not name.strip() and not value.strip() and control_type != "DocumentControl":
                    continue
                box = control.BoundingRectangle
                rect = (int(box.left), int(box.top), int(box.right), int(box.bottom))
                rows.append(
                    PageControl(
                        type=control_type,
                        name=name,
                        value=value,
                        rect=rect,
                        depth=depth,
                        offscreen=bool(control.IsOffscreen),
                        enabled=bool(control.IsEnabled),
                        control=control if keep_control else None,
                    )
                )
            except Exception:
                continue
    except Exception:
        pass
    return rows


def collect_page_controls(hwnd: int) -> list[PageControl]:
    with _uia_context() as auto:
        return _collect_with_auto(auto, hwnd)


def capture_page(hwnd: int) -> tuple[list[PageControl], StudySnapshot]:
    controls = collect_page_controls(hwnd)
    title = _find_page_title(controls)
    snapshot = parse_controls(
        controls,
        page_title=title,
        window_rect=win.get_window_rect(hwnd),
    )
    return controls, snapshot


def _activate_control_with_auto(auto, control, hwnd: int = 0) -> bool:
    try:
        if control.ControlTypeName == "TabItemControl":
            pattern = control.GetSelectionItemPattern()
            if pattern is not None:
                pattern.Select()
                return True
    except Exception:
        pass
    try:
        pattern = control.GetInvokePattern()
        if pattern is not None:
            pattern.Invoke()
            return True
    except Exception:
        pass
    try:
        control.Click(simulateMove=False)
        return True
    except Exception:
        pass
    if hwnd:
        try:
            win.focus_window(hwnd)
            time.sleep(0.15)
        except Exception:
            pass
    try:
        point = control.GetClickablePoint()
        x = int(point.x if hasattr(point, "x") else point[0])
        y = int(point.y if hasattr(point, "y") else point[1])
        import pyautogui

        pyautogui.click(x, y)
        return True
    except Exception:
        return False


def _find_control(
    controls: list[PageControl],
    names: tuple[str, ...] | list[str] = (),
    types: set[str] | None = None,
    exact: bool = False,
    predicate: Callable[[PageControl], bool] | None = None,
) -> PageControl | None:
    needles = [name.lower() for name in names]
    for control in controls:
        if types and control.type not in types:
            continue
        label = _norm(control.name or control.value)
        low = label.lower()
        matches = False
        if needles:
            for needle in needles:
                if (exact and low == needle) or (not exact and needle in low):
                    matches = True
                    break
        if predicate is not None:
            matches = matches and predicate(control)
        if matches:
            return control
    return None


def click_named(
    hwnd: int,
    names: tuple[str, ...] | list[str],
    types: set[str] | None = None,
    exact: bool = False,
    timeout: float = 6.0,
    predicate: Callable[[PageControl], bool] | None = None,
) -> bool:
    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        with _uia_context() as auto:
            controls = _collect_with_auto(
                auto, hwnd, keep_control=True
            )
            visible = [
                control for control in controls
                if _visible_clickable(control, win.get_window_rect(hwnd))
            ]
            target = _find_control(
                visible, names, types=types, exact=exact, predicate=predicate
            )
            if target is not None and target.control is not None:
                if _activate_control_with_auto(auto, target.control, hwnd):
                    return True
        time.sleep(0.35)
    return False


def _wait_for_page(
    hwnd: int, predicate: Callable[[str, list[PageControl]], bool], timeout: float
) -> tuple[list[PageControl], StudySnapshot]:
    deadline = time.monotonic() + max(0.5, timeout)
    controls: list[PageControl] = []
    snapshot = StudySnapshot()
    while time.monotonic() < deadline:
        controls = collect_page_controls(hwnd)
        title = _find_page_title(controls)
        snapshot = parse_controls(
            controls,
            page_title=title,
            window_rect=win.get_window_rect(hwnd),
        )
        if predicate(title, controls):
            return controls, snapshot
        time.sleep(0.6)
    return controls, snapshot


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def _edge_path() -> str:
    candidate = shutil.which("msedge.exe")
    if candidate:
        return candidate
    roots = [
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        path = os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.exists(path):
            return path
    return ""


def find_browser_window(process: str = EDGE_PROCESS) -> int:
    foreground = win.get_foreground_window()
    if foreground and _process_name(win.get_window_pid(foreground)) == process:
        return foreground
    best = 0
    best_area = 0
    for info in win.enumerate_windows():
        if _process_name(info.pid) != process:
            continue
        area = _area(info.rect)
        if area > best_area:
            best, best_area = info.hwnd, area
    return best


def launch_edge(url: str = STUDY_URL, timeout: float = 15.0) -> int:
    path = _edge_path()
    if not path:
        raise StudyError("Microsoft Edge was not found on this system.")
    try:
        subprocess.Popen(
            [path, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise StudyError(f"Could not open Edge: {exc}") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_browser_window()
        if hwnd:
            return hwnd
        time.sleep(0.4)
    raise StudyError("Edge did not open in time.")


def list_tabs(hwnd: int) -> list[str]:
    tabs: list[str] = []
    with _uia_context() as auto:
        controls = _collect_with_auto(auto, hwnd, max_depth=8)
    for control in controls:
        if control.type == "TabItemControl":
            label = _norm(control.name)
            if label:
                tabs.append(label)
    return tabs


def activate_tab(hwnd: int, needle: str) -> bool:
    needle = needle.lower()
    with _uia_context() as auto:
        controls = _collect_with_auto(auto, hwnd, max_depth=8, keep_control=True)
        for control in controls:
            if control.type != "TabItemControl":
                continue
            if needle not in _norm(control.name).lower():
                continue
            if control.control is not None and _activate_control_with_auto(
                auto, control.control, hwnd
            ):
                return True
    return False


def _any_pw_tab(hwnd: int) -> str:
    tabs = list_tabs(hwnd)
    for tab in tabs:
        low = tab.lower()
        if "physics wallah" in low or "pw.live" in low:
            return tab
    return ""


def click_study_nav(hwnd: int) -> bool:
    return click_named(
        hwnd,
        ("study study", "study"),
        types={"ButtonControl"},
        exact=True,
        timeout=4.0,
        predicate=lambda control: control.rect[0] < 250,
    )


def _navigate_current_tab(hwnd: int, url: str) -> bool:
    try:
        win.focus_window(hwnd)
        time.sleep(0.25)
        from pynput.keyboard import Controller, Key

        keyboard = Controller()
        with keyboard.pressed(Key.ctrl):
            keyboard.press("l")
            keyboard.release("l")
        time.sleep(0.15)
        keyboard.type(url)
        time.sleep(0.1)
        keyboard.press(Key.enter)
        keyboard.release(Key.enter)
        return True
    except Exception:
        return False


def _open_study_tab(hwnd: int, url: str) -> bool:
    try:
        win.focus_window(hwnd)
        time.sleep(0.25)
        from pynput.keyboard import Controller, Key

        keyboard = Controller()
        with keyboard.pressed(Key.ctrl):
            keyboard.press("t")
            keyboard.release("t")
        time.sleep(0.3)
        keyboard.type(url)
        time.sleep(0.1)
        keyboard.press(Key.enter)
        keyboard.release(Key.enter)
        return True
    except Exception:
        return False


def ensure_study_page(
    url: str = STUDY_URL,
    timeout: float = 15.0,
    allow_schedule: bool = False,
    batch: str = "",
) -> tuple[int, list[PageControl], StudySnapshot]:
    hwnd = find_browser_window()
    if not hwnd:
        hwnd = launch_edge(url)
    target = "weekly schedule - physics wallah" if allow_schedule else "study - physics wallah"
    activated = activate_tab(hwnd, target)
    if not activated and allow_schedule:
        activated = activate_tab(hwnd, "study - physics wallah")
    if not activated:
        any_tab = _any_pw_tab(hwnd)
        if any_tab:
            activate_tab(hwnd, any_tab.lower().split(" - memory")[0])
            if not allow_schedule:
                if not click_study_nav(hwnd):
                    _navigate_current_tab(hwnd, url)
        else:
            _open_study_tab(hwnd, url)
    def ready(title: str, controls: list[PageControl]) -> bool:
        low = title.lower()
        if allow_schedule and "weekly schedule" in low:
            return True
        if "study - physics wallah" in low:
            text = " ".join(_norm(control.name) for control in controls)
            return "upcoming events" in text.lower() or "batch offerings" in text.lower()
        return False

    controls, snapshot = _wait_for_page(hwnd, ready, timeout)
    low_title = snapshot.page_title.lower()
    if not low_title:
        raise StudyError(
            "I couldn't read the Physics Wallah page. Make sure Edge is signed in "
            "and the Study tab is loaded."
        )
    if "sign in" in low_title or "log in" in low_title or "login" in low_title:
        raise StudyError(
            "Physics Wallah is asking for a sign-in. Sign in in Edge, then try again."
        )
    if not snapshot.batch and batch:
        snapshot.batch = batch
    return hwnd, controls, snapshot


def reload_study_page(
    timeout: float = 12.0,
) -> tuple[int, list[PageControl], StudySnapshot]:
    hwnd, _controls, _snapshot = ensure_study_page(timeout=timeout)
    try:
        win.focus_window(hwnd)
        time.sleep(0.2)
        from pynput.keyboard import Controller, Key

        keyboard = Controller()
        keyboard.press(Key.f5)
        keyboard.release(Key.f5)
    except Exception:
        pass
    time.sleep(1.2)
    controls, snapshot = _wait_for_page(
        hwnd,
        lambda title, _controls: "study - physics wallah" in title.lower(),
        timeout,
    )
    return hwnd, controls, snapshot


def open_schedule_page(
    timeout: float = 18.0,
) -> tuple[int, list[PageControl], StudySnapshot]:
    hwnd, _controls, study = ensure_study_page(timeout=timeout)
    batch = study.batch
    if not activate_tab(hwnd, "weekly schedule - physics wallah"):
        if not click_named(
            hwnd,
            ("view full schedule",),
            types={"ButtonControl", "HyperlinkControl"},
            exact=False,
            timeout=6.0,
        ):
            raise StudyError("I couldn't find the View Full Schedule button.")
    controls, snapshot = _wait_for_page(
        hwnd,
        lambda title, _controls: "weekly schedule" in title.lower(),
        timeout,
    )
    snapshot.batch = snapshot.batch or batch
    return hwnd, controls, snapshot


def _find_subject_card(
    controls: list[PageControl], subject: str
) -> PageControl | None:
    candidates: list[PageControl] = []
    for control in controls:
        if control.type not in {"HyperlinkControl", "ButtonControl"}:
            continue
        label = _norm(control.name or control.value).lower()
        if "%" not in label:
            continue
        if subject and subject in label:
            candidates.append(control)
    if not candidates:
        return None
    candidates.sort(key=lambda control: (control.rect[1] // 20, control.rect[0]))
    return candidates[0]


def _find_first_chapter(controls: list[PageControl]) -> PageControl | None:
    candidates = [
        control for control in controls
        if control.type in {"HyperlinkControl", "ButtonControl"}
        and re.match(r"^ch\s*-\s*\d+", _norm(control.name), re.I)
        and control.rect[0] > 250
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda control: (control.rect[1], control.rect[0]))
    return candidates[0]


def _find_recording_action(controls: list[PageControl]) -> PageControl | None:
    for control in controls:
        if control.type not in {"ButtonControl", "HyperlinkControl"}:
            continue
        label = _norm(control.name or control.value).lower()
        if not re.search(r"\b(?:watch again|watch|resume|play|continue)\b", label):
            continue
        if "mark as incomplete" in label or "notes & more" in label:
            continue
        if control.rect[0] < 600:
            continue
        return control
    return None


def open_recorded_subject(
    subject: str, timeout: float = 20.0
) -> tuple[bool, str]:
    subject = normalize_subject(subject) or "maths"
    hwnd, _controls, _snapshot = ensure_study_page(timeout=timeout)
    if not click_named(
        hwnd,
        ("all classes",),
        types={"HyperlinkControl", "ButtonControl"},
        exact=True,
        timeout=6.0,
    ):
        return False, "I couldn't find the All Classes link."
    _wait_for_page(
        hwnd,
        lambda title, _controls: "all classes" in title.lower(),
        12.0,
    )
    with _uia_context() as auto:
        controls = _collect_with_auto(auto, hwnd, keep_control=True)
        visible = [
            control for control in controls
            if _visible(control, win.get_window_rect(hwnd))
        ]
        card = _find_subject_card(visible, subject)
        if card is None or card.control is None:
            labels = [
                _norm(control.name) for control in visible
                if control.type == "HyperlinkControl" and "%" in control.name
            ][:8]
            hint = ", ".join(labels) if labels else "none"
            return False, f"I couldn't find a {subject} subject card. Available: {hint}."
        _activate_control_with_auto(auto, card.control, hwnd)
    time.sleep(1.2)
    with _uia_context() as auto:
        controls = _collect_with_auto(auto, hwnd, keep_control=True)
        visible = [
            control for control in controls
            if _visible(control, win.get_window_rect(hwnd))
        ]
        chapter = _find_first_chapter(visible)
        if chapter is None or chapter.control is None:
            return False, f"I couldn't find a {subject} chapter to open."
        _activate_control_with_auto(auto, chapter.control, hwnd)
    time.sleep(1.2)
    with _uia_context() as auto:
        controls = _collect_with_auto(auto, hwnd, keep_control=True)
        visible = [
            control for control in controls
            if _visible(control, win.get_window_rect(hwnd))
        ]
        action = _find_recording_action(visible)
        if action is None or action.control is None:
            return False, f"I couldn't find a recorded {subject} lecture."
        _activate_control_with_auto(auto, action.control, hwnd)
    time.sleep(1.0)
    return True, f"Opened the latest {subject} recording."


def activate_event(
    hwnd: int, event: StudyEvent, action_hint: str = ""
) -> tuple[bool, str]:
    hints = [
        hint for hint in (
            action_hint,
            "join now", "watch live", "join", "attend", "start class",
            "watch again", "watch", "resume", "play", "continue", "open",
        ) if hint
    ]
    with _uia_context() as auto:
        controls = _collect_with_auto(auto, hwnd, keep_control=True)
        visible = [
            control for control in controls
            if _visible(control, win.get_window_rect(hwnd))
        ]
        action: PageControl | None = None
        for hint in hints:
            for control in visible:
                if control.type not in INTERACTIVE_TYPES:
                    continue
                if not _inside(control.rect, event.rect, margin=12):
                    continue
                label = _norm(control.name or control.value).lower()
                if hint in label:
                    action = control
                    break
            if action is not None:
                break
        target = action
        if target is None:
            for control in visible:
                if control.type not in INTERACTIVE_TYPES:
                    continue
                label = _norm(control.name)
                if label == event.control_name and _area(control.rect) > 2000:
                    target = control
                    break
        if target is None and event.control_name:
            target = _find_control(
                visible, (event.control_name,), types=INTERACTIVE_TYPES, exact=True
            )
        if target is None or target.control is None:
            return False, f"I couldn't find the {event.title} control on the page."
        if _activate_control_with_auto(auto, target.control, hwnd):
            time.sleep(0.8)
            return True, f"Opened {_event_line(event)}."
    return False, f"I couldn't click {event.title}."


class StudyWatcher:
    """Poll the dashboard in the background and open a class when it goes live."""

    def __init__(
        self,
        subject: str = "",
        kind: str = "live",
        interval: float = 20.0,
        timeout: float = 2 * 60 * 60,
        on_open: Callable[[StudyEvent, str], None] | None = None,
        on_timeout: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.subject = normalize_subject(subject)
        self.kind = normalize_kind(kind)
        self.interval = max(5.0, float(interval))
        self.timeout = max(30.0, float(timeout))
        self.on_open = on_open
        self.on_timeout = on_timeout
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="NeedleStudyWatcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        deadline = time.monotonic() + self.timeout
        reported = ""
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                hwnd, _controls, snapshot = ensure_study_page(timeout=12.0)
                event = find_event(snapshot, self.subject, "live")
                if event is not None:
                    opened, message = activate_event(hwnd, event)
                    if opened:
                        if self.on_open is not None:
                            self.on_open(event, message)
                        return
                    self._last_error = message
                reported = ""
            except Exception as exc:
                self._last_error = str(exc)
                if reported != self._last_error:
                    reported = self._last_error
                    if self.on_error is not None:
                        self.on_error(self._last_error)
            self._stop.wait(self.interval)
        if not self._stop.is_set() and self.on_timeout is not None:
            self.on_timeout(self._last_error)
