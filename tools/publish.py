#!/usr/bin/env python3
"""Wraps build/groups.json and build/events.json into versioned files.

The app only accepts payloads with a known schemaVersion, so this is the last
step of every pipeline run before the commit.
"""

from __future__ import annotations

import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
BUILD = ROOT / "build"


def publish(source: str, key: str, target: str) -> bool:
    path = BUILD / source
    if not path.exists():
        print(f"skip {target}: {path} отсутствует")
        return False

    data = json.loads(path.read_text(encoding="utf-8"))
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": data.get("generatedAt"),
        key: data[key],
    }
    out = ROOT / target
    out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"{target}: {len(data[key])} записей, {out.stat().st_size / 1024:.0f} КБ")
    return True


def main() -> int:
    published = publish("groups.json", "groups", "catalog.json")
    published = publish("events.json", "events", "events.json") or published
    if not published:
        print("нечего публиковать")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
