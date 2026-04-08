# Цифровой поводырь

Локальная система микрорайона для кейса НТО «Умный город».

Проект решает задачу сопровождения незрячего пользователя по маршрутам микрорайона и МФЦ, работает с локальными навигационными точками, читает состояние городского сервера, отправляет события в город, считает ETA автобусов, отслеживает пробки и препятствия, формирует погодные рекомендации и управляет виброплатформой на переходе.

## 1. Что входит в проект

- Локальный сервер управления на `FastAPI`
- Веб-интерфейс для демонстрации и управления сценариями
- Конфиги устройств, маршрутов, сигналов и услуг МФЦ
- Каркасы прошивок `ESP32` для:
  - звуковой точки `type1`
  - световой точки `type2`
  - виброплатформы
- Модули логики:
  - poller состояния города
  - отправка событий в городской сервер
  - route engine
  - scenario engine
  - ETA engine
  - congestion engine
  - obstacle engine
  - recommendation engine
  - voice engine
  - device gateway
  - simulation mode
- Unit и integration tests

## 2. Структура репозитория

```text
НТО/
├─ configs/
│  ├─ team_config.yaml
│  ├─ route_graph.json
│  ├─ devices.json
│  ├─ signals.json
│  └─ mfc_services.json
├─ firmware/
│  ├─ nav_point_type1/
│  ├─ nav_point_type2/
│  └─ vibro_platform/
├─ src/digital_guide/
│  ├─ app.py
│  ├─ main.py
│  ├─ api/
│  ├─ core/
│  ├─ services/
│  └─ web/
└─ tests/
```

## 3. Требования

- Windows PowerShell
- Python `3.11+`
- Доступ к сети для запросов к городскому серверу
- Опционально: Arduino IDE или PlatformIO для прошивок `ESP32`

## 4. Быстрый запуск

Из корня проекта:

```powershell
cd C:\Users\79104\Desktop\НТО
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
$env:PYTHONPATH="src"
.\.venv\Scripts\python -m uvicorn digital_guide.main:app --reload --host 127.0.0.1 --port 8080
```

После запуска открой:

- `http://127.0.0.1:8080/dashboard`
- `http://127.0.0.1:8080/devices`
- `http://127.0.0.1:8080/route-builder`
- `http://127.0.0.1:8080/transport`
- `http://127.0.0.1:8080/mfc`
- `http://127.0.0.1:8080/logs`

## 5. Проверка после запуска

Проверка health:

```powershell
curl.exe http://127.0.0.1:8080/health
```

Ожидаемый ответ:

```json
{"status":"ok","team_id":"team-01","team_name":"Malevin's Kids","server_name":"Malevin's Kids"}
```

Проверка тестов:

```powershell
cd C:\Users\79104\Desktop\НТО
$env:PYTHONPATH="src"
.\.venv\Scripts\python -m pytest -q
```

## 6. Основные страницы интерфейса

### `dashboard`

Показывает:

- текущий маршрут
- рекомендацию по погоде
- таблицу автобусов и ETA
- последние события
- снимок состояния системы

### `devices`

Показывает:

- heartbeat устройств
- симуляцию face-подтверждения
- симуляцию события виброплатформы

### `route-builder`

Позволяет:

- выбрать `user_id`
- указать стартовую и целевую точку
- задать промежуточные точки
- выбрать сценарий
- построить JSON-маршрут
- запустить голосовой ввод маршрута
- распознать речь в текст
- отправить текст в `DeepSeek-R1`
- автозаполнить форму
- автоматически построить маршрут тем же backend-механизмом

### `transport`

Позволяет:

- запустить транспортный сценарий
- симулировать препятствие
- видеть таблицу автобусов
- смотреть результат сценария и симуляции

### `mfc`

Позволяет:

- выбрать услугу МФЦ
- запустить indoor-сценарий
- увидеть JSON результата

### `logs`

Показывает:

- читаемую временную шкалу событий
- полный machine snapshot состояния

## 7. Конфигурация проекта

### `configs/team_config.yaml`

Основной файл команды:

- `team_id`
- `team_name`
- `primary_ring`
- `user_id`
- `selected_confirmation_mode`
- `face_threshold`
- `city.*`
- `congestion.*`
- `recommendation.*`
- `simulation.*`
- `ring_stop_order`
- `team_point_mapping`

### `configs/route_graph.json`

Граф маршрутов:

- `nodes`
- `edges`
- `stop_mappings`
- `indoor_mappings`

### `configs/devices.json`

Список устройств:

- `device_id`
- `logical_role`
- `device_kind`
- `location_id`
- `ip_address`
- `active`

### `configs/signals.json`

Профили сигналов:

- частоты и длительности для звуковых точек
- цвета и длительности для световых точек

### `configs/mfc_services.json`

Связь услуги с окном:

- `service_id`
- `service_name`
- `window_node`
- `queue_prefix`

### `deepseek` в `team_config.yaml`

Для голосового построения маршрута используется отдельный блок:

```yaml
deepseek:
  enabled: false
  base_url: "https://api.deepseek.com"
  api_key: ""
  model: "deepseek-reasoner"
  timeout_seconds: 30.0
```

Чтобы включить голосовой маршрут через `DeepSeek-R1`:

1. Установите `deepseek.enabled: true`
2. Укажите реальный `deepseek.api_key`
3. Перезапустите сервер

### Важный флаг для локальной разработки

В примере проекта городская интеграция по умолчанию выключена:

```yaml
city:
  enabled: false
```

Это сделано специально, чтобы локальный запуск не засыпал логами, если городской сервер недоступен.

Если у вас есть реальный стенд организаторов, включите интеграцию:

```yaml
city:
  enabled: true
  allow_outbound_events: true
  base_url: "http://<CITY_HOST>:<PORT>"
  state_url: "http://<CITY_HOST>:<PORT>/debug/state"
  access_token: "<TOKEN_FROM_ORGANIZERS>"
```

### Режим подключения только на чтение

Если нужно подключить локальный сервер к городскому, но не отправлять в него никакие сигналы и события, используйте:

```yaml
city:
  enabled: true
  allow_outbound_events: false
  base_url: "http://192.168.31.63:8000"
  state_url: "http://192.168.31.63:8000/debug/state"
  event_path: "/event"
  access_token: ""
  bus_ring_mapping:
    "1": left_ring
    "2": right_ring
```

`bus_ring_mapping` задаёт явное соответствие `bus_id -> ring_id` для городского `debug/state`. Если городской сервер отдаёт автобус `2` и номер позиции остановки внутри кольца, сервер сначала возьмёт кольцо из этого маппинга, а уже потом переведёт позицию в вашу внутреннюю остановку графа.

В этом режиме:

- `debug/state` читается
- городские автобусы, ETA и погодные данные могут подтягиваться
- ни один `POST /event` в городской сервер не уходит
- события `TYPE 1..6` локально обрабатываются, но наружу не отправляются

## 8. Архитектура системы

### Поток данных

```text
Городской сервер --GET debug/state--> Local Control Server
Local Control Server --POST /event--> Городской сервер
ESP32 devices <--poll commands / send events--> Local Control Server
Web UI <--> FastAPI
```

### Ключевые backend-модули

- `city_state_poller` читает состояние города
- `city_event_sender` отправляет события `TYPE 1..6`
- `route_engine` строит маршруты
- `scenario_engine` управляет сценариями
- `eta_engine` считает ETA автобусов
- `congestion_engine` определяет пробки
- `obstacle_engine` хранит препятствия и триггерит reroute
- `recommendation_engine` формирует рекомендации по погоде
- `voice_engine` готовит и отправляет голосовые сообщения
- `device_gateway` принимает события устройств и раздаёт команды
- `session_manager` хранит состояние сценариев
- `simulation` позволяет защищать проект без реального железа

## 9. Поддерживаемые типы событий

По кейсу поддерживаются:

- `TYPE 1` — озвучка текста
- `TYPE 2` — включение звуковой точки типа 1
- `TYPE 3` — включение световой точки типа 2
- `TYPE 4` — RFID-событие
- `TYPE 5` — препятствие
- `TYPE 6` — подтверждение прохождения по распознаванию лица

Все исходящие сообщения имеют обязательное поле `type`.

## 10. Сценарии системы

### Пешая навигация

1. Пользователь выбирает цель
2. Строится маршрут
3. Активируется текущая звуковая точка
4. После подтверждения включается следующая
5. После финальной точки сценарий завершается

### Транспорт

1. Идём до остановки
2. Ждём нужный автобус
3. Даём cue на посадку
4. Считаем ETA и отслеживаем остановки
5. Даём cue на выход и пересадку
6. Завершаем маршрут в целевом кольце

### МФЦ

1. Выбирается услуга
2. Определяется окно обслуживания
3. Строится indoor-маршрут
4. Последовательно активируются световые точки
5. После подтверждения точек сценарий завершается

### Погода и пробки

- по температуре определяется режим `cold/normal/hot`
- по росту ETA или времени круга определяется пробка
- пользователю выдаётся рекомендация по одежде и предупреждение

### Виброплатформа

1. Датчик расстояния фиксирует приближение
2. Событие приходит на локальный сервер
3. Локальный сервер включает вибрацию
4. После ухода объекта вибрация выключается

## 11. Confirmation mode: FACE и RFID

Режим задаётся в `team_config.yaml`:

```yaml
selected_confirmation_mode: face
face_threshold: 0.85
```

### FACE

Точка считается подтверждённой, если:

- `confidence >= face_threshold`
- `user_id` совпадает с ожидаемым пользователем

### RFID

Точка считается подтверждённой после валидного UID.

В архитектуре уже предусмотрен debounce и anti-spam повторных событий.

## 12. Simulation mode

Simulation mode нужен для демонстрации без полного железа.

Управляется в `team_config.yaml`:

```yaml
simulation:
  enabled: true
  auto_bus_updates: false
  auto_point_confirmation: false
  loop_interval_seconds: 1.0
```

Доступные симуляции:

- face-подтверждение точки
- obstacle event
- событие датчика расстояния
- mock bus updates

## 13. API

Основные endpoints:

- `GET /api/state`
- `GET /api/routes/current`
- `POST /api/routes/build`
- `POST /api/scenario/start`
- `POST /api/scenario/stop`
- `POST /api/device/event`
- `POST /api/device/heartbeat`
- `GET /api/device/commands/{device_id}`
- `POST /api/obstacle`
- `GET /api/eta`
- `GET /api/recommendations`
- `GET /api/logs`
- `POST /api/voice/route-parse`
- `GET /health`

### Пример голосового парсинга маршрута

```powershell
curl.exe -X POST http://127.0.0.1:8080/api/voice/route-parse ^
  -H "Content-Type: application/json" ^
  -d "{\"transcript\":\"Построй маршрут для user_demo от точки 1 до точки 16 через 5, 8 и 11, автобусом\",\"current_user_id\":\"user_demo\"}"
```

### Пример построения маршрута

```powershell
curl.exe -X POST http://127.0.0.1:8080/api/routes/build ^
  -H "Content-Type: application/json" ^
  -d "{\"user_id\":\"user_demo\",\"start_node\":1,\"goal_node\":16,\"via_nodes\":[],\"scenario_kind\":\"walk\"}"
```

### Пример старта сценария

```powershell
curl.exe -X POST http://127.0.0.1:8080/api/scenario/start ^
  -H "Content-Type: application/json" ^
  -d "{\"scenario_kind\":\"transport\",\"user_id\":\"user_demo\",\"start_node\":1,\"goal_node\":29,\"via_nodes\":[16,17,18],\"bus_id\":\"left_ring-bus-1\"}"
```

## 14. Логи и сохранение состояния

Система ведёт:

- читаемые console logs
- machine JSON logs
- persisted snapshot в `data/`

Логируются:

- запуск сценария
- построение маршрута
- активация точек
- RFID/face подтверждения
- bus updates
- перерасчёт ETA
- пробки
- препятствия
- reroute
- включение и выключение вибрации
- погодные рекомендации
- ошибки

## 15. Прошивки ESP32

### Звуковая точка

Файлы:

- `firmware/nav_point_type1/config.h`
- `firmware/nav_point_type1/nav_point_type1.ino`

Функции:

- подключение к Wi-Fi
- heartbeat
- polling команд
- buzzer
- RFID event

### Световая точка

Файлы:

- `firmware/nav_point_type2/config.h`
- `firmware/nav_point_type2/nav_point_type2.ino`

Функции:

- управление `WS2812B`
- heartbeat
- polling команд
- RFID event

### Виброплатформа

Файлы:

- `firmware/vibro_platform/config.h`
- `firmware/vibro_platform/vibro_platform.ino`

Функции:

- чтение датчика расстояния
- отправка метрик расстояния
- локальная фильтрация ложных срабатываний
- включение и выключение вибромотора

### Как подготовить прошивки

1. Открой нужный `.ino` в Arduino IDE
2. Отредактируй `config.h`
3. Укажи:
   - `WIFI_SSID`
   - `WIFI_PASS`
   - `LOCAL_SERVER_BASE`
   - `DEVICE_ID`
4. Выбери плату `ESP32`
5. Собери и прошей

## 16. Запуск с реальным городским сервером

В `configs/team_config.yaml` укажи реальные значения:

```yaml
city:
  base_url: "http://<CITY_HOST>:<PORT>"
  state_url: "http://<CITY_HOST>:<PORT>/debug/state"
  event_path: "/event"
  access_token: "<TOKEN_FROM_ORGANIZERS>"
```

Важно:

- локальный сервер не принимает команды напрямую от города
- локальный сервер только читает `debug/state`
- события в город идут только `POST /event`

## 17. Типовой сценарий защиты

1. Открыть `dashboard`
2. Показать конфиги и подключённые устройства
3. На `route-builder` построить пеший маршрут
4. На `devices` симулировать face-подтверждение
5. На `transport` запустить транспортный сценарий
6. Симулировать препятствие и показать reroute
7. Показать ETA и предупреждение о пробке
8. На `mfc` запустить indoor-сценарий
9. Показать включение виброплатформы
10. На `logs` показать историю событий

## 18. Типовые проблемы

### Не запускается `uvicorn`

Проверь:

- активировано ли виртуальное окружение
- установлен ли `PYTHONPATH=src`
- выполнена ли команда `pip install -e .[dev]`

### Не открываются русские страницы в браузере

HTML уже сохранён в UTF-8. Если в PowerShell вывод выглядит «кракозябрами», это проблема консоли, а не файлов браузера.

### Нет данных о городе

Если городской сервер недоступен, интерфейс всё равно откроется, но:

- ETA не будет обновляться из реальных данных
- в логах появятся предупреждения poller'а

### В логах было `failed to send city event`

Это означает, что локальный сервер попытался отправить событие в городской сервер, но тот:

- недоступен по сети
- выключен
- недостижим с текущего Wi‑Fi
- принимает соединение нестабильно
- либо вы используете тестовый placeholder-токен вместо реального

Что делать:

1. Для локальной разработки выставить `city.enabled: false`
2. Для реального стенда включить `city.enabled: true`
3. Проверить доступность:

```powershell
curl.exe http://<CITY_HOST>:<PORT>/debug/state
```

4. Проверить, что указан реальный `X-Access-Token`

### Устройство не получает команд

Проверь:

- корректность `LOCAL_SERVER_BASE`
- совпадение `DEVICE_ID`
- Wi-Fi
- endpoint `GET /api/device/commands/{device_id}`

## 19. Полезные команды

Запуск сервера:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python -m uvicorn digital_guide.main:app --reload --host 127.0.0.1 --port 8080
```

Запуск тестов:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python -m pytest -q
```

Получить текущее состояние:

```powershell
curl.exe http://127.0.0.1:8080/api/state
```

## 20. Текущее состояние репозитория

Сейчас в проекте уже есть:

- рабочий backend-каркас
- русифицированный веб-интерфейс
- simulation mode
- стартовые конфиги
- каркасы прошивок
- тесты для ключевой логики

Базовая проверка:

- `python -m pytest -q` -> `9 passed`
