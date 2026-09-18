"""Устройство модели tNavigator: секции ``.data`` файла и подключённые через INCLUDE файлы.

Чистый разбор текста, без кластера и без сети — поэтому его легко читать и тестировать.

    outline = parse_deck(text)
    outline.section_of(line)                  # в какой секции лежит строка
    outline.includes_in("SCHEDULE")           # какие файлы подключены в секции SCHEDULE
    new_text = replace_include_path(text, ref, "'../SCHEDULE/FORECAST/new.INC'")

Что важно знать про формат (мануал tNavigator, разделы 9 «Файлы данных модели» и 12.1.81 INCLUDE):

* ``<имя>.data`` — входной файл модели, остальное подключается ключевым словом ``INCLUDE``;
* секции идут в фиксированном порядке: RUNSPEC, GRID, EDIT, PROPS, REGIONS, SOLUTION, SUMMARY, SCHEDULE;
* комментарий — от ``--`` до конца строки; запись ключевого слова закрывается одиночным ``/``;
* путь в ``INCLUDE`` может стоять на той же строке или ниже, в кавычках или без них;
* относительные пути по умолчанию (опция ``-p``/``--ecl-include-path`` = ``on``) считаются от каталога
  входного ``.data`` файла; при ``off`` — от каталога включающего файла. Поэтому агент проверяет оба
  варианта и берёт тот, который существует на кластере.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

#: Секции модели по порядку (мануал, раздел 11.1 «Язык ключевых слов»).
SECTION_KEYWORDS: tuple[str, ...] = ("RUNSPEC", "GRID", "EDIT", "PROPS", "REGIONS", "SOLUTION", "SUMMARY", "SCHEDULE")
#: Строка с датами расчёта — по числу этих строк опознаётся основной файл schedule.
DATES_PATTERN = r"^[[:space:]]*DATES([[:space:]]|$)"
_DATES_RE = re.compile(r"^\s*DATES(\s|$)", re.MULTILINE)
_KEYWORD_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")
_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_BARE_RE = re.compile(r"[^\s/]+")


def strip_comment(line: str) -> str:
    """Убрать комментарий ``--`` (в кавычках ``--`` остаётся частью пути)."""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            i += 1
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "-" and line[i + 1 : i + 2] == "-":
            break
        out.append(char)
        i += 1
    return "".join(out)


class IncludeRef(BaseModel):
    """Одно ключевое слово INCLUDE в ``.data`` файле."""

    model_config = ConfigDict(extra="forbid")

    #: Номер строки с ключевым словом INCLUDE (с 1).
    keyword_line: int
    #: Номер строки, в которой стоит сам путь (обычно та же или следующая).
    path_line: int
    #: Путь так, как он написан в файле, вместе с кавычками.
    raw: str
    #: Путь без кавычек.
    path: str
    #: Секция, в которой стоит это INCLUDE.
    section: str = ""

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path).name


class SectionSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start_line: int
    end_line: int


class DeckOutline(BaseModel):
    """Карта ``.data`` файла: секции и подключённые файлы."""

    model_config = ConfigDict(extra="forbid")

    sections: list[SectionSpan] = Field(default_factory=list)
    includes: list[IncludeRef] = Field(default_factory=list)
    line_count: int = 0

    @property
    def section_names(self) -> list[str]:
        return [span.name for span in self.sections]

    def section_of(self, line: int) -> str:
        for span in self.sections:
            if span.start_line <= line <= span.end_line:
                return span.name
        return ""

    def includes_in(self, section: str) -> list[IncludeRef]:
        return [ref for ref in self.includes if ref.section == section]


def parse_deck(text: str) -> DeckOutline:
    """Разобрать текст ``.data`` файла: где какая секция и какие файлы подключены."""
    lines = str(text or "").splitlines()
    sections: list[SectionSpan] = []
    includes: list[IncludeRef] = []
    pending_include_line = 0  # ждём путь после ключевого слова INCLUDE
    for index, original in enumerate(lines, start=1):
        line = strip_comment(original).strip()
        if not line:
            continue
        first = line.split()[0].upper().rstrip("/")
        if pending_include_line:
            ref = _take_path(line, keyword_line=pending_include_line, path_line=index)
            if ref is not None:
                includes.append(ref)
                pending_include_line = 0
                continue
            if _KEYWORD_RE.match(first) and first != "INCLUDE":
                pending_include_line = 0  # INCLUDE без пути — битый файл, не гадаем
        if first in SECTION_KEYWORDS and _KEYWORD_RE.match(first):
            if sections:
                sections[-1] = sections[-1].model_copy(update={"end_line": index - 1})
            sections.append(SectionSpan(name=first, start_line=index, end_line=len(lines)))
            continue
        if first == "INCLUDE":
            rest = line[len("INCLUDE") :].strip()
            ref = _take_path(rest, keyword_line=index, path_line=index) if rest else None
            if ref is not None:
                includes.append(ref)
            else:
                pending_include_line = index
    if sections:
        sections[-1] = sections[-1].model_copy(update={"end_line": len(lines)})
    outline = DeckOutline(sections=sections, includes=includes, line_count=len(lines))
    return outline.model_copy(
        update={"includes": [ref.model_copy(update={"section": outline.section_of(ref.keyword_line)}) for ref in outline.includes]}
    )


def _take_path(fragment: str, *, keyword_line: int, path_line: int) -> IncludeRef | None:
    quoted = _QUOTED_RE.search(fragment)
    if quoted:
        raw = quoted.group(0)
        path = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        return IncludeRef(keyword_line=keyword_line, path_line=path_line, raw=raw, path=str(path).strip())
    bare = _BARE_RE.search(fragment)
    if bare and not _KEYWORD_RE.match(bare.group(0)):
        return IncludeRef(keyword_line=keyword_line, path_line=path_line, raw=bare.group(0), path=bare.group(0))
    return None


def replace_include_path(text: str, ref: IncludeRef, new_path: str) -> str:
    """Заменить путь одного INCLUDE, не трогая остальной файл (кавычки сохраняются)."""
    lines = str(text or "").splitlines(keepends=True)
    index = ref.path_line - 1
    if index < 0 or index >= len(lines):
        raise ValueError("строка с путём INCLUDE не найдена в файле модели")
    quote = ref.raw[0] if ref.raw[:1] in ("'", '"') else ""
    replacement = f"{quote}{new_path}{quote}" if quote else new_path
    if ref.raw not in lines[index]:
        raise ValueError("путь INCLUDE изменился с момента разбора файла модели")
    lines[index] = lines[index].replace(ref.raw, replacement, 1)
    return "".join(lines)


def include_candidates(raw_path: str, *, data_dir: str, including_dir: str = "") -> list[str]:
    """Возможные расположения включённого файла: от каталога ``.data`` (по умолчанию) и от включающего файла."""
    path = PurePosixPath(str(raw_path or "").strip())
    if path.is_absolute():
        return [str(path)]
    bases = [data_dir] + ([including_dir] if including_dir and including_dir != data_dir else [])
    out: list[str] = []
    for base in bases:
        joined = _normalise(PurePosixPath(base) / path)
        if joined not in out:
            out.append(joined)
    return out


def count_dates(text: str) -> int:
    """Сколько строк DATES в тексте (для локальных проверок; на кластере считает ``grep -c``)."""
    return len(_DATES_RE.findall(str(text or "")))


def version_suffix(name: str) -> str:
    """Имя версии инженера → безопасный хвост имени файла (латиница, цифры, подчёркивание)."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "").strip()).strip("_")
    return cleaned[:40] or "v2"


def _normalise(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts and parts[-1] != "/":
                parts.pop()
            continue
        if part == ".":
            continue
        parts.append(part)
    return str(PurePosixPath(*parts)) if parts else "/"
