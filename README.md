# NTO Smart City Local Server

Локальный сервер для инженерного тура НТО по профилю «Умный город». Проект принимает события от умных устройств, валидирует `TYPE 1..6`, пересылает их на городской сервер и показывает локальный мониторинг в веб-интерфейсе.

## Что уже реализовано

- Отправка событий `TYPE 1..6` на городской сервер `http://192.168.31.63:8000/event`.
- Заголовок `X-Access-Token` уже заполнен токеном организаторов `kids8461`.
- Фоновый опрос страницы состояния города `http://192.168.31.63:8000/debug/state`.
- Локальный UI для ручной отправки событий, просмотра логов, очереди озвучки, TYPE 2/3 и состояния виброплатформы.
- Локальные endpoints для датчиков температуры/влажности/давления и датчика расстояния.
- Автоматические рекомендации по одежде, предупреждения по пробкам и препятствиям.
- Пример ESP32-скетча для связи не напрямую с городом, а через ваш локальный сервер.

## Откуда взяты параметры

- Форматы `TYPE 1..6`, ограничения и общая схема обмена взяты из PDF ТЗ.
- URL `/event` и `/debug/state`, а также заголовок `X-Access-Token` подтверждены по примерам из папки участников.
- Частоты TYPE 1, цвета TYPE 2 и `hero_user_id` вынесены в [config/reference-data.json](config/reference-data.json) на основе `signals.xlsx` и `heroes.docx`.

## Быстрый старт

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Откройте `http://localhost:8080`.

## Что настроить перед защитой

1. Проверьте [config/team.json](config/team.json):
   - `team_id`
   - `hero_user_id`
   - `devices.type1_ids`
   - `devices.type2_ids`
   - `devices.vibration_device_id`
   - `bus.target_stop_id`
   - `bus.route_by_bus`
2. Если локальный сервер будет слушать не `8080`, обновите `.env`.
3. В [arduino/local_bridge_example.ino](arduino/local_bridge_example.ino) укажите IP ноутбука в сети `NTO_MGBOT_CITY`.

## Основные локальные API

- `POST /api/events`
- `POST /api/sensors/environment`
- `POST /api/sensors/distance`
- `GET /api/state`
- `GET /api/city/raw`
- `GET /api/devices/{device_id}/command?device_type=type1|type2|vibration`

Пример `TYPE 5`:

```json
{
  "type": 5,
  "location_id": 12,
  "obstacle_type": "construction",
  "reroute_required": true,
  "message": "На вашем маршруте строительные работы. Маршрут перестроен."
}
```

## Что важно понимать

- Из этой среды городской сервер `192.168.31.63:8000` не отвечал по таймауту, поэтому реальная интеграция не была прогнана end-to-end.
- Проект готов к запуску на площадке, но `team.json` нужно довести до вашего конкретного микрорайона.
- ETA автобуса считается по данным `current_stop/timestamp`; для хорошей точности лучше задать `route_by_bus`.
