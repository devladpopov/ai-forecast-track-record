#!/usr/bin/env python3
"""
Архивация источников в Wayback Machine.

Собирает URL из полей source и mirror всех прогнозов (плюс URL, переданные
аргументами) и просит web.archive.org сохранить их. Нужен, чтобы источник
резолюции нельзя было подменить или потерять.

Использование:
    python scripts/archive.py                       # все source/mirror из forecasts/
    python scripts/archive.py https://example.com   # конкретные ссылки
    python scripts/archive.py --dry-run
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from score import ForecastError, load_forecasts  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAVE = "https://web.archive.org/save/"
UA = "forecast-track-record/1.0 (+https://github.com/)"
TIMEOUT = 60


def collect_urls(directory: pathlib.Path) -> list:
    urls = []
    for f in load_forecasts(directory):
        for field in ("source", "mirror"):
            value = f.get(field)
            if value and str(value).startswith("http"):
                urls.append(str(value))
    return sorted(set(urls))


def save(url: str) -> tuple:
    req = urllib.request.Request(SAVE + url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return True, f"{resp.status} {resp.url}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # сеть, таймаут, троттлинг Wayback
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="если пусто, берутся source/mirror из forecasts/")
    ap.add_argument("--dir", default=str(ROOT / "forecasts"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=6.0, help="пауза между запросами, сек")
    args = ap.parse_args()

    if args.urls:
        urls = args.urls
    else:
        try:
            urls = collect_urls(pathlib.Path(args.dir))
        except ForecastError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 2

    if not urls:
        print("Нечего архивировать: ни одного source/mirror пока нет")
        return 0

    failures = 0
    for i, url in enumerate(urls):
        if args.dry_run:
            print(f"[dry-run] {url}")
            continue
        ok, detail = save(url)
        print(f"{'ok  ' if ok else 'fail'} {url}  {detail}")
        failures += 0 if ok else 1
        if i < len(urls) - 1:
            time.sleep(args.delay)

    # Wayback часто троттлит. Это не повод ронять сборку, но повод сказать вслух.
    if failures:
        print(f"Не сохранено: {failures} из {len(urls)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
