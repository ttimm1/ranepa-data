#!/usr/bin/env python3
"""Collect upcoming RANEPA SPb events for the app.

The events section is a WordPress custom post type (`anons-sziu`).  The REST
API gives titles and body text; the event date lives in a JetEngine field on
the detail page, so each detail page is fetched once and cached.

Output: "../RANEPA Schedule/events.json" and "data/events-stats.json".
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
from datetime import datetime, timezone
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = "/tmp/ranepa-events/html"
OUT_EVENTS = os.environ.get("EVENTS_OUT", os.path.join(ROOT, "build", "events.json"))
OUT_STATS = os.environ.get("EVENTS_STATS", os.path.join(ROOT, "build", "events-stats.json"))

API = "https://spb.ranepa.ru/wp-json/wp/v2/anons-sziu"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Europe/Moscow")
except Exception:  # pragma: no cover - Windows without tzdata
    _TZ = timezone.utc

# Automation runs unattended for months: never pin the date again.
TODAY = datetime.now(_TZ).strftime("%Y-%m-%d")
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
MONTH_RE = "|".join(MONTHS)
DATE_RE = re.compile(
    r"(\d{1,2})\s*(?:[–—−-]\s*(\d{1,2}))?\s+(" + MONTH_RE + r")(?:\s*,?\s*(\d{4}))?",
    re.IGNORECASE,
)

CATEGORIES = [
    ("openDay", ["день открытых дверей", "открытых дверей"]),
    ("conference", ["конференц", "чтения", "форум", "симпозиум"]),
    ("olympiad", ["олимпиад", "конкурс", "чемпионат"]),
    ("seminar", ["семинар", "круглый стол", "вебинар"]),
    ("culture", ["фестивал", "фест", "концерт", "спектакл", "выставк", "кинопоказ", "литератур"]),
    ("sport", ["спорт", "турнир", "соревнован", "забег", "футбол", "баскетбол", "волейбол"]),
    ("career", ["карьер", "стажиров", "ваканс", "работодател", "трудоустрой"]),
    ("education", ["курс", "регистрац", "набор", "подготовит", "обучен"]),
    ("science", ["наук", "исследован", "экспертн"]),
]


def fetch(url: str, session: requests.Session, attempts: int = 3) -> str:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    path = os.path.join(CACHE, key + ".html")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return open(path, encoding="utf-8", errors="replace").read()

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            text = response.text
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return text
        except Exception as error:  # noqa: BLE001
            last = error
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def clean(text: str) -> str:
    text = html_lib.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return " ".join(text.split())


def parse_date(text: str, fallback_year: int) -> tuple[str, str | None] | None:
    match = DATE_RE.search(clean(text))
    if not match:
        return None
    start_day = int(match.group(1))
    end_day = int(match.group(2)) if match.group(2) else None
    month = MONTHS[match.group(3).lower()]
    year = int(match.group(4)) if match.group(4) else fallback_year

    def iso(day: int) -> str:
        return f"{year:04d}-{month:02d}-{day:02d}"

    start = iso(start_day)
    end = iso(end_day) if end_day and end_day != start_day else None
    return start, end


def category_for(title: str, text: str) -> str:
    # The title is the strongest signal; the body is only a fallback for
    # categories that are unambiguous in running text.
    heading = title.lower().replace("ё", "е")
    for name, keywords in CATEGORIES:
        if any(keyword in heading for keyword in keywords):
            return name

    body = text.lower().replace("ё", "е")
    for name in ("conference", "seminar", "olympiad", "sport", "culture"):
        keywords = next(k for n, k in CATEGORIES if n == name)
        if any(keyword in body for keyword in keywords):
            return name
    return "other"


def paragraphs_from_html(rendered: str) -> list[str]:
    soup = BeautifulSoup(rendered, "html.parser")
    for tag in soup(["script", "style", "form", "noscript"]):
        tag.decompose()

    result: list[str] = []
    seen: set[str] = set()
    for element in soup.find_all(["p", "li", "h2", "h3"]):
        text = clean(element.get_text(" ", strip=True))
        if len(text) < 8 or text in seen:
            continue
        if text.lower().startswith(("поделиться", "записаться", "регистрация по ссылке")):
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= 18:
            break
    return result


def extract_date(html: str, fallback_year: int) -> tuple[str, str | None] | None:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select(".jet-listing-dynamic-field__content"):
        text = clean(element.get_text(" ", strip=True))
        if not text or not re.search(r"\d", text):
            continue
        if not re.search(MONTH_RE, text, re.IGNORECASE):
            continue
        parsed = parse_date(text, fallback_year)
        if parsed:
            return parsed
    return None


def extract_image(rendered: str) -> str | None:
    soup = BeautifulSoup(rendered, "html.parser")
    image = soup.find("img")
    if image and image.get("src"):
        src = image["src"]
        if src.startswith("http"):
            return src
    return None


def main() -> int:
    os.makedirs(CACHE, exist_ok=True)
    session = requests.Session()

    posts: list[dict] = []
    for page in range(1, 4):
        response = session.get(
            API,
            params={
                "per_page": 100,
                "page": page,
                "_fields": "id,date,slug,link,title,content,featured_media",
            },
            headers=HEADERS,
            timeout=40,
        )
        if response.status_code != 200:
            break
        batch = response.json()
        if not batch:
            break
        posts.extend(batch)
        if len(batch) < 100:
            break

    print(f"REST posts: {len(posts)}", flush=True)

    def work(post: dict) -> dict | None:
        fallback_year = int(post["date"][:4])
        html = fetch(post["link"], session)
        parsed_date = extract_date(html, fallback_year)
        if not parsed_date:
            return None

        title = clean(post["title"]["rendered"])
        title = re.sub(r"(?<=[а-яa-z])(?=[«\"(])", " ", title)
        title = re.sub(r"\s+", " ", title).strip()
        body = paragraphs_from_html(post["content"]["rendered"])
        if not body:
            return None

        return {
            "id": post["id"],
            "title": title,
            "date": parsed_date[0],
            "endDate": parsed_date[1],
            "category": category_for(title, " ".join(body)),
            "summary": body[0][:220],
            "body": body,
            "url": post["link"],
            "image": extract_image(post["content"]["rendered"]),
        }

    events: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(work, post): post for post in posts}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                event = future.result()
            except Exception as error:  # noqa: BLE001
                print(f"failed: {error}", flush=True)
                event = None
            if event:
                events[event["id"]] = event
            if done % 25 == 0:
                print(f"[{done}/{len(posts)}] events={len(events)}", flush=True)

    # The site sometimes publishes the same event twice with slightly
    # different headlines; keep the shorter version.
    deduped: dict[int, dict] = {}
    seen_keys: set[tuple[str, str]] = set()
    for event in sorted(events.values(), key=lambda e: (e["date"], len(e["title"]))):
        normalized = re.sub(r"[^а-яa-z0-9]", "", event["title"].lower().replace("ё", "е"))
        key = (event["date"], normalized[-44:])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped[event["id"]] = event
    events = deduped

    today = TODAY
    upcoming = sorted(
        (e for e in events.values() if e["date"] >= today),
        key=lambda e: (e["date"], e["title"]),
    )
    recent = sorted(
        (e for e in events.values() if e["date"] < today),
        key=lambda e: e["date"],
        reverse=True,
    )[:8]

    selected = (recent + upcoming)[:110]
    selected.sort(key=lambda e: (e["date"], e["title"]))

    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": selected,
    }

    with open(OUT_EVENTS, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    stats = {
        "generatedAt": payload["generatedAt"],
        "restPosts": len(posts),
        "eventsWithDate": len(events),
        "upcoming": len(upcoming),
        "included": len(selected),
        "withImage": sum(1 for e in selected if e["image"]),
        "categories": {},
        "firstDate": selected[0]["date"] if selected else None,
        "lastDate": selected[-1]["date"] if selected else None,
        "sizeKB": round(os.path.getsize(OUT_EVENTS) / 1024),
    }
    for event in selected:
        stats["categories"][event["category"]] = stats["categories"].get(event["category"], 0) + 1

    os.makedirs(os.path.dirname(OUT_STATS), exist_ok=True)
    with open(OUT_STATS, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"wrote {OUT_EVENTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
