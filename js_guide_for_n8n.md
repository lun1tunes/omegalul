# Гайд по JavaScript для n8n Code Nodes (для Python-разработчика)

## 1. Базовые отличия от Python

| Python | JavaScript | Комментарий |
|---|---|---|
| `x = 5` | `const x = 5` / `let x = 5` | `const` — нельзя переприсвоить, `let` — можно |
| `None` | `null` / `undefined` | `null` — явно пусто, `undefined` — не определено |
| `True / False` | `true / false` | строчными |
| `dict` | `object` / `{}` | `{key: value}` |
| `list` | `array` / `[]` | `[1, 2, 3]` |
| `str` | `string` | `'текст'` или `"текст"` или `` `текст` `` |
| `f"привет {name}"` | `` `привет ${name}` `` | шаблонные строки через обратные кавычки |
| `and / or / not` | `&& / \|\| / !` | логические операторы |
| `len(x)` | `x.length` | свойство, не функция |
| `x in list` | `list.includes(x)` | метод массива |

```js
// Python: name = "мир"; print(f"привет {name}")
// JS:
const name = "мир";
console.log(`привет ${name}`);
```

---

## 2. Объекты (аналог dict)

```js
const obj = {
  name: "W-101",
  rate: 120,
  active: true
};

// Доступ к полям
obj.name;          // "W-101"
obj["rate"];       // 120

// Проверка существования
obj.name !== undefined;  // true
"rate" in obj;           // true

// Добавление/изменение
obj.bhp = 85;
obj.rate = 150;
```

**Аналог в Python:**
```python
obj = {"name": "W-101", "rate": 120, "active": True}
obj["name"]
obj["bhp"] = 85
```

---

## 3. Массивы (аналог list)

```js
const arr = [1, 2, 3, 4, 5];

arr.length;              // 5
arr[0];                  // 1
arr.includes(3);         // true
arr.indexOf(3);          // 2

// Добавление
arr.push(6);             // в конец
arr.unshift(0);          // в начало

// Удаление
arr.pop();               // с конца
arr.shift();             // с начала

// Срез
arr.slice(1, 3);         // [2, 3]
```

---

## 4. Spread оператор `...` — самое важное в n8n

Это то, что ты видел в конструкциях типа `{...$json, parameter: value}`.

### 4.1. Копирование объекта с изменением полей

```js
const original = { name: "W-101", rate: 120, bhp: 85 };

// Создать копию и изменить только rate
const updated = { ...original, rate: 150 };
// Результат: { name: "W-101", rate: 150, bhp: 85 }

// Создать копию и добавить новое поле
const extended = { ...original, status: "active" };
// Результат: { name: "W-101", rate: 120, bhp: 85, status: "active" }
```

**Аналог в Python:**
```python
original = {"name": "W-101", "rate": 120, "bhp": 85}
updated = {**original, "rate": 150}
extended = {**original, "status": "active"}
```

### 4.2. Копирование массива

```js
const arr = [1, 2, 3];
const copy = [...arr];           // [1, 2, 3]
const extended = [...arr, 4, 5]; // [1, 2, 3, 4, 5]
```

### 4.3. Типичный паттерн в n8n

```js
// Взять все поля из $json и добавить/заменить некоторые
return [{
  json: {
    ...$json,
    status: "completed",
    message: "Готово",
    extra_field: "новое значение"
  }
}];
```

---

## 5. Деструктуризация

Извлечение полей из объекта в переменные:

```js
const obj = { name: "W-101", rate: 120, bhp: 85 };

// Деструктуризация
const { name, rate } = obj;
// name = "W-101", rate = 120

// С переименованием
const { name: wellName, rate: oilRate } = obj;
// wellName = "W-101", oilRate = 120

// С дефолтным значением
const { name, rate, status = "unknown" } = obj;
// status = "unknown", потому что в obj нет поля status
```

**Аналог в Python:**
```python
name, rate = obj["name"], obj["rate"]
```

---

## 6. Стрелочные функции (аналог lambda)

```js
// Обычная функция
function add(a, b) {
  return a + b;
}

// Стрелочная функция (аналог lambda)
const add = (a, b) => a + b;

// С телом (если больше одной строки)
const add = (a, b) => {
  const result = a + b;
  return result;
};

// С одним аргументом (скобки можно опустить)
const double = x => x * 2;
```

**Типичное использование в n8n:**

```js
// map — аналог list comprehension
const items = [1, 2, 3, 4];
const doubled = items.map(x => x * 2);  // [2, 4, 6, 8]

// filter — фильтрация
const evens = items.filter(x => x % 2 === 0);  // [2, 4]

// find — найти первый элемент
const found = items.find(x => x > 2);  // 3

// some / every — проверки
items.some(x => x > 3);   // true (есть ли хоть один > 3)
items.every(x => x > 0);  // true (все ли > 0)
```

**Аналог в Python:**
```python
doubled = [x * 2 for x in items]
evens = [x for x in items if x % 2 == 0]
found = next((x for x in items if x > 2), None)
```

---

## 7. Пайп-вызовы (цепочки методов)

В n8n часто встречаются цепочки вызовов:

```js
// Пример из реального n8n кода
const result = items
  .filter(x => x.status === "active")
  .map(x => ({ ...x, processed: true }))
  .sort((a, b) => a.name.localeCompare(b.name));
```

Это эквивалент:
```js
// Без цепочки
let filtered = items.filter(x => x.status === "active");
let mapped = filtered.map(x => ({ ...x, processed: true }));
let result = mapped.sort((a, b) => a.name.localeCompare(b.name));
```

**Аналог в Python:**
```python
result = sorted(
    [{**x, "processed": True} for x in items if x["status"] == "active"],
    key=lambda x: x["name"]
)
```

### Частые цепочки в n8n:

```js
// Строка: обрезать, привести к нижнему регистру, убрать пробелы
const clean = String(value || '').trim().toLowerCase();

// Массив: отфильтровать, преобразовать, взять первое
const first = items
  .filter(x => x.kind === 'agent.handoff')
  .map(x => x.status_message)
  .find(Boolean);  // первый не-пустой

// Объект: преобразовать в массив пар, отфильтровать, собрать обратно
const filtered = Object.entries(obj)
  .filter(([key, value]) => value != null)
  .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {});
```

---

## 8. Нативные переменные n8n

Это самое важное — то, чего нет в обычном JavaScript.

### 8.1. `$json` — данные текущего элемента

```js
// Аналог: текущая строка данных
const name = $json.name;
const rate = $json.rate;

// Типичный паттерн: взять данные и вернуть изменённые
return [{
  json: {
    ...$json,
    processed: true
  }
}];
```

### 8.2. `$input` — входные данные ноды

```js
// Все входные элементы
const allItems = $input.all();       // массив всех элементов
const firstItem = $input.first();    // первый элемент
const lastItem = $input.last();      // последний элемент

// Данные первого элемента
const data = $input.first().json;
```

### 8.3. `$('Node Name')` — доступ к данным другой ноды

```js
// Получить данные из конкретной ноды
const prevData = $('Previous Node').first().json;
const allPrev = $('Previous Node').all();

// Безопасный доступ с проверкой
const result = (() => {
  try {
    return $('Some Node').first().json;
  } catch {
    return {};
  }
})();
```

**Это критически важно в n8n**, когда нужно обратиться к данным из другой ноды, не обязательно предыдущей.

### 8.4. `$execution` — информация о текущем execution

```js
const executionId = $execution.id;
const mode = $execution.mode;  // 'manual' или 'trigger'
```

### 8.5. `$env` — переменные окружения

```js
const apiKey = $env.MY_API_KEY;
```

### 8.6. `$now` — текущая дата

```js
const timestamp = $now.toISO();
```

---

## 9. Тернарный оператор (аналог `x if condition else y`)

```js
// Python: status = "ok" if code == 200 else "error"
// JS:
const status = code === 200 ? "ok" : "error";

// Вложенный
const level = score > 90 ? "A" : score > 80 ? "B" : "C";
```

---

## 10. Опциональная цепочка `?.` и нулевое слияние `??`

### 10.1. Опциональная цепочка `?.`

Безопасный доступ к вложенным полям без проверки на `null`/`undefined`:

```js
const obj = { a: { b: { c: 42 } } };

obj.a.b.c;        // 42
obj.a.b.d;        // undefined (не ошибка)
obj.a.x.c;        // ОШИБКА! Нельзя читать 'c' из undefined

// С опциональной цепочкой:
obj.a.x?.c;       // undefined (без ошибки)
obj?.a?.b?.c;     // 42
obj?.missing?.c;  // undefined
```

**Аналог в Python:**
```python
# Python 3.8+: getattr цепочки или ручные проверки
value = obj.get("a", {}).get("x", {}).get("c")
```

### 10.2. Нулевое слияние `??`

Возвращает правый операнд, только если левый — `null` или `undefined`:

```js
const a = null ?? "default";       // "default"
const b = undefined ?? "default";  // "default"
const c = 0 ?? "default";          // 0 (не "default"!)
const d = "" ?? "default";         // "" (не "default"!)
```

**Отличие от `||`:**
```js
0 || "default";   // "default" (0 — falsy)
0 ?? "default";   // 0 (0 — не null/undefined)
```

---

## 11. Шаблонные строки (аналог f-strings)

```js
const name = "W-101";
const rate = 120;

// Базовая
const msg = `Скважина ${name}, дебит ${rate}`;

// С выражениями
const info = `Итого: ${rate * 30} м3/мес`;

// Многострочная
const text = `
  Скважина: ${name}
  Дебит: ${rate}
  Статус: ${rate > 0 ? "активна" : "остановлена"}
`;
```

---

## 12. Работа с JSON

```js
// Сериализация (аналог json.dumps)
const str = JSON.stringify(obj);
const pretty = JSON.stringify(obj, null, 2);  // с отступами

// Парсинг (аналог json.loads)
const obj = JSON.parse(str);

// Безопасный парсинг
const safeParse = (str) => {
  try {
    return JSON.parse(str);
  } catch {
    return {};
  }
};
```

---

## 13. Типичные паттерны в n8n Code Nodes

### 13.1. Вернуть один элемент

```js
return [{ json: { ...$json, processed: true } }];
```

### 13.2. Вернуть несколько элементов

```js
return [
  { json: { id: 1, name: "A" } },
  { json: { id: 2, name: "B" } },
];
```

### 13.3. Безопасное получение данных из другой ноды

```js
const getData = (nodeName) => {
  try {
    return $(nodeName).first().json || {};
  } catch {
    return {};
  }
};

const prev = getData('Previous Node');
```

### 13.4. Преобразование массива объектов

```js
const items = $input.all().map(item => item.json);
const filtered = items.filter(x => x.status === "active");
return filtered.map(x => ({ json: { ...x, processed: true } }));
```

### 13.5. Условная логика

```js
const status = $json.status;

if (status === "completed") {
  return [{ json: { ...$json, result: "success" } }];
} else if (status === "needs_input") {
  return [{ json: { ...$json, result: "waiting" } }];
} else {
  return [{ json: { ...$json, result: "failed" } }];
}
```

### 13.6. IIFE (Immediately Invoked Function Expression)

Часто встречается для создания локальной области видимости:

```js
const result = (() => {
  try {
    return $('Some Node').first().json;
  } catch {
    return {};
  }
})();
```

Это функция, которая сразу вызывается. Аналог в Python:
```python
result = (lambda: get_data() if condition else {})()
```

### 13.7. Проверка типа

```js
typeof value === 'string';    // проверка на строку
typeof value === 'object';    // проверка на объект
Array.isArray(value);         // проверка на массив
value != null;                // не null и не undefined
```

---

## 14. Сравнение: `===` vs `==`

```js
5 === 5;       // true (строгое сравнение)
5 === "5";     // false (разные типы)
5 == "5";      // true (приведение типов) — НЕ используй!

null == undefined;   // true
null === undefined;  // false
```

**Правило:** всегда используй `===` и `!==`.

---

## 15. Методы массивов (шпаргалка)

```js
const arr = [1, 2, 3, 4, 5];

// Преобразование
arr.map(x => x * 2);           // [2, 4, 6, 8, 10]
arr.filter(x => x > 3);        // [4, 5]
arr.reduce((sum, x) => sum + x, 0);  // 15

// Поиск
arr.find(x => x > 3);          // 4
arr.findIndex(x => x > 3);     // 3
arr.includes(3);               // true

// Сортировка (мутирует массив!)
arr.sort((a, b) => a - b);     // по возрастанию
arr.sort((a, b) => b - a);     // по убыванию

// Проверки
arr.some(x => x > 3);          // true (хотя бы один)
arr.every(x => x > 0);         // true (все)

// Объединение
[...arr, 6, 7];                // [1, 2, 3, 4, 5, 6, 7]
arr.concat([6, 7]);            // [1, 2, 3, 4, 5, 6, 7]
```

---

## 16. Методы объектов (шпаргалка)

```js
const obj = { a: 1, b: 2, c: 3 };

Object.keys(obj);      // ['a', 'b', 'c']
Object.values(obj);    // [1, 2, 3]
Object.entries(obj);   // [['a', 1], ['b', 2], ['c', 3]]

// Проверка наличия поля
'a' in obj;            // true
obj.hasOwnProperty('a');  // true

// Слияние объектов
const merged = { ...obj, d: 4 };  // { a: 1, b: 2, c: 3, d: 4 }
Object.assign({}, obj, { d: 4 }); // то же самое
```

---

## 17. Пример разбора реального n8n кода

Вот кусок из твоего оркестратора:

```js
const prev = $('Prepare decision context').first().json || {};
const raw = $json;
const obj = v => v && typeof v === 'object' && !Array.isArray(v);
const parse = v => {
  if (obj(v)) return v;
  try {
    const p = JSON.parse(String(v || ''));
    return obj(p) ? p : {};
  } catch {
    return {};
  }
};
const out = parse(raw.output || raw.text || raw);
const decision = obj(out.action) ? out : (obj(raw) ? raw : {});
```

Разбор по строкам:

```js
// 1. Получить данные из ноды 'Prepare decision context', безопасно
const prev = $('Prepare decision context').first().json || {};

// 2. Текущие входные данные
const raw = $json;

// 3. Стрелочная функция-предикат: проверяет, что значение — объект (не массив)
const obj = v => v && typeof v === 'object' && !Array.isArray(v);

// 4. Функция безопасного парсинга
const parse = v => {
  if (obj(v)) return v;                    // если уже объект — вернуть как есть
  try {
    const p = JSON.parse(String(v || '')); // попробовать распарсить
    return obj(p) ? p : {};                // если получился объект — вернуть, иначе {}
  } catch {
    return {};                             // если ошибка парсинга — вернуть {}
  }
};

// 5. Распарсить выход из LLM (может быть строкой или объектом)
const out = parse(raw.output || raw.text || raw);

// 6. Если out.action — объект, использовать out; иначе попробовать raw
const decision = obj(out.action) ? out : (obj(raw) ? raw : {});
```

---

## 18. Частые ошибки Python-разработчиков

| Ошибка | Правильно |
|---|---|
| `if x:` (truthy check) | `if (x != null)` или `if (Boolean(x))` |
| `for x in arr:` | `for (const x of arr)` или `arr.forEach(x => ...)` |
| `x is None` | `x === null` или `x == null` |
| `dict.get(key, default)` | `obj[key] ?? default` |
| `list.append()` | `arr.push()` |
| `str.split()` | `str.split()` (то же) |
| `print()` | `console.log()` |
| `import json` | не нужен, `JSON` встроенный |
| Отступы | фигурные скобки `{}` |
| `def func():` | `function func() {}` или `const func = () => {}` |

---

## 19. Мини-шпаргалка для n8n Code Node

```js
// Получить данные текущего элемента
const data = $json;

// Получить данные из другой ноды
const prev = $('Node Name').first().json;

// Безопасный доступ
const value = $json?.nested?.field ?? 'default';

// Вернуть результат
return [{ json: { ...$json, new_field: "value" } }];

// Вернуть несколько элементов
return [1, 2, 3].map(x => ({ json: { id: x } }));

// Логирование (видно в execution)
console.log("Debug:", JSON.stringify($json));

// Проверка типа
typeof $json === 'object';
Array.isArray($json);
```

Этого достаточно, чтобы понимать 95% кода в n8n Code Nodes. Остальное — это комбинации этих базовых конструкций.