#!/usr/bin/env python3
"""Turn the crawled RANEPA pages into the app's bundled group catalogue.

The site mixes several things in the "Группы" column:

* individual groups              МН-3-26-07
* merged flows                  БИ-3-23-01-02
* slash groups                  ТД-5-26-01/02
* combined long flows           БИ-3-22-01-02-03-04-05-06-07-08-09

Students know their own atomic group (МН-3-26-07), so the catalogue contains
atomic groups only.  A page is attached to every atomic group that appears in
it, which means a lecture for the merged flow 01-02 is visible to both 01 and
02.

Output: "../RANEPA Schedule/groups.json" and "data/catalog-stats.json".
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = "/tmp/ranepa-raw/pages.json"
OUT_GROUPS = os.environ.get("GROUPS_OUT", os.path.join(ROOT, "build", "groups.json"))
OUT_STATS = os.environ.get("CATALOG_STATS", os.path.join(ROOT, "build", "catalog-stats.json"))

_TODAY = datetime.now(timezone.utc)
# The academic year starts in September; the course number of a group is
# derived from it, so it has to follow the calendar instead of a constant.
ACADEMIC_START = _TODAY.year if _TODAY.month >= 8 else _TODAY.year - 1
REFERENCE_DATE = _TODAY

GROUP_RE = re.compile(
    r"([А-ЯЁA-Z]{2,4})\s*-\s*(\d)\s*-\s*(\d{2})((?:\s*[-–/]\s*\d{2})*)"
)
# The law faculty publishes groups without separators: ЮР32529, НБ52401.
COMPACT_GROUP_RE = re.compile(
    r"(?<![А-ЯЁA-Z])([А-ЯЁA-Z]{2,4})(\d)(\d{2})(\d{2}(?:\s*[-–/]\s*\d{2})*)(?!\d)"
)
PAGE_ORDER = {"semester": 0, "other": 1, "session": 2, "attestation": 3, "gia": 4}


def normalize_header(value: str) -> str:
    return "".join(
        ch for ch in value.lower().replace("ё", "е") if ch.isalnum()
    )


def page_kind(url: str) -> str:
    slug = url.lower()
    if "sessiya" in slug or "sessii" in slug:
        return "session"
    if "gia" in slug or "gek" in slug:
        return "gia"
    if "attestac" in slug:
        return "attestation"
    if "semestr" in slug:
        return "semester"
    return "other"


def atomic_codes(text: str) -> list[str]:
    upper = text.upper()
    result: list[str] = []
    seen: set[str] = set()
    matches = list(GROUP_RE.finditer(upper)) + list(COMPACT_GROUP_RE.finditer(upper))
    for match in matches:
        prefix, level, year, chain = match.groups()
        base = f"{prefix}-{level}-{year}-"
        numbers = [n for n in re.split(r"[-–/]", chain) if n.strip()]
        if not numbers:
            numbers = ["01"]
        for number in numbers:
            number = number.strip()
            if number and f"{base}{number}" not in seen:
                seen.add(f"{base}{number}")
                result.append(f"{base}{number}")
    return result


def column_map(headers: list[str]) -> dict[str, int | None]:
    keys = [normalize_header(h) for h in headers]
    used: set[int] = set()
    result: dict[str, int | None] = {
        "weekday": None,
        "day": None,
        "month": None,
        "time": None,
        "groups": None,
        "type": None,
        "subject": None,
        "position": None,
        "teacher": None,
        "room": None,
    }

    def assign(field: str, predicate) -> None:
        for index, key in enumerate(keys):
            if index in used:
                continue
            if predicate(key):
                used.add(index)
                result[field] = index
                return

    assign(
        "weekday",
        lambda k: "деньнедел" in k or k in {"деньнед", "дн", "деньнедели"},
    )
    assign("day", lambda k: k == "дата" or k.startswith("дата"))
    if result["day"] is None:
        assign("day", lambda k: k in {"день", "число"})
    assign("month", lambda k: "месяц" in k)
    assign("time", lambda k: "время" in k)
    assign("groups", lambda k: "групп" in k)
    assign(
        "type",
        lambda k: "тип" in k or "видзанят" in k or "формаконтрол" in k,
    )
    assign(
        "subject",
        lambda k: any(
            word in k for word in ("предмет", "дисциплин", "мероприят", "наименован")
        ),
    )
    assign("position", lambda k: "должност" in k or "кафедра" in k)
    assign(
        "teacher",
        lambda k: "преподавател" in k or "преподовател" in k or k == "фио",
    )
    assign("room", lambda k: "ауд" in k or "адрес" in k)
    return result


DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?")


def parse_day_month(day_text: str, month_text: str | None):
    match = DATE_RE.search(day_text.replace(" ", ""))
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = match.group(3)
        if year:
            year = int(year)
            if year < 100:
                year += 2000
        if 1 <= day <= 31 and 1 <= month <= 12:
            return day, month, year

    if not month_text:
        return None
    digits = re.sub(r"\D", "", month_text)
    if not digits:
        return None
    month = int(digits)
    digits = re.sub(r"\D", "", day_text)
    if not digits:
        return None
    day = int(digits)
    if 1 <= day <= 31 and 1 <= month <= 12:
        return day, month, None
    return None


def clean_field(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        return ""
    if all(ch in "—-– " for ch in value):
        return ""
    return value


def parse_page(page: dict) -> list[dict]:
    headers = page["headers"]
    if not headers:
        return []
    columns = column_map(headers)
    if columns["day"] is None:
        return []

    rows: list[dict] = []
    for raw in page["rows"]:
        def value(field: str) -> str:
            index = columns[field]
            if index is None or index >= len(raw):
                return ""
            return " ".join(raw[index].split())

        groups_text = value("groups")
        groups = atomic_codes(groups_text)
        if not groups:
            continue

        parsed_date = parse_day_month(value("day"), value("month") or None)
        if parsed_date is None:
            continue

        subject = value("subject")
        if not subject:
            continue

        rows.append(
            {
                "day": parsed_date[0],
                "month": parsed_date[1],
                "year": parsed_date[2],
                "subject": subject,
                "groups": groups,
            }
        )
    return rows


def main() -> int:
    if not os.path.exists(RAW):
        print(f"missing {RAW}; run scrape.py first", file=sys.stderr)
        return 1

    pages = json.load(open(RAW, encoding="utf-8"))

    # group code -> {url: kind}
    group_pages: dict[str, dict[str, str]] = defaultdict(dict)
    page_rows: dict[str, int] = {}
    skipped_layouts: dict[str, int] = defaultdict(int)

    for page in pages:
        rows = parse_page(page)
        page_rows[page["url"]] = len(rows)
        if not rows:
            signature = " | ".join(page["headers"])
            skipped_layouts[signature] += 1
            continue
        kind = page_kind(page["url"])
        for row in rows:
            for group in row["groups"]:
                group_pages[group][page["url"]] = kind

    groups = []
    for code in sorted(group_pages):
        match = re.match(r"^([А-ЯЁ]{2,4})-(\d)-(\d{2})-(\d{2})$", code)
        if not match:
            continue
        program, _level, yy, _number = match.groups()
        entry_year = 2000 + int(yy)
        course = ACADEMIC_START - entry_year + 1
        if course < 1 or course > 6:
            course = 0

        page_list = [
            {"url": url, "kind": kind}
            for url, kind in sorted(
                group_pages[code].items(),
                key=lambda item: (PAGE_ORDER.get(item[1], 9), item[0]),
            )
        ]
        groups.append(
            {
                "code": code,
                "program": program,
                "course": course,
                "entryYear": entry_year,
                "pages": page_list,
            }
        )

    groups.sort(key=lambda g: (g["program"], g["course"], g["code"]))

    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "groups": groups,
    }

    os.makedirs(os.path.dirname(OUT_GROUPS), exist_ok=True)
    with open(OUT_GROUPS, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    programs = sorted({g["program"] for g in groups})
    stats = {
        "generatedAt": payload["generatedAt"],
        "pages": len(pages),
        "pagesWithLessons": sum(1 for count in page_rows.values() if count),
        "pagesWithoutLessons": sum(1 for count in page_rows.values() if not count),
        "uniqueUrlsWithGroups": len({u for pages in group_pages.values() for u in pages}),
        "groups": len(groups),
        "programs": programs,
        "pagesPerGroup": {
            "min": min((len(g["pages"]) for g in groups), default=0),
            "max": max((len(g["pages"]) for g in groups), default=0),
        },
        "lessonsParsed": sum(page_rows.values()),
        "topSkippedLayouts": sorted(
            skipped_layouts.items(), key=lambda item: -item[1]
        )[:5],
    }
    os.makedirs(os.path.dirname(OUT_STATS), exist_ok=True)
    with open(OUT_STATS, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\ncatalog: {OUT_GROUPS} ({os.path.getsize(OUT_GROUPS) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
