# FastAPI Math Service (MVP)

Минимальный HTTP-сервис геометрических расчётов MAS. Endpoint
`trajectory-intersection` пакетно читает стандартные станции DEV в порядке
`MD X Y Z`, преобразует ASCII CPS3/ZMAP grid в массивы NumPy, билинейно
интерполирует поверхность и возвращает первое пересечение по MD для каждой траектории.
Поверхность разбирается один раз на весь batch.

## Запуск в Windows CMD

Нужен Python 3.11+:

```bat
cd fastapi-math-service
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Проверка: `http://127.0.0.1:8100/health`.

## Endpoint

`POST /api/v1/math/trajectory-intersection` принимает multipart-поля:

- `trajectory_files` — одно или несколько повторяющихся multipart-полей
  с текстовыми `.dev`; в каждом минимум две станции `MD X Y Z`;
- `surface_file` — ASCII CPS3 с обычными `FSNROW <rows> <columns>`,
  `FSLIMI` и `->GRID` (вариант с отдельным `FSNCOL` тоже поддержан), либо
  совместимый ZMAP `@GRID`.

Ответ:

```json
{
  "results": [
    {
      "filename": "W-1.dev",
      "intersection_md": 2540.5,
      "x": 100.0,
      "y": 200.0,
      "z": -2450.0
    }
  ]
}
```

Возвращается первое пересечение по MD для каждого DEV. `filename` сохраняет
исходное имя файла. Batch работает fail-fast: `404` с именем DEV означает отсутствие
его пересечения в валидной области grid, `422` — ошибку входного формата. Траектории и
поверхность должны использовать одинаковые CRS, единицы длины, вертикальный
datum и знак Z; сервис намеренно не выполняет преобразование координат.

Auth, credentials, sandbox и tNavigator-код отсутствуют по правилам локального
MVP. Сервис выполняет только математику и возвращает JSON.
