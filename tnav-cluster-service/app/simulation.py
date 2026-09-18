"""Запуск расчёта tNavigator на кластере и чтение его состояния из лог-файлов.

Модели pydantic v2: ``SimulatorProfile`` (что запускаем), ``RunHandle`` (что запустили),
``LogDigest`` (что видно в логе), ``RunStatus`` (как идёт расчёт).

Факты о консольной версии — мануал tNavigator:

* раздел 8: запуск ``<путь к tNavigator-con> <опции> <модель>.data``; опции — 8.1;
* раздел 9.1.1: рядом с моделью появляется каталог ``RESULTS`` c ``<имя>.log`` (полный отчёт о расчёте),
  ``<имя>.err`` (ошибки входных данных и расчёта), ``<имя>.end`` (сводка ошибок/проблем/предупреждений),
  ``<имя>.lock`` (живёт, пока модель считается; остаётся после аварийного завершения);
* раздел 9.3: в лог пишутся «Статистика загрузки: …» и итог
  ``Время расчёта=01.50.30, CPU=01.15.15, шагов=550, NI=819, LI=18678``;
* опция ``--no-license-exit`` даёт код выхода 69, если на первом шаге нет лицензии (8.1.3).

Строки прогресса по шагам в мануале не стандартизованы (зависят от версии и ``REPORTFILE``), поэтому
разбор терпимый: берём последнюю дату расчёта, число пройденных шагов и итоговую строку, если она есть.
Оценка остатка — от доли пройденных дат из файла schedule, а не от обещаний симулятора.
"""

from __future__ import annotations

import re
import shlex
import time
import uuid
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Итоговая строка лога (RU и EN варианты).
_TOTAL_RE = re.compile(r"(?:Врем[яa]\s*расч[ёе]т[аa]|Total\s*elapsed|Elapsed)\s*=?\s*(\d{1,4}[.:]\d{2}[.:]\d{2})", re.I)
_CPU_RE = re.compile(r"\bCPU\s*=?\s*(\d{1,4}[.:]\d{2}[.:]\d{2})", re.I)
_STEPS_RE = re.compile(r"(?:шагов|steps)\s*=\s*(\d+)", re.I)
_NI_RE = re.compile(r"\bNI\s*=\s*(\d+)")
_LI_RE = re.compile(r"\bLI\s*=\s*(\d+)")
#: Дата расчётного шага в стиле ECLIPSE/tNavigator: «1 JUL 2019», «01 ЯНВ 2030».
_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Z]{3,4}|[А-Я]{3,4})\s+(\d{4})\b")
_ERROR_RE = re.compile(r"(?:^|[\s:])(ERROR|FATAL|Ошибка|ОШИБКА|Error)\b")
_WARN_RE = re.compile(r"(?:^|[\s:])(WARNING|Warning|Предупреждение)\b")
#: Сводка .end файла: числа рядом со словами об ошибках/проблемах/предупреждениях.
_END_COUNT_RE = re.compile(r"(errors?|ошиб\w*|problems?|проблем\w*|warnings?|предупрежд\w*)\D{0,20}(\d+)", re.I)
#: Код выхода tNavigator при отсутствии лицензии (мануал 8.1.3, --no-license-exit).
NO_LICENSE_EXIT_CODE = 69

RunState = Literal["starting", "running", "finished", "failed", "unknown"]


def parse_clock(value: str) -> int:
    """``01.50.30`` (часы.минуты.секунды) → секунды. Неразборчивое значение → 0."""
    parts = re.split(r"[.:]", str(value or "").strip())
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return 0
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds


def duration_ru(seconds: float) -> str:
    """Секунды → русская проза: «2 часа 13 минут», «около минуты», «3 суток 4 часа»."""
    total = max(0, int(seconds or 0))
    if total < 90:
        return "около минуты"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    chunks: list[str] = []
    if days:
        chunks.append(_plural(days, "сутки", "суток", "суток"))
    if hours:
        chunks.append(_plural(hours, "час", "часа", "часов"))
    if minutes and not days:
        chunks.append(_plural(minutes, "минута", "минуты", "минут"))
    return " ".join(chunks) or "около минуты"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return f"{n} {one}"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


class SimulatorProfile(BaseModel):
    """Команда расчёта: как есть из настроек сервиса, с единственной подстановкой ``{model}``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str

    @field_validator("command")
    @classmethod
    def _has_model(cls, value: str) -> str:
        if "{model}" not in str(value):
            raise ValueError("в команде расчёта нужна подстановка {model}")
        return str(value)

    @property
    def program(self) -> str:
        parts = shlex.split(self.command.replace("{model}", "MODEL"))
        return parts[0] if parts else ""

    @property
    def options(self) -> list[str]:
        parts = shlex.split(self.command.replace("{model}", "MODEL"))
        return [part for part in parts[1:] if part != "MODEL"]

    def rendered(self, model_path: str) -> str:
        """Готовая команда для кластера: путь к модели экранирован."""
        return self.command.replace("{model}", shlex.quote(str(model_path)))

    def launch_script(self, model_path: str, *, console_path: str, workdir: str) -> str:
        """Скрипт запуска в фоне: расчёт живёт дольше SSH-сессии, на stdout — идентификатор процесса.

        ``nohup`` отвязывает процесс от сессии, вывод консоли идёт в файл рядом с результатами.
        """
        return (
            f"cd {shlex.quote(workdir)} && "
            f"nohup {self.rendered(model_path)} > {shlex.quote(console_path)} 2>&1 < /dev/null & "
            "echo $!"
        )


class ResultsLayout(BaseModel):
    """Файлы, которые tNavigator создаёт в каталоге ``RESULTS`` (мануал 9.1.1). Пути — от корня кластера."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    results_dir: str
    log: str
    err: str
    end: str
    lock: str
    console: str

    @classmethod
    def for_model(cls, model_path: str, *, results_dirname: str = "RESULTS") -> "ResultsLayout":
        path = PurePosixPath(str(model_path))
        stem = path.stem
        results = path.parent / results_dirname
        return cls(
            results_dir=str(results),
            log=str(results / f"{stem}.log"),
            err=str(results / f"{stem}.err"),
            end=str(results / f"{stem}.end"),
            lock=str(results / f"{stem}.lock"),
            console=str(results / f"{stem}.console.log"),
        )


class RunHandle(BaseModel):
    """Запущенный расчёт: что считаем, где смотреть, с какого момента ждём."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:10]}")
    model_path: str
    model_name: str
    results: ResultsLayout
    pid: int = 0
    started_at: float = Field(default_factory=time.time)
    #: Сколько дат в файле schedule этой модели — из него считается остаток времени.
    dates_total: int = 0
    command: str = ""

    @property
    def elapsed_s(self) -> float:
        return max(0.0, time.time() - float(self.started_at or 0))


class LogDigest(BaseModel):
    """Что видно в ``<имя>.log`` прямо сейчас."""

    model_config = ConfigDict(extra="forbid")

    steps: int = 0
    current_date: str = ""
    elapsed_s: int = 0
    cpu_s: int = 0
    newton_iterations: int = 0
    linear_iterations: int = 0
    finished: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: int = 0
    tail: str = ""

    @classmethod
    def parse(cls, text: str, *, tail_lines: int = 12) -> "LogDigest":
        body = str(text or "")
        lines = [line.rstrip() for line in body.splitlines() if line.strip()]
        dates = _DATE_RE.findall(body)
        total = _TOTAL_RE.search(body)
        cpu = _CPU_RE.search(body)
        steps_field = _STEPS_RE.search(body)
        steps = int(steps_field.group(1)) if steps_field else len(dates)
        errors = [line.strip()[:200] for line in lines if _ERROR_RE.search(line)]
        return cls(
            steps=steps,
            current_date=" ".join(dates[-1]) if dates else "",
            elapsed_s=parse_clock(total.group(1)) if total else 0,
            cpu_s=parse_clock(cpu.group(1)) if cpu else 0,
            newton_iterations=int(_NI_RE.search(body).group(1)) if _NI_RE.search(body) else 0,
            linear_iterations=int(_LI_RE.search(body).group(1)) if _LI_RE.search(body) else 0,
            finished=bool(total),
            errors=errors[-5:],
            warnings=sum(1 for line in lines if _WARN_RE.search(line)),
            tail="\n".join(lines[-tail_lines:]),
        )


class EndReport(BaseModel):
    """Сводка ``<имя>.end``: сколько ошибок, проблем и предупреждений нашёл симулятор."""

    model_config = ConfigDict(extra="forbid")

    errors: int = 0
    problems: int = 0
    warnings: int = 0
    text: str = ""

    @classmethod
    def parse(cls, text: str) -> "EndReport":
        body = str(text or "")
        counts = {"errors": 0, "problems": 0, "warnings": 0}
        for word, number in _END_COUNT_RE.findall(body):
            key = "errors" if word.lower().startswith(("error", "ошиб")) else "problems" if word.lower().startswith(("problem", "проблем")) else "warnings"
            counts[key] = int(number)
        return cls(**counts, text=body.strip()[:400])


class RunStatus(BaseModel):
    """Состояние расчёта для агента и для инженера."""

    model_config = ConfigDict(extra="forbid")

    state: RunState = "unknown"
    digest: LogDigest = Field(default_factory=LogDigest)
    end_report: EndReport | None = None
    process_alive: bool = False
    lock_present: bool = False
    elapsed_s: int = 0
    eta_s: int | None = None
    fraction: float | None = None
    #: Хвост ``<имя>.err`` — только когда расчёт упал.
    error_tail: str = ""

    @property
    def done(self) -> bool:
        return self.state in ("finished", "failed")

    def human_progress(self) -> str:
        """Строка для ленты инженера: русская проза, без имён файлов и путей."""
        if self.state == "finished":
            return f"Расчёт на кластере завершён, время расчёта {duration_ru(self.digest.elapsed_s or self.elapsed_s)}."
        if self.state == "failed":
            return "Расчёт на кластере завершился с ошибкой, подробности в журнале расчёта."
        parts = [f"Расчёт идёт, прошло {duration_ru(self.elapsed_s)}"]
        if self.digest.steps:
            parts.append(f"пройдено шагов: {self.digest.steps}")
        if self.digest.current_date:
            parts.append(f"текущая дата расчёта {self.digest.current_date}")
        if self.eta_s:
            parts.append(f"осталось примерно {duration_ru(self.eta_s)}")
        return ", ".join(parts) + "."


def estimate(handle: RunHandle, digest: LogDigest, *, elapsed_s: float) -> tuple[float | None, int | None]:
    """Доля пройденного и остаток в секундах по числу дат в файле schedule. Нет данных — ``None``."""
    total = int(handle.dates_total or 0)
    done = int(digest.steps or 0)
    if total <= 0 or done <= 0 or elapsed_s <= 0:
        return None, None
    fraction = min(1.0, done / total)
    if fraction >= 1.0:
        return 1.0, 0
    return round(fraction, 3), int(elapsed_s * (1 - fraction) / fraction)
