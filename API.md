# KrakenBot — API новостей FISHING UNBOUND

Источник правды — хранилище бота на основном сервере. Сайт (Netlify) читает и публикует новости через этот API,
бот публикует их в Discord. Новости, опубликованные кнопкой в Discord, сразу видны в `GET /api/news`.

```
Браузер ─▶ Netlify Functions ─HTTPS─▶ API бота ─▶ Discord
                                         ▲
Discord (кнопка «📰 Опубликовать новость») ┘   → та же лента GET /api/news
```

## Адрес

```
https://45-134-217-51.sslip.io:47821/api
```

HTTPS обеспечивает Caddy с сертификатом Let's Encrypt. `45-134-217-51.sslip.io` — бесплатное имя, указывающее на IP сервера. Если появится свой домен, поменяется только адрес. В Netlify: `FISHING_UPSTREAM_API_URL=https://45-134-217-51.sslip.io:47821/api`.

## Ключи и вход

| Что | Где хранится | Как передаётся |
|---|---|---|
| Сервисный ключ `API_TOKEN` бота | `.env` бота и `FISHING_UPSTREAM_API_KEY` в Netlify | `X-API-Key: <ключ>` на всех непубличных запросах |
| Токен администратора | выдаёт `/api/auth/login`, Netlify держит в HttpOnly cookie | `Authorization: Bearer <access_token>` |
| Ключ только для чтения (необязательно) | `API_PUBLIC_READ_KEY` бота и `FISHING_PUBLIC_READ_API_KEY` в Netlify | `X-API-Key` на `GET /api/news`. Если не задан, чтение открыто |

**Учётные записи администраторов выдаёт бот.** Участник с правом «🔑 Админка сайта» (назначается ролям в `/setup` → Права) нажимает в Discord-канале команды кнопку **🔑 Доступ к сайту** и получает личный логин и пароль. Права проверяются **на каждом запросе**: если у человека забрали роль с этим правом, его токен сразу перестаёт работать. Повторное нажатие кнопки выдаёт новый пароль и закрывает все старые входы. Сервисный ключ без токена администратора права на публикацию не даёт.

## Публичные методы

### `GET /api` и `GET /api/health`
`{"ok": true, "service": "KrakenBot news API"}`

### `GET /api/news?limit=50&offset=0`
Только опубликованные новости, новые сверху:

```json
{
  "news": [
    {
      "id": "1309876543210987654",
      "date": "2026-11-20",
      "title": "Обновление 0.3",
      "body": "Новые снасти и водоёмы…",
      "images": ["https://…"],
      "url": "https://discord.com/channels/…",
      "source": "site",
      "author": "Luviel",
      "external_id": null,
      "created_at": "2026-11-20T12:00:00+00:00",
      "updated_at": "2026-11-20T12:00:00+00:00"
    }
  ],
  "total": 1
}
```

`id` — строка (id сообщения в Discord). `source` — `site` или `discord`. Записи с пустым `title` или `body` не отдаются, у каждой есть `date` в формате `YYYY-MM-DD`. Картинки новостей из Discord — ссылки на вложения Discord, со временем они перестают открываться.

### `GET /api/news/{id}`
Одна новость или `404`.

## Авторизация

### `POST /api/auth/login`
Заголовки: `X-API-Key`. Тело: `{"username": "luviel.dev", "password": "…"}`

`200 {"access_token": "…", "token_type": "Bearer", "expires_in": 3600}`, при неверных данных — `401`. После 5 неудачных попыток вход по этому логину блокируется на 15 минут (`429`).

### `GET /api/auth/me`
Заголовки: `X-API-Key`, `Authorization: Bearer`.

```json
{"authenticated": true, "user": {"username": "luviel.dev", "name": "Luviel", "discord_id": "…", "avatar_url": "…", "roles": ["admin"]}, "expires_at": 1795167384}
```

### `POST /api/auth/logout`
Заголовки: `X-API-Key`, `Authorization: Bearer`. Отзывает токен на сервере. `{"ok": true}`

## Публикация (администратор)

Все методы требуют `X-API-Key` и `Authorization: Bearer <access_token>`.

### `POST /api/news`

```json
{"date": "2026-11-20", "title": "Обновление 0.3", "body": "Текст новости", "status": "published"}
```

| Поле | Обяз. | |
|---|---|---|
| `title` | да | до 250 символов |
| `body` | да | до 4000 символов, Markdown Discord |
| `date` | нет | `YYYY-MM-DD`, по умолчанию — сегодня |
| `status` | нет | только `published` |
| `images` | нет | до 10 ссылок `https://` на картинки, в Discord показываются сеткой |
| `mention` | нет | `none` (по умолчанию), `everyone`, `here` |
| `external_id` | нет | id на сайте для защиты от дублей; можно передать заголовком `Idempotency-Key` |

Ответ: `201` и новость в формате ленты. Новость сразу публикуется в Discord в «Канал новостей» из `/setup`, автором указывается вошедший администратор.

**Защита от дублей.** Повтор с тем же `external_id` / `Idempotency-Key`, а без них — та же пара `title` + `body` в течение 10 минут, возвращает уже созданную новость (`200`) и не публикует её второй раз.

### `PATCH /api/news/{id}`
`{"title": "…", "body": "…", "date": "YYYY-MM-DD"}`, любое из полей. Меняет новость и сообщение в Discord.

### `DELETE /api/news/{id}`
Удаляет новость и сообщение в Discord. Ответ `204`.

## Ошибки

`{"error": "<код>", "message": "<описание по-русски>"}`

| HTTP | `error` | Когда |
|---|---|---|
| 400 | `bad_json`, `bad_query`, `guild_required` | неверный запрос |
| 401 | `unauthorized`, `invalid_credentials` | нет ключа, неверный логин или пароль, токен истёк, нет права «Админка сайта» |
| 403 | `discord_forbidden` | у бота нет прав в канале новостей |
| 404 | `not_found` | новость не найдена |
| 409 | `news_channel_not_set` | в `/setup` не выбран канал новостей |
| 410 | `gone` | сообщение уже удалено в Discord |
| 422 | `validation_error` | поля не прошли проверку |
| 429 | `too_many_attempts` | много неудачных входов |

## Вебхук на сайт (необязательно)

Сайт и так читает ленту из `GET /api/news`, поэтому вебхук не обязателен. Если сайту нужно узнавать о новостях сразу, укажите в `.env` бота `SITE_WEBHOOK_URL` и `SITE_WEBHOOK_SECRET`. Тогда бот отправит `POST` с событием `news.created` (публикация кнопкой в Discord) или `news.deleted` (сообщение удалено в Discord):

```
X-Kraken-Event: news.created
X-Kraken-Signature: sha256=<HMAC-SHA256 сырого тела ключом SITE_WEBHOOK_SECRET>

{"event": "news.created", "data": { …внутренний объект новости… }}
```

Если сайт не ответил кодом `2xx`, бот повторяет отправку через 5 с, 30 с и 2 мин.

## Служебный доступ

`Authorization: Bearer <API_TOKEN>` (без входа) даёт полный доступ к API. Он нужен для скриптов на самом сервере, Netlify им не пользуется.
