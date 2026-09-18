"""Миниатюрное дерево моделей для мока кластера — форма как у боевых моделей, объём как у фикстуры.

Форма повторяет ``simulation-model-example/cluster_mock``: входной файл ``.data`` с секциями и
``INCLUDE`` на файлы в подкаталогах; в секции расписания два файла — история (мало дат) и прогноз
(много дат). Основной файл расписания определяется по числу строк ``DATES``, а не по имени.
"""

from __future__ import annotations

from pathlib import Path

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _dates_block(start_year: int, count: int) -> str:
    lines: list[str] = []
    for index in range(count):
        year = start_year + index // 12
        month = MONTHS[index % 12]
        lines.append("DATES")
        lines.append(f"  1 {month} {year} /")
        lines.append("/")
        lines.append("")
        lines.append("WCONPROD")
        lines.append(f"  P{index % 4 + 1:02d} OPEN LRAT 3* 250 1* 120 /")
        lines.append("/")
        lines.append("")
    return "\n".join(lines)


def _schedule_file(title: str, start_year: int, dates: int) -> str:
    return "\n".join(
        [
            f"-- {title}",
            "WEFAC",
            "  P01 0.95 /",
            "/",
            "",
            _dates_block(start_year, dates),
        ]
    )


def _data_file(name: str, includes: dict[str, str]) -> str:
    def inc(path: str) -> str:
        return f"INCLUDE\n  '{path}' /\n"

    return "\n".join(
        [
            f"-- {name}: учебная модель мока кластера",
            "RUNSPEC",
            "",
            "TITLE",
            f"  {name}",
            "",
            "DIMENS",
            "  10 10 3 /",
            "",
            "OIL",
            "WATER",
            "",
            "START",
            "  1 JAN 2019 /",
            "",
            "GRID",
            "",
            inc(includes["grid"]),
            "PROPS",
            "",
            inc(includes["props"]),
            "SOLUTION",
            "",
            "EQUIL",
            "  2500 250 2600 0 1* 0 /",
            "",
            "SUMMARY",
            "",
            "FOPR",
            "FWPR",
            "",
            "SCHEDULE",
            "",
            "-- история работы скважин",
            inc(includes["history"]),
            "-- прогнозный файл расписания (его переписывает кластерный агент)",
            inc(includes["forecast"]),
            "END",
            "",
        ]
    )


def _grid_file(name: str) -> str:
    return "\n".join(
        [
            f"-- {name}: сетка (в моке — крошечная)",
            "SPECGRID",
            "  10 10 3 1 F /",
            "",
            "DXV",
            "  10*100 /",
            "DYV",
            "  10*100 /",
            "DZV",
            "  3*5 /",
            "TOPS",
            "  100*2500 /",
            "",
        ]
    )


def _props_file(name: str) -> str:
    return "\n".join(
        [
            f"-- {name}: свойства",
            "DENSITY",
            "  860 1010 0.9 /",
            "",
            "PVDO",
            "  200 1.12 1.2",
            "  300 1.10 1.3 /",
            "",
            "PVTW",
            "  250 1.02 4.0e-5 0.5 0 /",
            "",
        ]
    )


def build_model_tree(root: str | Path, *, forecast_dates: int = 14, history_dates: int = 4) -> dict[str, str]:
    """Создать две модели под ``root``. Возвращает пути (от корня) для тестов и лаборатории.

    ``SEVER`` — пути ``INCLUDE`` относительно каталога входного файла (обычный случай);
    ``YUG`` — прогнозный файл подключён путём от корня кластера (второй допустимый случай).
    """
    base = Path(root)
    sever = base / "MODELS" / "SEVER"
    yug = base / "MODELS" / "YUG"
    (sever / "INCLUDE" / "SCHEDULE").mkdir(parents=True, exist_ok=True)
    (sever / "INCLUDE" / "GRID").mkdir(parents=True, exist_ok=True)
    (sever / "INCLUDE" / "PROPS").mkdir(parents=True, exist_ok=True)
    (yug / "DATA").mkdir(parents=True, exist_ok=True)
    (yug / "INCLUDE" / "SCHEDULE").mkdir(parents=True, exist_ok=True)
    (yug / "INCLUDE" / "GRID").mkdir(parents=True, exist_ok=True)
    (yug / "INCLUDE" / "PROPS").mkdir(parents=True, exist_ok=True)

    (sever / "INCLUDE" / "GRID" / "GRID.GRDECL").write_text(_grid_file("SEVER"), encoding="utf-8")
    (sever / "INCLUDE" / "PROPS" / "PROPS.INC").write_text(_props_file("SEVER"), encoding="utf-8")
    (sever / "INCLUDE" / "SCHEDULE" / "HISTORY.INC").write_text(
        _schedule_file("история SEVER", 2019, history_dates), encoding="utf-8"
    )
    (sever / "INCLUDE" / "SCHEDULE" / "FORECAST.INC").write_text(
        _schedule_file("прогноз SEVER", 2024, forecast_dates), encoding="utf-8"
    )
    (sever / "SEVER.data").write_text(
        _data_file(
            "SEVER",
            {
                "grid": "INCLUDE/GRID/GRID.GRDECL",
                "props": "INCLUDE/PROPS/PROPS.INC",
                "history": "INCLUDE/SCHEDULE/HISTORY.INC",
                "forecast": "INCLUDE/SCHEDULE/FORECAST.INC",
            },
        ),
        encoding="utf-8",
    )

    (yug / "INCLUDE" / "GRID" / "GRID.GRDECL").write_text(_grid_file("YUG"), encoding="utf-8")
    (yug / "INCLUDE" / "PROPS" / "PROPS.INC").write_text(_props_file("YUG"), encoding="utf-8")
    (yug / "INCLUDE" / "SCHEDULE" / "HISTORY.INC").write_text(
        _schedule_file("история YUG", 2018, history_dates), encoding="utf-8"
    )
    (yug / "INCLUDE" / "SCHEDULE" / "PROGNOZ.INC").write_text(
        _schedule_file("прогноз YUG", 2025, forecast_dates + 3), encoding="utf-8"
    )
    (yug / "DATA" / "YUG.data").write_text(
        _data_file(
            "YUG",
            {
                "grid": "../INCLUDE/GRID/GRID.GRDECL",
                "props": "../INCLUDE/PROPS/PROPS.INC",
                "history": "../INCLUDE/SCHEDULE/HISTORY.INC",
                "forecast": "MODELS/YUG/INCLUDE/SCHEDULE/PROGNOZ.INC",
            },
        ),
        encoding="utf-8",
    )
    return {
        "sever": "MODELS/SEVER/SEVER.data",
        "sever_schedule": "MODELS/SEVER/INCLUDE/SCHEDULE/FORECAST.INC",
        "yug": "MODELS/YUG/DATA/YUG.data",
        "yug_schedule": "MODELS/YUG/INCLUDE/SCHEDULE/PROGNOZ.INC",
    }


def main_schedule_path(model: str) -> str:
    """Какой файл расписания считается основным в дереве мока (для проверок тестов)."""
    return {
        "MODELS/SEVER/SEVER.data": "MODELS/SEVER/INCLUDE/SCHEDULE/FORECAST.INC",
        "MODELS/YUG/DATA/YUG.data": "MODELS/YUG/INCLUDE/SCHEDULE/PROGNOZ.INC",
    }.get(model, "")
