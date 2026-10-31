#!/usr/bin/env python3
"""
Скоринг трек-рекорда прогнозов: Brier score, калибровка, таблицы для README.

Зависимостей нет (только стандартная библиотека), чтобы GitHub Action
запускался за секунды и не ломался на обновлении пакетов.

Использование:
    python scripts/score.py                 # обновить README.md и docs/calibration.svg
    python scripts/score.py --check         # ничего не писать, упасть если README устарел
    python scripts/score.py --dir tests/fixtures --stdout   # посчитать по другой папке
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

# Консоль Windows по умолчанию cp1252/cp866 и падает на кириллице в выводе.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SVG_PATH = ROOT / "docs" / "calibration.svg"

START = "<!-- SCORE:START -->"
END = "<!-- SCORE:END -->"

YES = {"yes", "true", "да"}
NO = {"no", "false", "нет"}
AMBIGUOUS = {"ambiguous", "annulled", "n/a", "неоднозначно"}
NULLS = {"", "null", "none", "~"}

CATEGORY_TITLES = {
    "regulation-ru": "Регулирование РФ",
    "regulation-eu": "Регулирование EU",
    "models": "Релизы моделей",
    "pricing": "Цены API",
    "benchmarks": "Бенчмарки",
    "products": "Судьба продуктов",
    "ma": "M&A",
}


class ForecastError(Exception):
    pass


# --------------------------------------------------------------------------
# Парсинг
# --------------------------------------------------------------------------

def parse_scalar(raw: str):
    """YAML-подмножество: строка в кавычках, число, дата, null."""
    raw = raw.strip()
    if raw[:1] in {'"', "'"} and raw[-1:] == raw[:1] and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"')
    if raw.lower() in NULLS:
        return None
    return raw


def parse_front_matter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ForecastError(f"{path.name}: нет YAML front matter")
    _, fm, body = text.split("---", 2)
    data = {}
    for line in fm.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ForecastError(f"{path.name}: строка без ключа: {line!r}")
        key, _, value = line.partition(":")
        data[key.strip()] = parse_scalar(value)
    data["_body"] = body.strip()
    data["_path"] = path
    return data


def parse_date(value, path, field):
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ForecastError(f"{path.name}: поле {field} не дата ISO: {value!r}") from exc


def load_forecasts(directory: pathlib.Path) -> list:
    forecasts = []
    for path in sorted(directory.glob("*.md")):
        if path.name.upper().startswith("TEMPLATE"):
            continue
        f = parse_front_matter(path)
        for field in ("id", "statement", "deadline", "probability", "status"):
            if not f.get(field):
                raise ForecastError(f"{path.name}: обязательное поле {field} пустое")

        try:
            p = float(f["probability"])
        except ValueError as exc:
            raise ForecastError(
                f"{path.name}: probability не число: {f['probability']!r}"
            ) from exc
        if not 1 <= p <= 99:
            raise ForecastError(
                f"{path.name}: probability={p}. Допустимо 1..99: "
                "0 и 100 означают абсолютную уверенность и делают Brier неинформативным"
            )
        f["probability"] = p

        f["deadline_date"] = parse_date(f["deadline"], path, "deadline")
        f["resolved_at_date"] = parse_date(f.get("resolved_at"), path, "resolved_at")

        status = str(f["status"]).lower()
        if status not in {"open", "resolved"}:
            raise ForecastError(f"{path.name}: status={status!r}, допустимо open/resolved")
        f["status"] = status

        resolution = str(f.get("resolution")).lower() if f.get("resolution") else None
        if status == "resolved":
            if resolution is None:
                raise ForecastError(f"{path.name}: status=resolved, но resolution пустой")
            if resolution not in YES | NO | AMBIGUOUS:
                raise ForecastError(f"{path.name}: resolution={resolution!r} не распознан")
            if f["resolved_at_date"] is None:
                raise ForecastError(f"{path.name}: status=resolved, но resolved_at пустой")
            if not f.get("source"):
                raise ForecastError(
                    f"{path.name}: резолюция без source. Каждая резолюция обязана "
                    "ссылаться на публичный источник"
                )
        elif resolution is not None:
            raise ForecastError(f"{path.name}: status=open, но resolution заполнен")
        f["resolution"] = resolution

        if f["id"] != path.stem:
            raise ForecastError(f"{path.name}: id={f['id']!r} не совпадает с именем файла")

        forecasts.append(f)
    return forecasts


# --------------------------------------------------------------------------
# Метрики
# --------------------------------------------------------------------------

def outcome(f: dict):
    """1.0 если событие произошло, 0.0 если нет, None если не считается."""
    if f["status"] != "resolved" or f["resolution"] in AMBIGUOUS:
        return None
    return 1.0 if f["resolution"] in YES else 0.0


def brier(pairs):
    if not pairs:
        return None
    return sum((p / 100 - o) ** 2 for p, o in pairs) / len(pairs)


def calibration_bins(pairs, width=10):
    """Разбивка по декам вероятности: сколько сказано, сколько сбылось."""
    bins = []
    for low in range(0, 100, width):
        high = low + width
        if low == 0:
            chunk = [(p, o) for p, o in pairs if low <= p <= high]
        else:
            chunk = [(p, o) for p, o in pairs if low < p <= high]
        if not chunk:
            bins.append({"low": low, "high": high, "n": 0})
            continue
        bins.append(
            {
                "low": low,
                "high": high,
                "n": len(chunk),
                "mean_p": sum(p for p, _ in chunk) / len(chunk),
                "hit_rate": 100 * sum(o for _, o in chunk) / len(chunk),
            }
        )
    return bins


def md_escape(text) -> str:
    return str(text).replace("|", "\\|")


# --------------------------------------------------------------------------
# Рендер
# --------------------------------------------------------------------------

def render(forecasts: list, today: dt.date):
    resolved = [f for f in forecasts if f["status"] == "resolved"]
    open_ = [f for f in forecasts if f["status"] == "open"]
    overdue = [f for f in open_ if f["deadline_date"] and f["deadline_date"] < today]
    scored = [
        (f["probability"], outcome(f)) for f in resolved if outcome(f) is not None
    ]

    bs = brier(scored)
    coin = brier([(50.0, o) for _, o in scored])
    base_rate = (sum(o for _, o in scored) / len(scored)) if scored else None
    base = brier([(100 * base_rate, o) for _, o in scored]) if scored else None

    # Дата генерации сознательно не печатается: ежедневный крон иначе менял бы
    # README каждые сутки и засорял историю коммитов, которая здесь и есть
    # доказательство таймстампов. Свежесть видна по дате последнего коммита.
    lines = [START, ""]
    lines.append("**Счёт**")
    lines.append("")
    lines.append("```")
    lines.append(f"Всего прогнозов      {len(forecasts)}")
    lines.append(f"Открыто              {len(open_)}")
    lines.append(f"Разрешено            {len(resolved)}")
    lines.append(f"  сбылось            {sum(1 for f in resolved if outcome(f) == 1.0)}")
    lines.append(f"  не сбылось         {sum(1 for f in resolved if outcome(f) == 0.0)}")
    lines.append(f"  аннулировано       {sum(1 for f in resolved if outcome(f) is None)}")
    lines.append(f"Просрочено           {len(overdue)}")
    lines.append("")
    if bs is None:
        lines.append("Brier score          пока нет разрешённых прогнозов")
    else:
        lines.append(f"Brier score          {bs:.4f}   (0 = идеально, 0.25 = монетка)")
        lines.append(f"  монетка 50%        {coin:.4f}")
        lines.append(f"  базовая ставка     {base:.4f}   (всегда {100 * base_rate:.0f}%)")
        verdict = "лучше монетки" if bs < coin else "хуже монетки"
        lines.append(f"  вердикт            {verdict}")
    lines.append("```")
    lines.append("")

    lines.append("**Калибровка по декам**")
    lines.append("")
    lines.append("```")
    lines.append("вероятность   прогнозов   сбылось   идеально")
    for b in calibration_bins(scored):
        rng = f"{b['low']}-{b['high']}%"
        if b["n"] == 0:
            lines.append(f"{rng:<13} {'0':>9}   {'-':>7}   {'-':>8}")
        else:
            lines.append(
                f"{rng:<13} {b['n']:>9}   {b['hit_rate']:>6.0f}%   {b['mean_p']:>7.0f}%"
            )
    lines.append("```")
    lines.append("")
    lines.append("![Кривая калибровки](docs/calibration.svg)")
    lines.append("")

    if overdue:
        lines.append("**Просрочены, ждут резолюции**")
        lines.append("")
        lines.append("| дедлайн | вероятность | прогноз |")
        lines.append("|---|---|---|")
        for f in sorted(overdue, key=lambda x: x["deadline_date"]):
            lines.append(
                f"| {f['deadline']} | {f['probability']:.0f}% | "
                f"[{md_escape(f['statement'])}](forecasts/{f['id']}.md) |"
            )
        lines.append("")

    lines.append("**Разрешённые прогнозы**")
    lines.append("")
    if not resolved:
        lines.append("Пока ни одного. Первые дедлайны впереди.")
    else:
        lines.append("| резолюция | сказано | итог | вклад в Brier | прогноз |")
        lines.append("|---|---|---|---|---|")
        for f in sorted(resolved, key=lambda x: x["resolved_at_date"], reverse=True):
            o = outcome(f)
            if o is None:
                verdict, contrib = "аннулирован", "-"
            else:
                verdict = "сбылось" if o == 1.0 else "не сбылось"
                contrib = f"{(f['probability'] / 100 - o) ** 2:.3f}"
            lines.append(
                f"| {f['resolved_at']} | {f['probability']:.0f}% | {verdict} | {contrib} | "
                f"[{md_escape(f['statement'])}](forecasts/{f['id']}.md) |"
            )
    lines.append("")

    lines.append("**Открытые прогнозы**")
    lines.append("")
    if not open_:
        lines.append("Пока ни одного.")
    else:
        lines.append("| дедлайн | вероятность | тема | прогноз |")
        lines.append("|---|---|---|---|")
        for f in sorted(open_, key=lambda x: x["deadline_date"]):
            cat = CATEGORY_TITLES.get(str(f.get("category", "")), f.get("category") or "-")
            lines.append(
                f"| {f['deadline']} | {f['probability']:.0f}% | {cat} | "
                f"[{md_escape(f['statement'])}](forecasts/{f['id']}.md) |"
            )
    lines.append("")
    lines.append("_Таблица собрана автоматически: `scripts/score.py`. Руками не редактируется._")
    lines.append("")
    lines.append(END)

    stats = {
        "generated": today.isoformat(),
        "total": len(forecasts),
        "open": len(open_),
        "resolved": len(resolved),
        "overdue": len(overdue),
        "brier": bs,
        "brier_coin": coin,
        "brier_base_rate": base,
        "calibration": calibration_bins(scored),
    }
    return "\n".join(lines), stats


def render_svg(forecasts: list) -> str:
    """Кривая калибровки без matplotlib: сказано по X, сбылось по Y."""
    pairs = [
        (f["probability"], outcome(f))
        for f in forecasts
        if f["status"] == "resolved" and outcome(f) is not None
    ]
    bins = [b for b in calibration_bins(pairs) if b["n"]]

    w = h = 420
    pad = 50
    size = w - 2 * pad

    def sx(p):
        return pad + size * p / 100

    def sy(p):
        return h - pad - size * p / 100

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="system-ui, sans-serif" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
    ]
    for t in range(0, 101, 25):
        parts.append(
            f'<line x1="{sx(t):.1f}" y1="{pad}" x2="{sx(t):.1f}" y2="{h - pad}" '
            'stroke="#eeeeee"/>'
        )
        parts.append(
            f'<line x1="{pad}" y1="{sy(t):.1f}" x2="{w - pad}" y2="{sy(t):.1f}" '
            'stroke="#eeeeee"/>'
        )
        parts.append(
            f'<text x="{sx(t):.1f}" y="{h - pad + 16}" text-anchor="middle" '
            f'fill="#666666">{t}%</text>'
        )
        parts.append(
            f'<text x="{pad - 8}" y="{sy(t) + 4:.1f}" text-anchor="end" '
            f'fill="#666666">{t}%</text>'
        )
    parts.append(
        f'<line x1="{sx(0):.1f}" y1="{sy(0):.1f}" x2="{sx(100):.1f}" y2="{sy(100):.1f}" '
        'stroke="#bbbbbb" stroke-dasharray="4 4"/>'
    )
    parts.append(
        f'<rect x="{pad}" y="{pad}" width="{size}" height="{size}" fill="none" '
        'stroke="#333333"/>'
    )

    if bins:
        pts = " ".join(f"{sx(b['mean_p']):.1f},{sy(b['hit_rate']):.1f}" for b in bins)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="#1f6feb" stroke-width="2"/>'
        )
        for b in bins:
            r = 3 + min(b["n"], 12) * 0.6
            parts.append(
                f'<circle cx="{sx(b["mean_p"]):.1f}" cy="{sy(b["hit_rate"]):.1f}" '
                f'r="{r:.1f}" fill="#1f6feb"/>'
            )
    else:
        parts.append(
            f'<text x="{w / 2}" y="{h / 2}" text-anchor="middle" fill="#999999">'
            "нет разрешённых прогнозов</text>"
        )

    parts.append(
        f'<text x="{w / 2}" y="{h - 10}" text-anchor="middle" fill="#333333">'
        "сказанная вероятность</text>"
    )
    parts.append(
        f'<text x="14" y="{h / 2}" text-anchor="middle" fill="#333333" '
        f'transform="rotate(-90 14 {h / 2})">доля сбывшихся</text>'
    )
    parts.append(
        f'<text x="{w / 2}" y="26" text-anchor="middle" fill="#111111" font-size="13">'
        "Кривая калибровки</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def splice(readme_text: str, block: str) -> str:
    if START not in readme_text or END not in readme_text:
        raise ForecastError(f"В README.md нет маркеров {START} / {END}")
    head = readme_text.split(START)[0]
    tail = readme_text.split(END, 1)[1]
    return head + block + tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "forecasts"))
    ap.add_argument("--check", action="store_true", help="не писать, упасть если README устарел")
    ap.add_argument("--stdout", action="store_true", help="напечатать блок и выйти")
    ap.add_argument("--json", action="store_true", help="напечатать метрики в JSON")
    ap.add_argument("--today", default=None, help="дата для детерминированных тестов")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    try:
        forecasts = load_forecasts(pathlib.Path(args.dir))
    except ForecastError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    block, stats = render(forecasts, today)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.stdout:
        print(block)
        return 0

    current = README.read_text(encoding="utf-8")
    updated = splice(current, block)

    if args.check:
        if updated != current:
            print("README.md устарел: запустите python scripts/score.py", file=sys.stderr)
            return 1
        print("README.md актуален")
        return 0

    README.write_text(updated, encoding="utf-8", newline="\n")
    SVG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SVG_PATH.write_text(render_svg(forecasts), encoding="utf-8", newline="\n")
    print(
        f"README.md обновлён: {stats['total']} прогнозов, "
        f"{stats['resolved']} разрешено, Brier {stats['brier']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
