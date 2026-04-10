Положите сюда фотографии для локального распознавания лиц.

Структура:

`data/faces/<user_id>/image_01.jpg`

Пример:

`data/faces/blind_user_01/frame1.jpg`
`data/faces/blind_user_01/frame2.jpg`
`data/faces/blind_user_02/frame1.jpg`

После добавления снимков:

1. `POST /faces/retrain`
2. Или `POST /faces/train` с multipart-файлами
