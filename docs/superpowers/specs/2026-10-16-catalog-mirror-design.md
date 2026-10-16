# Локальное зеркало каталога (SQLite) — дизайн

**Дата:** 2026-10-16
**Статус:** на ревью
**Проект:** «Маршрут» — веб-обёртка над API транспортного портала Екатеринбурга

## 1. Цель

Хранить справочные данные о **остановках, маршрутах и их связях** локально (в файле
SQLite), чтобы:

- не нагружать оригинальный портал повторными запросами каталога;
- мгновенно отвечать на поиск остановок и отдавать «остановка → маршруты»;
- разблокировать показ вида транспорта и чипов маршрутов в списке остановок (вариант A) и
  поиск «от маршрута» (вариант C) без роста числа запросов к порталу.

Живыми (онлайн, через портал) остаются только **изменяющиеся в реальном времени** данные:
прогноз прибытия на остановку и информация о борте.

## 2. Границы (YAGNI)

Входит:

- локальная БД со остановками, маршрутами, связями и полными деталями маршрутов;
- построение БД из портала и её периодическое/ручное обновление;
- отдача существующих справочных эндпоинтов из БД;
- новый эндпоинт обогащённого поиска остановок и эндпоинт обновления/статуса.

Не входит:

- кэширование прогнозов и бортов (остаются живыми, память + TTL как сейчас);
- история, аналитика, гео-индексы, карта;
- внешние зависимости (только стандартная библиотека, `sqlite3` встроен);
- фоновые демоны/таймеры вне процесса сервера (обновление — потоком внутри сервера).

## 3. Текущее состояние

- `portal.py` — `PortalClient` с тонкими методами `get_routes / get_route / get_stations /
  get_forecasts / get_station_routes / get_vehicle / get_vehicle_forecasts` и in-memory
  `CachedValue` (TTL 6 ч для каталога, 10 с для прогноза).
- `server.py` — `handle_api` отдаёт справочные данные напрямую из портала; `FavoritesStore`
  (JSON, атомарная запись) хранит избранное; `load_config` кладёт `config.local.json` поверх
  `config.json`.
- Кэш каталога — только в памяти: после рестарта всё тянется заново; обогащение списка
  остановок (вариант A) требует `getStationRoutes` по каждой остановке.

## 4. Источник данных и объёмы (проверено вживую)

| Команда | Что даёт | Объём | Время |
|---|---|---|---|
| `getStations` | список остановок | 1597 объектов | ~0.9 c |
| `getRoutes` | список маршрутов | 104 (68 авт., 22 трам., 14 тролл.) | ~0.7 c |
| `getRouteById({id})` | детали маршрута: subroutes → stations[] (id, name), points | 1 на маршрут | ~1 c |
| `getStationForecasts({sid})` | прогноз (живой) | — | — |
| `getVehicleInfo` / `getVehicleForecasts` | борт (живой) | — | — |

Связь «остановка → маршруты» строится **из деталей маршрутов** (104 запроса), а не из
`getStationRoutes` по каждой остановке (1597 запросов). Полная пересборка ≈ **106 запросов
≈ 2 минуты**, и только ~раз в сутки.

Объект остановки из портала заполнен лишь частично: непусты `id`, `name`, `lat`, `lng`,
`direction`; `adress`, `description`, `shortname`, `tags` — всегда пусты. Схема это учитывает.

## 5. Архитектура

Новый модуль `catalog.py` — класс `Catalog`, инкапсулирующий SQLite-файл. Сервер получает
экземпляр `Catalog` (или `None`, если зеркало отключено) и в `handle_api` предпочитает
локальные данные, с фолбэком на портал.

```
server.py  ──uses──▶  catalog.py (SQLite: stops, routes, stop_routes, route_detail, meta)
     │                        ▲
     │                        │ rebuild()  (фоновый поток, ~раз в сутки / вручную)
     └────────────▶ portal.py ─┘
```

- Чтения из БД — короткие, открывают собственное соединение на запрос (read-only).
- Пересборка: сначала **все сетевые запросы в память** (долго, без блокировок БД), затем
  **одна транзакция** записи (быстро) — читатели не блокируются надолго.
- `get_forecasts` и `get_vehicle*` не трогаем: остаются онлайн.

## 6. Модель данных (SQLite)

```sql
CREATE TABLE IF NOT EXISTS stops (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL DEFAULT '',
  lat         REAL,
  lng         REAL,
  direction   INTEGER,
  adress      TEXT DEFAULT '',
  description TEXT DEFAULT '',
  shortname   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_stops_name ON stops(name);

CREATE TABLE IF NOT EXISTS routes (
  id            INTEGER PRIMARY KEY,
  number        TEXT DEFAULT '',
  name          TEXT DEFAULT '',
  short_name    TEXT DEFAULT '',
  route_type_id INTEGER,
  company       TEXT DEFAULT '',
  from_station  TEXT DEFAULT '',
  to_station    TEXT DEFAULT '',
  price         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_routes_type ON routes(route_type_id);

CREATE TABLE IF NOT EXISTS stop_routes (
  stop_id  INTEGER NOT NULL,
  route_id INTEGER NOT NULL,
  PRIMARY KEY (stop_id, route_id)
);
CREATE INDEX IF NOT EXISTS idx_stop_routes_route ON stop_routes(route_id);

CREATE TABLE IF NOT EXISTS route_detail (
  route_id INTEGER PRIMARY KEY,
  payload  TEXT NOT NULL               -- исходный JSON getRouteById
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
-- meta: schema_version, last_refresh (unix ts)
```

Порядок остановок внутри направления хранится в `route_detail.payload` (subroutes[].stations[]),
поэтому «от маршрута» (вариант C) не требует отдельного хранилища.

## 7. Построение и обновление

`Catalog.rebuild(portal)`:

1. `stops = portal.get_stations()`, `routes = portal.get_routes()`.
2. Для каждого маршрута: `detail = portal.get_route(id)`; сложить в память; из
   `detail.subroutes[].stations[].id` накопить пары `(stop_id, route_id)`.
3. Одной транзакцией: очистить таблицы, вставить `stops`, `routes`, `stop_routes`,
   `route_detail`; записать `meta.last_refresh = now`, `schema_version`.
4. Ошибка любого шага — транзакция откатывается, старые данные остаются; ошибка логируется
   и сохраняется в `meta.last_error`.

Запуск обновления (`Catalog.refresh_async(portal)`): фоновый поток; одновременно не более
одного (`_building` флаг/лок); повторный вызов во время пересборки — no-op.

Триггеры:

- **на старте** — если БД пуста/нет, запустить пересборку в фоне (не блокируя запуск сервера);
- **периодически** — поток-сторож проверяет `now - last_refresh > refreshSeconds` и запускает
  пересборку (по умолчанию `refreshSeconds = 86400`);
- **вручную** — `POST /api/refresh` запускает пересборку и сразу отвечает.

## 8. Изменения HTTP API

Справочные эндпоинты читают из БД (фолбэк на портал, если БД недоступна/пуста или зеркало
отключено):

| Метод | Путь | Источник | Ответ |
|---|---|---|---|
| GET | `/api/routes?type=` | БД | `{routes:[...]}` (фильтр по `routeTypeId`) |
| GET | `/api/route?id=` | БД (`route_detail`) | `{route:{...}}` |
| GET | `/api/stations?q=` | БД | `{stations:[...]}` (тот же формат, что сейчас) |
| GET | `/api/stop-routes?sid=` | БД (`stop_routes`⋈`routes`) | `{routes:[...]}` |
| GET | **`/api/stops?q=`** *(новый)* | БД | `{stops:[{id,name,lat,lng,direction,routes:[{id,number,shortName,name,routeTypeId}]}]}` |
| POST | **`/api/refresh`** *(новый)* | — | `{status:"started"\|"building"\|"disabled", lastRefresh}` |
| GET | **`/api/catalog`** *(новый)* | БД | `{enabled, building, lastRefresh, counts:{stops,routes}}` |
| GET | `/api/forecast?sid=` | портал (живой) | без изменений |
| GET | `/api/vehicle?deviceCode=` | портал (живой) | без изменений |

`/api/stops` — целевой источник для варианта A (точки видов транспорта + чипы маршрутов в
одном ответе). `/api/stations` сохраняется для совместимости.

## 9. Конфиг, фолбэк, ошибки

Новый блок в `DEFAULTS`/`config.json`:

```json
"catalog": {
  "enabled": true,
  "path": "catalog.db",
  "refreshSeconds": 86400
}
```

- `path` относителен каталога `server.py` (рядом с `favorites.json`); в `.gitignore`.
- `enabled=false` → сервер работает как раньше (всё из портала), БД не создаётся.
- Любая ошибка БД (нет файла, повреждён, schema mismatch) → эндпоинт отдаёт данные из
  портала (текущее поведение) и не падает.
- Ошибки портала при чтении БД не возникают; при фолбэке `PortalError` → `502`, как сейчас.

## 10. Тесты (TDD)

- `tests/test_catalog.py` (новая):
  - построение из фейкового портала → в БД правильные числа остановок/маршрутов;
  - поиск остановок по подстроке (без регистра);
  - `stop_routes` для остановки; `routes_for_stop` возвращает нужные маршруты;
  - `route_detail` для маршрута; порядок остановок сохраняется;
  - повторная пересборка заменяет данные и обновляет `last_refresh`;
  - повреждённый/пустой файл → БД инициализируется пустой, чтения возвращают пусто.
- `tests/test_server.py` (дополнение):
  - `/api/stops?q=` отдаёт остановки с вложенными маршрутами;
  - `/api/stations`, `/api/routes`, `/api/stop-routes`, `/api/route` обслуживаются из БД,
    когда `Catalog` передан;
  - фолбэк на портал, когда `Catalog` пуст/`None`;
  - `POST /api/refresh` вызывает `refresh_async`; `GET /api/catalog` отдаёт статус.
- Сеть мокается (как в существующих тестах); `sqlite3` — реальный (файл в tempdir).

## 11. Развёртывание

- Файл `catalog.db` создаётся при первом старте; добавить в `.gitignore` и в
  `ReadWritePaths` systemd-юнита (каталог уже разрешён на запись: `/opt/transport`).
- Обновление кода: как обычно, `git pull --ff-only && systemctl restart marshrut`.
- Первый запуск после деплоя: сборка идёт в фоне (~2 мин), до готовности — фолбэк на портал.

## 12. Влияние на фронтенд (после реализации зеркала)

- Вариант A: список остановок берёт `/api/stops?q=` (точки + чипы) — мгновенно.
- Вариант C: вход «от маршрута» использует `/api/route?id=` (локально).
- Это отдельные правки фронта; спек по зеркалу их разблокирует, но не включает.

## 13. Вне области / на будущее

- Полностью офлайн-режим (кэш прогнозов на диск) — не нужно, прогноз живёт секундами.
- Инкрементальное обновление (только изменения) — при 104 маршрутах избыточно.
- Экспорт/импорт БД, версионирование схемы миграциями — пока не требуется (`schema_version`
  в `meta` достаточно: при несовпадении — пересборка с нуля).
