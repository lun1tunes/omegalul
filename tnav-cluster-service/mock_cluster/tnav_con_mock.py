#!/usr/bin/env python3
"""Заглушка консольного tNavigator: ведёт себя как настоящий расчёт, только быстро и без физики.

Поведение взято из мануала tNavigator (консольная версия):

* вызов ``tNavigator-con [опции] <модель>.data``; неизвестные опции игнорируются, как и в реальном
  запуске через обёртки кластера; ``--version`` и ``--help`` отвечают и выходят;
* рядом с моделью создаётся каталог ``RESULTS`` и в нём ``<имя>.log`` (отчёт о расчёте), ``<имя>.err``
  (ошибки), ``<имя>.end`` (сводка), ``<имя>.lock`` (живёт, пока идёт расчёт);
* в лог идут строки чтения модели, шаги расчёта с датами и итоговая строка
  ``Время расчёта=00.00.03, CPU=00.00.09, шагов=14, NI=31, LI=352``;
* при отсутствии лицензии (``--no-license-exit``) код выхода 69.

Ручки для тестов (переменные окружения): ``TNAV_MOCK_SECONDS`` — сколько «считать» (по умолчанию 2),
``TNAV_MOCK_FAIL=1`` — упасть с ошибкой входных данных, ``TNAV_MOCK_NO_LICENSE=1`` — нет лицензии,
``TNAV_MOCK_HANG=1`` — не дописывать итог (имитация зависшего расчёта).
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

VERSION = "tNavigator 24.4 (mock console build)"
USAGE = (
    "Использование: tNavigator-con [опции] <модель>.data\n"
    "  --cpu-num=N            число ядер на узле\n"
    "  --mpi-num=N            число MPI-процессов\n"
    "  --log-lang=ru|en       язык журнала расчёта\n"
    "  --dump-res             сохранять результаты расчёта\n"
    "  --max-calc-time=H      ограничение времени расчёта, часы\n"
    "  --no-license-exit      выйти с кодом 69, если нет лицензии\n"
    "  --nosim                только проверить входные данные\n"
)
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DATES_RE = re.compile(r"^\s*DATES\b", re.M)
DATE_LINE_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\s*/", re.M)


def include_paths(text: str) -> list[str]:
    """Пути из ``INCLUDE`` (ключевое слово на одной строке, путь на следующей) — как читает симулятор."""
    lines = text.splitlines()
    out: list[str] = []
    for index, line in enumerate(lines):
        if not line.split("--")[0].strip().upper().startswith("INCLUDE"):
            continue
        for follower in lines[index + 1 : index + 4]:
            body = follower.split("--")[0].strip()
            if not body:
                continue
            out.append(body.rstrip("/").strip().strip("'\""))
            break
    return [path for path in out if path]


def clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}.{total % 3600 // 60:02d}.{total % 60:02d}"


def schedule_dates(data_path: Path) -> list[str]:
    """Даты расчётных шагов: из самого ``.data`` и из подключённых им файлов (как читает симулятор)."""
    texts: list[str] = []
    try:
        head = data_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Расчётные шаги берутся из секции расписания, а не из START в шапке модели.
    texts.append(head.split("\nSCHEDULE", 1)[1] if "\nSCHEDULE" in head else head)
    for raw in include_paths(head):
        for candidate in (data_path.parent / raw, Path(raw)):
            try:
                if candidate.is_file():
                    texts.append(candidate.read_text(encoding="utf-8", errors="replace"))
                    break
            except OSError:
                continue
    dates: list[str] = []
    for text in texts:
        for day, month, year in DATE_LINE_RE.findall(text):
            dates.append(f"{int(day)} {month} {year}")
    if dates:
        return dates
    count = sum(len(DATES_RE.findall(text)) for text in texts)
    return [f"1 {MONTHS[i % 12]} {2024 + i // 12}" for i in range(count or 5)]


def main(argv: list[str]) -> int:
    args = argv[1:]
    if any(arg in ("-v", "--version") for arg in args):
        print(VERSION)
        return 0
    if any(arg in ("-h", "--help", "--full-help") for arg in args):
        print(VERSION)
        print(USAGE)
        return 0
    positional = [arg for arg in args if not arg.startswith("-")]
    if not positional:
        sys.stderr.write("Ошибка: не указан входной файл модели\n" + USAGE)
        return 2
    model = Path(positional[-1])
    if not model.is_file():
        sys.stderr.write(f"Ошибка: входной файл модели не найден: {model}\n")
        return 2
    if os.getenv("TNAV_MOCK_NO_LICENSE") == "1" and "--no-license-exit" in args:
        sys.stderr.write("Ошибка: лицензия на расчёт не получена\n")
        return 69

    results = model.parent / "RESULTS"
    results.mkdir(parents=True, exist_ok=True)
    log_path = results / f"{model.stem}.log"
    err_path = results / f"{model.stem}.err"
    end_path = results / f"{model.stem}.end"
    lock_path = results / f"{model.stem}.lock"
    lock_path.write_text(f"pid={os.getpid()}\n", encoding="utf-8")

    dates = schedule_dates(model)
    total_seconds = float(os.getenv("TNAV_MOCK_SECONDS") or 2)
    pause = total_seconds / max(1, len(dates))
    started = time.time()
    cpu_num = next((arg.split("=", 1)[1] for arg in args if arg.startswith("--cpu-num=")), "1")

    with log_path.open("w", encoding="utf-8") as log:

        def write(line: str) -> None:
            log.write(line + "\n")
            log.flush()
            print(line, flush=True)

        write(VERSION)
        write(f"Команда: {' '.join(argv)}")
        write(f"Чтение модели: {model.name}")
        write("Формат входных данных: e1")
        write(f"Число ядер: {cpu_num}")
        write("Статистика соединений: всего=1300, геометрических=1300, выклин.=0, несоседн.=0")
        if os.getenv("TNAV_MOCK_FAIL") == "1":
            message = "ERROR: ключевое слово WCONPROD: скважина P09 не объявлена в WELSPECS"
            write(message)
            err_path.write_text(message + "\nРасчёт прерван на чтении входных данных\n", encoding="utf-8")
            end_path.write_text("Ошибок: 1, проблем: 0, предупреждений: 2\n", encoding="utf-8")
            lock_path.unlink(missing_ok=True)
            return 1
        if "--nosim" in args:
            write("Проверка входных данных завершена, расчёт не запускался")
            end_path.write_text("Ошибок: 0, проблем: 0, предупреждений: 0\n", encoding="utf-8")
            lock_path.unlink(missing_ok=True)
            return 0
        write("Расчёт начат")
        newton = 0
        linear = 0
        for index, date in enumerate(dates, start=1):
            time.sleep(pause)
            newton += 2 + index % 3
            linear += 18 + index % 7
            write(f"Шаг {index}: {date}, dt=30 сут, Newton #={2 + index % 3}, |F|=8.7e+003, its={18 + index % 7}. ({index})")
        if os.getenv("TNAV_MOCK_HANG") == "1":
            write("Шаг прерван: расчёт остановлен по внешнему сигналу")
            return 0  # ни итоговой строки, ни снятой блокировки — так выглядит аварийный обрыв
        elapsed = time.time() - started
        write("Статистика загрузки: 56608,12235,16403")
        write(
            f"Время расчёта={clock(elapsed)}, CPU={clock(elapsed * float(cpu_num or 1))}, "
            f"шагов={len(dates)}, NI={newton}, LI={linear}"
        )
        write("Расчёт завершён")
    end_path.write_text("Ошибок: 0, проблем: 0, предупреждений: 3\n", encoding="utf-8")
    err_path.write_text("", encoding="utf-8")
    lock_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
