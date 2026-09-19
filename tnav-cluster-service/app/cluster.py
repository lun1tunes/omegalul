"""Работа с моделями на ГД-кластере: разобрать модель, сделать новую версию, запустить и наблюдать расчёт.

Один объект — ``ClusterWorkspace`` — собирает три части: оболочку кластера (``shell.py``), разбор ``.data``
файла (``model_files.py``) и запуск/наблюдение расчёта (``simulation.py``). Инструменты агента вызывают
только его методы, поэтому доменную логику можно читать и тестировать без n8n и без LLM.

    workspace = ClusterWorkspace(ClusterSettings.from_env())
    workspace.check_connection()                       # кластер отвечает, корень на месте
    models = workspace.find_models()                   # какие .data файлы есть под корнем
    layout = workspace.layout("MODELS/SEVER/SEVER.data")   # секции, INCLUDE, основной файл schedule
    version = workspace.create_version(layout, "prognoz_2027", payload, "FORECAST.INC")
    handle = workspace.launch(version.model_path, dates_total=version.dates_total)
    status = workspace.status(handle)                   # идёт / завершился / упал, остаток времени

Тяжёлые файлы модели (сетка, свойства — гигабайты) никогда не читаются целиком: агент читает только
``.data`` и хвосты логов, а строки ``DATES`` считает сам кластер через ``grep -c``.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from .model_files import (
    DATES_PATTERN,
    DeckOutline,
    IncludeRef,
    include_candidates,
    parse_deck,
    replace_include_path,
    version_suffix,
)
from .settings import ClusterSettings
from .shell import ClusterError, ClusterShell, open_shell
from .simulation import (
    EndReport,
    LogDigest,
    ResultsLayout,
    RunHandle,
    RunStatus,
    SimulatorProfile,
    estimate,
)

logger = logging.getLogger(__name__)

#: Сколько байт .data файла читаем (входной файл модели — десятки килобайт, ограничение от опечаток).
DATA_FILE_LIMIT = 4_000_000
#: Расширения файлов, которые могут быть файлом schedule.
SCHEDULE_SUFFIXES = (".inc", ".sch", ".grdecl", ".data", ".txt")


class ModelSummary(BaseModel):
    """Найденный на кластере входной файл модели."""

    model_config = ConfigDict(extra="forbid")

    path: str
    name: str
    directory: str
    bytes: int = 0


class ResolvedInclude(BaseModel):
    """INCLUDE из ``.data`` файла с путём, проверенным на кластере."""

    model_config = ConfigDict(extra="forbid")

    raw: str
    path: str = ""
    section: str = ""
    exists: bool = False
    keyword_line: int = 0
    path_line: int = 0
    bytes: int = 0

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path or self.raw).name


class ScheduleFile(BaseModel):
    """Кандидат в основной файл schedule: сколько в нём строк DATES и насколько он велик."""

    model_config = ConfigDict(extra="forbid")

    path: str
    filename: str
    dates: int = 0
    bytes: int = 0
    include_raw: str = ""
    keyword_line: int = 0
    path_line: int = 0


class ModelLayout(BaseModel):
    """Устройство одной модели: секции, подключённые файлы, основной файл schedule, каталог результатов."""

    model_config = ConfigDict(extra="forbid")

    data_path: str
    name: str
    directory: str
    sections: list[str] = Field(default_factory=list)
    includes: list[ResolvedInclude] = Field(default_factory=list)
    schedule_files: list[ScheduleFile] = Field(default_factory=list)
    main_schedule: ScheduleFile | None = None
    results: ResultsLayout
    missing_includes: list[str] = Field(default_factory=list)

    @property
    def dates_total(self) -> int:
        return int(self.main_schedule.dates) if self.main_schedule else 0

    def inspect_view(self) -> dict[str, Any]:
        """Короткая сводка для модели (LLM): без гигабайтных списков и без кавычек исходника."""
        return {
            "model": self.data_path,
            "sections": self.sections,
            "include_count": len(self.includes),
            "schedule_candidates": [
                {"file": item.path, "dates": item.dates, "bytes": item.bytes} for item in self.schedule_files[:8]
            ],
            "main_schedule": (
                {"file": self.main_schedule.path, "dates": self.main_schedule.dates} if self.main_schedule else None
            ),
            "missing_includes": self.missing_includes[:5],
            "results_dir": self.results.results_dir,
        }


class ModelVersion(BaseModel):
    """Новая версия модели: копия ``.data`` рядом с исходной и новый файл schedule возле старого."""

    model_config = ConfigDict(extra="forbid")

    version: str
    source_model: str
    model_path: str
    schedule_path: str
    replaced_include: str
    dates_total: int = 0
    schedule_bytes: int = 0


class ClusterWorkspace:
    """Фасад кластера. Оболочка открывается на каждый вызов: SSH-сессии не живут между шагами агента."""

    def __init__(self, settings: ClusterSettings, shell_factory: Callable[[ClusterSettings], ClusterShell] | None = None):
        self.settings = settings
        self._shell_factory = shell_factory or open_shell
        self.profile = SimulatorProfile(command=settings.cli_command)

    def shell(self) -> ClusterShell:
        """Новая оболочка (контекстный менеджер: закрывается сама)."""
        return self._shell_factory(self.settings)

    # -- подключение и поиск моделей ----------------------------------------------------------------

    def check_connection(self) -> dict[str, Any]:
        """Кластер отвечает и корень существует. Ошибка подключения — ``ClusterError``."""
        with self.shell() as shell:
            if not shell.is_dir("."):
                raise ClusterError(f"рабочий каталог кластера не найден: {self.settings.root}")
            host = shell.run(["hostname"])
            return {
                "root": shell.pwd() or self.settings.root,
                "host": host.stdout.strip() if host.ok else "",
                "transport": self.settings.transport,
            }

    def find_models(self, *, limit: int | None = None) -> list[ModelSummary]:
        """Входные файлы модели (``*.data``) под корнем — обход только по метаданным."""
        cap = int(limit or self.settings.max_models)
        with self.shell() as shell:
            paths = shell.find_files("*.data", depth=self.settings.scan_depth, limit=cap)
            out: list[ModelSummary] = []
            for path in paths:
                out.append(
                    ModelSummary(
                        path=path,
                        name=PurePosixPath(path).name,
                        directory=str(PurePosixPath(path).parent),
                        bytes=shell.file_size(path),
                    )
                )
            return sorted(out, key=lambda item: item.path)

    # -- устройство модели ---------------------------------------------------------------------------

    def layout(self, model_path: str) -> ModelLayout:
        """Разобрать ``.data`` файл: секции, INCLUDE и основной файл schedule (больше всего строк DATES)."""
        with self.shell() as shell:
            rel = shell.rel(shell.path(model_path))
            if not shell.is_file(rel):
                raise ClusterError(f"входной файл модели не найден: {rel}")
            text = shell.read_text(rel, limit=DATA_FILE_LIMIT)
            outline = parse_deck(text)
            data_dir = str(PurePosixPath(rel).parent)
            includes = [self._resolve(shell, ref, data_dir=data_dir) for ref in outline.includes]
            schedules = self._schedule_candidates(shell, outline, includes, data_dir=data_dir)
            main = schedules[0] if schedules else None
            return ModelLayout(
                data_path=rel,
                name=PurePosixPath(rel).stem,
                directory=data_dir,
                sections=outline.section_names,
                includes=includes,
                schedule_files=schedules,
                main_schedule=main,
                results=ResultsLayout.for_model(rel, results_dirname=self.settings.results_dirname),
                missing_includes=[item.raw for item in includes if not item.exists],
            )

    def _resolve(self, shell: ClusterShell, ref: IncludeRef, *, data_dir: str) -> ResolvedInclude:
        """Путь INCLUDE → путь на кластере: сначала от каталога ``.data`` (умолчание tNavigator), потом от корня."""
        for candidate in include_candidates(ref.path, data_dir=data_dir, including_dir=self.settings.root):
            try:
                rel = shell.rel(shell.path(candidate))
            except ClusterError:
                continue
            if shell.is_file(rel):
                return ResolvedInclude(
                    raw=ref.path,
                    path=rel,
                    section=ref.section,
                    exists=True,
                    keyword_line=ref.keyword_line,
                    path_line=ref.path_line,
                    bytes=shell.file_size(rel),
                )
        return ResolvedInclude(raw=ref.path, section=ref.section, keyword_line=ref.keyword_line, path_line=ref.path_line)

    def _schedule_candidates(
        self,
        shell: ClusterShell,
        outline: DeckOutline,
        includes: list[ResolvedInclude],
        *,
        data_dir: str,
    ) -> list[ScheduleFile]:
        """Файлы секции SCHEDULE, отсортированные по числу строк DATES (первый — основной файл schedule)."""
        candidates: list[ScheduleFile] = []
        for item in includes[: self.settings.max_schedule_candidates * 2]:
            if item.section != "SCHEDULE" or not item.exists:
                continue
            if not item.path.lower().endswith(SCHEDULE_SUFFIXES):
                continue
            dates = shell.count_lines(item.path, DATES_PATTERN)
            candidates.append(
                ScheduleFile(
                    path=item.path,
                    filename=item.filename,
                    dates=dates,
                    bytes=item.bytes,
                    include_raw=item.raw,
                    keyword_line=item.keyword_line,
                    path_line=item.path_line,
                )
            )
            if len(candidates) >= self.settings.max_schedule_candidates:
                break
        candidates.sort(key=lambda item: (-item.dates, -item.bytes, item.path))
        return [item for item in candidates if item.dates > 0] or candidates

    # -- новая версия модели --------------------------------------------------------------------------

    def create_version(
        self,
        layout: ModelLayout,
        version: str,
        schedule_payload: bytes,
        *,
        schedule_filename: str = "",
    ) -> ModelVersion:
        """Копия ``.data`` файла + новый файл schedule рядом со старым + переписанный путь в INCLUDE.

        Ничего не удаляется и не перезаписывается: если такие файлы уже есть, метод отказывается работать.
        """
        if layout.main_schedule is None:
            raise ClusterError("в модели не найден файл schedule, подключённый в секции SCHEDULE")
        if not schedule_payload:
            raise ClusterError("новый файл schedule пуст")
        suffix = version_suffix(version)
        schedule_old = PurePosixPath(layout.main_schedule.path)
        new_schedule_name = (schedule_filename or "").strip() or f"{schedule_old.stem}_{suffix}{schedule_old.suffix or '.INC'}"
        new_schedule_rel = str(schedule_old.parent / PurePosixPath(new_schedule_name).name)
        data_old = PurePosixPath(layout.data_path)
        new_model_rel = str(data_old.parent / f"{data_old.stem}_{suffix}{data_old.suffix or '.data'}")

        with self.shell() as shell:
            for target in (new_model_rel, new_schedule_rel):
                if shell.exists(target):
                    raise ClusterError(f"файл уже существует, выберите другое имя версии: {target}")
            shell.write_bytes(new_schedule_rel, schedule_payload)
            # Копия основной модели: сохраняем атрибуты, затем меняем в копии только путь INCLUDE.
            shell.copy(layout.data_path, new_model_rel)
            text = shell.read_text(new_model_rel, limit=DATA_FILE_LIMIT)
            outline = parse_deck(text)
            ref = self._include_for(outline, layout.main_schedule)
            new_raw = str(PurePosixPath(ref.path).parent / PurePosixPath(new_schedule_name).name)
            shell.write_bytes(new_model_rel, replace_include_path(text, ref, new_raw).encode("utf-8"))
            dates = shell.count_lines(new_schedule_rel, DATES_PATTERN)
            checked = parse_deck(shell.read_text(new_model_rel, limit=DATA_FILE_LIMIT))
            if not any(PurePosixPath(item.path).name == PurePosixPath(new_schedule_name).name for item in checked.includes):
                raise ClusterError("в копии модели не удалось переписать путь к файлу schedule")
            return ModelVersion(
                version=suffix,
                source_model=layout.data_path,
                model_path=new_model_rel,
                schedule_path=new_schedule_rel,
                replaced_include=str(new_raw),
                dates_total=dates,
                schedule_bytes=len(schedule_payload),
            )

    @staticmethod
    def _include_for(outline: DeckOutline, schedule: ScheduleFile) -> IncludeRef:
        for ref in outline.includes:
            if ref.path == schedule.include_raw or ref.keyword_line == schedule.keyword_line:
                return ref
        raise ClusterError("в файле модели не найдено подключение файла schedule")

    # -- расчёт ----------------------------------------------------------------------------------------

    def launch(self, model_path: str, *, dates_total: int = 0) -> RunHandle:
        """Запустить расчёт командой из настроек. Возвращает дескриптор для наблюдения."""
        with self.shell() as shell:
            rel = shell.rel(shell.path(model_path))
            if not shell.is_file(rel):
                raise ClusterError(f"входной файл модели не найден: {rel}")
            results = ResultsLayout.for_model(rel, results_dirname=self.settings.results_dirname)
            if shell.exists(results.lock):
                raise ClusterError("модель уже считается другим запуском (в каталоге результатов есть файл блокировки)")
            shell.makedirs(results.results_dir)
            script = self.profile.launch_script(
                shell.path(rel),
                console_path=shell.path(results.console),
                workdir=str(PurePosixPath(shell.path(rel)).parent),
            )
            result = shell.run_script(script).check()
            pid = _first_int(result.stdout)
            if pid <= 0:
                raise ClusterError("кластер не сообщил идентификатор процесса расчёта")
            return RunHandle(
                model_path=rel,
                model_name=PurePosixPath(rel).stem,
                results=results,
                pid=pid,
                dates_total=int(dates_total or 0),
                command=self.profile.rendered(shell.path(rel)),
            )

    def status(self, handle: RunHandle) -> RunStatus:
        """Состояние расчёта: процесс, файл блокировки, лог, сводка ``.end`` и хвост ошибок."""
        with self.shell() as shell:
            alive = shell.process_alive(handle.pid)
            lock = shell.exists(handle.results.lock)
            log_text = shell.read_text(handle.results.log, limit=self.settings.log_tail_bytes, tail=True) if shell.is_file(handle.results.log) else ""
            digest = LogDigest.parse(log_text)
            end_report = (
                EndReport.parse(shell.read_text(handle.results.end, limit=4000)) if shell.is_file(handle.results.end) else None
            )
            error_tail = ""
            if shell.is_file(handle.results.err) and shell.file_size(handle.results.err) > 0:
                error_tail = shell.read_text(handle.results.err, limit=4000, tail=True).strip()
            # nohup stdout is not tNavigator .err — only use it when the process is gone and there is no lock.
            if not error_tail and not alive and not lock and not digest.finished and shell.is_file(handle.results.console):
                error_tail = shell.read_text(handle.results.console, limit=2000, tail=True).strip()
            elapsed = int(handle.elapsed_s)
            fraction, eta = estimate(handle, digest, elapsed_s=elapsed)
            state = _state_of(alive=alive, lock=lock, digest=digest, end_report=end_report, error_tail=error_tail)
            return RunStatus(
                state=state,
                digest=digest,
                end_report=end_report,
                process_alive=alive,
                lock_present=lock,
                elapsed_s=elapsed,
                eta_s=eta if state == "running" else (0 if state == "finished" else None),
                fraction=1.0 if state == "finished" else fraction,
                error_tail=error_tail[-1200:] if state == "failed" else "",
            )


def _state_of(*, alive: bool, lock: bool, digest: LogDigest, end_report: EndReport | None, error_tail: str) -> str:
    """Правила чтения состояния (мануал 9.1.1: ``.lock`` живёт во время расчёта, ``.err`` — про ошибки)."""
    hard_error = bool(digest.errors) or (end_report is not None and end_report.errors > 0)
    if digest.finished and not hard_error:
        return "finished"
    if alive or (lock and not hard_error):
        return "running"
    if hard_error or error_tail:
        return "failed"
    if digest.steps or digest.current_date:
        return "failed"  # считал и пропал, итоговой строки нет
    return "starting"


def _first_int(text: str) -> int:
    for token in str(text or "").split():
        if token.strip().isdigit():
            return int(token.strip())
    return 0
