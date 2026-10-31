#!/usr/bin/env python3
"""
Проверка арифметики скоринга. Запуск: python tests/test_score.py

Считается на синтетических прогнозах из tests/fixtures/, чтобы ошибку в формуле
Brier или в разбивке калибровки было видно сразу, а не через полгода на реальных
данных.
"""

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import score  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: получили {got!r}, ждали {want!r}")
    else:
        print(f"ok  {name}")


def approx(name, got, want, eps=1e-9):
    if got is None or abs(got - want) > eps:
        FAILED.append(f"{name}: получили {got!r}, ждали ~{want!r}")
    else:
        print(f"ok  {name}")


def main():
    # Формула Brier на известных значениях
    approx("brier: сказал 100%, сбылось", score.brier([(99, 1.0)]), 0.0001)
    approx("brier: сказал 90%, не сбылось", score.brier([(90, 0.0)]), 0.81)
    approx("brier: монетка", score.brier([(50, 1.0), (50, 0.0)]), 0.25)
    check("brier: пусто", score.brier([]), None)

    # Границы дек: 10 попадает в первую деку, 11 во вторую, 100 в последнюю
    bins = {(b["low"], b["high"]): b["n"] for b in score.calibration_bins(
        [(10, 1.0), (11, 0.0), (90, 1.0), (91, 0.0)]
    )}
    check("дека 0-10", bins[(0, 10)], 1)
    check("дека 10-20", bins[(10, 20)], 1)
    check("дека 80-90", bins[(80, 90)], 1)
    check("дека 90-100", bins[(90, 100)], 1)

    # Фикстуры: 4 прогноза, 3 разрешено (одно аннулировано), 1 открыт и просрочен
    forecasts = score.load_forecasts(FIXTURES)
    check("фикстур загружено", len(forecasts), 4)

    _, stats = score.render(forecasts, dt.date(2027, 1, 15))
    check("всего", stats["total"], 4)
    check("открыто", stats["open"], 1)
    check("разрешено", stats["resolved"], 3)
    check("просрочено", stats["overdue"], 1)

    # В Brier идут только yes/no: (0.8-1)^2 + (0.3-0)^2 = 0.04 + 0.09, делим на 2
    approx("brier по фикстурам", stats["brier"], 0.065)
    approx("brier монетки", stats["brier_coin"], 0.25)

    # Аннулированный прогноз не должен попадать в скоринг
    annulled = [f for f in forecasts if f["resolution"] == "ambiguous"]
    check("аннулированных", len(annulled), 1)
    check("аннулированный вне скоринга", score.outcome(annulled[0]), None)

    # SVG рисуется и содержит точки калибровки
    svg = score.render_svg(forecasts)
    check("svg это svg", svg.startswith("<svg"), True)
    check("svg с точками", "<circle" in svg, True)

    # Валидация: 0% и 100% должны отклоняться
    bad = FIXTURES / "_tmp-invalid.md"
    bad.write_text(
        "---\nid: _tmp-invalid\nstatement: \"тест\"\ndeadline: 2027-01-01\n"
        "probability: 100\ncategory: models\ncreated: 2026-09-22\nstatus: open\n"
        "resolution: null\nresolved_at: null\nsource: null\n---\n\nтело\n",
        encoding="utf-8",
    )
    try:
        score.load_forecasts(FIXTURES)
        FAILED.append("валидация: probability=100 прошла, а не должна была")
    except score.ForecastError:
        print("ok  валидация: probability=100 отклонена")
    finally:
        bad.unlink()

    if FAILED:
        print("\nПРОВАЛЕНО:")
        for line in FAILED:
            print("  " + line)
        return 1
    print("\nвсе проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
