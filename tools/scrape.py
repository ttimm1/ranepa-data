#!/usr/bin/env python3
"""Download every RANEPA SPb schedule page and cache the raw tables.

The site is a WordPress installation; the schedule pages are listed in a
sitemap.  Every page contains one HTML table, but the column order differs
between page types (semester / session / attestation / GIA), so we keep the
raw header row and cells instead of assuming fixed positions.

Output: /tmp/ranepa-raw/pages.json  (raw, not committed)
        /tmp/ranepa-raw/html/<sha1>.html (download cache, resumable)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SITEMAP = "https://spb.ranepa.ru/wp-sitemap-posts-raspisanie-1.xml"
RAW_DIR = "/tmp/ranepa-raw"
HTML_DIR = os.path.join(RAW_DIR, "html")
PAGES_JSON = os.path.join(RAW_DIR, "pages.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

GROUP_RE = re.compile(
    r"([А-ЯЁ]{2,4})\s*-\s*(\d)\s*-\s*(\d{2})\s*-\s*(\d{2}(?:\s*-\s*\d{2})*)"
)


def fetch(url: str, session: requests.Session, attempts: int = 3) -> str:
    """Fetch a URL with retries.  Results are cached on disk by URL hash."""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    path = os.path.join(HTML_DIR, key + ".html")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            text = response.text
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return text
        except Exception as error:  # noqa: BLE001 - retry any network failure
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last_error}")


def parse_table(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return {"headers": [], "rows": []}

    trs = table.find_all("tr")
    if not trs:
        return {"headers": [], "rows": []}

    headers = [
        cell.get_text(" ", strip=True) for cell in trs[0].find_all(["th", "td"])
    ]
    rows = []
    for tr in trs[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    return {"headers": headers, "rows": rows}


def groups_in_text(text: str) -> list[str]:
    found = []
    for match in GROUP_RE.finditer(text.upper()):
        prefix, level, year, tail = match.groups()
        tail = re.sub(r"\s+", "", tail)
        found.append(f"{prefix}-{level}-{year}-{tail}")
    return found


def main() -> None:
    os.makedirs(HTML_DIR, exist_ok=True)
    session = requests.Session()

    sitemap_xml = fetch(SITEMAP, session)
    urls = re.findall(r"<loc>(.*?)</loc>", sitemap_xml)
    urls = sorted(set(urls))
    print(f"sitemap: {len(urls)} pages", flush=True)

    results = []
    started = time.time()

    def work(url: str) -> dict:
        html = fetch(url, session)
        table = parse_table(html)
        group_column = None
        for index, header in enumerate(table["headers"]):
            if "групп" in header.lower():
                group_column = index
                break
        groups: set[str] = set()
        if group_column is not None:
            for row in table["rows"]:
                if len(row) > group_column:
                    groups.update(groups_in_text(row[group_column]))
        return {
            "url": url,
            "slug": urlparse(url).path,
            "headers": table["headers"],
            "rows": table["rows"],
            "groups": sorted(groups),
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(work, url): url for url in urls}
        for done, future in enumerate(as_completed(futures), start=1):
            url = futures[future]
            try:
                results.append(future.result())
            except Exception as error:  # noqa: BLE001
                print(f"FAILED {url}: {error}", flush=True)
            if done % 25 == 0 or done == len(urls):
                print(
                    f"[{done}/{len(urls)}] {time.time() - started:.0f}s",
                    flush=True,
                )

    results.sort(key=lambda item: item["url"])
    with open(PAGES_JSON, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False)

    signature_counts: dict[str, int] = {}
    for page in results:
        signature = " | ".join(page["headers"])
        signature_counts[signature] = signature_counts.get(signature, 0) + 1

    print(f"\nwrote {len(results)} pages -> {PAGES_JSON}")
    print("\nheader signatures:")
    for signature, count in sorted(
        signature_counts.items(), key=lambda pair: -pair[1]
    ):
        print(f"  {count:4d}  {signature}")


if __name__ == "__main__":
    sys.exit(main())
