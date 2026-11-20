# KrakenBot — API новостей

Двусторонняя синхронизация новостей между Discord и сайтом.

```
Сайт ──── HTTP API бота (этот документ) ────▶ Discord
Сайт ◀─── вебхук SITE_WEBHOOK_URL ─────────── Discord (кнопка «📰 Опубликовать новость»)
```

- Новость, созданная **на сайте**, отправляется в API → бот публикует её в Discord и возвращает `id`.
- Новость, опубликованная **в Discord** кнопкой, бот отправляет на сайт вебхуком `news.created`.
- Новость, **удалённую в Discord**, бот отправляет на сайт вебхуком `news.deleted`.
- Изменения и удаления, которые сайт сделал через API, обратно на сайт **не** отправляются (нет эха).

Обращаться к API нужно **с сервера сайта (бэкенда)**. Из браузера нельзя: тогда `API_TOKEN` увидит любой посетитель.

## Адрес и авторизация

```
Базовый адрес:  https://<домен-бота>/api      (до настройки HTTPS: http://<IP сервера бота>:8080/api)
Заголовок:      Authorization: Bearer <API_TOKEN>
Формат:         JSON, UTF-8
```

`API_TOKEN` задаётся в `.env` бота. Без него API выключено. Все id (Discord snowflake) передаются **строками**: в JavaScript такие числа теряют точность.

## Объект новости

```json
{
  "id": "1309876543210987654",
  "external_id": "site-42",
  "source": "site",
  "guild_id": "1234567890",
  "channel_id": "1571844747327316069",
  "title": "Обновление 0.3",
  "text": "Новые снасти и водоёмы…",
  "images": ["https://example.com/cover.png"],
  "author": {"id": "…", "name": "Команда", "avatar_url": "https://…"},
  "url": "https://discord.com/channels/…/…/…",
  "created_at": "2026-11-20T12:00:00+00:00",
  "updated_at": "2026-11-20T12:00:00+00:00"
}
```

| Поле | Описание |
|---|---|
| `id` | id новости = id сообщения в Discord |
| `external_id` | id новости на сайте (если сайт его передал) |
| `source` | `site` — создана через API, `discord` — кнопкой в Discord |
| `images` | ссылки на картинки. У новостей из Discord это вложения Discord, **со временем они перестают открываться**, поэтому сайту нужно скачивать картинки к себе |
| `author` | для новостей из Discord — автор в Discord; для новостей с сайта — то, что передал сайт, или `null` |

## Методы

### `GET /api/health`
Без авторизации. `{"ok": true}`, когда бот подключён к Discord.

### `POST /api/news` — создать новость (сайт → Discord)

```json
{
  "title": "Обновление 0.3",
  "text": "Новые снасти и водоёмы…",
  "external_id": "site-42",
  "images": ["https://example.com/cover.png"],
  "mention": "none",
  "author": {"name": "Команда FISHING UNBOUND", "avatar_url": "https://example.com/logo.png"},
  "channel_id": "1571844747327316069",
  "guild_id": "1234567890"
}
```

| Поле | Обяз. | Описание |
|---|---|---|
| `title` | да | до 250 символов |
| `text` | да | до 4000 символов, поддерживается Markdown Discord |
| `external_id` | нет | id на сайте. Повторный запрос с тем же `external_id` **не создаёт дубль**, а возвращает уже созданную новость (200) |
| `images` | нет | до 10 публичных ссылок `http(s)` на картинки, в Discord они показываются сеткой |
| `mention` | нет | `none` (по умолчанию), `everyone`, `here` |
| `author` | нет | подпись над новостью в Discord |
| `channel_id` | нет | канал Discord; по умолчанию «Канал новостей» из `/setup` |
| `guild_id` | нет | нужен, только если бот на нескольких серверах |

Ответ: `201` и объект новости. Сохраните у себя `id`: он нужен для изменения и удаления.

### `GET /api/news?limit=20&offset=0&guild_id=…`
`{"items": [...], "total": N}`, новые сверху. Бот хранит последние 100 новостей.

### `GET /api/news/{id}`
Объект новости или `404`.

### `PATCH /api/news/{id}` — изменить
`{"title": "…", "text": "…"}`, можно передать любое из полей. Бот редактирует сообщение в Discord. Картинки и упоминание не меняются. Ответ — обновлённый объект.

### `DELETE /api/news/{id}` — удалить
Удаляет сообщение в Discord. Ответ `204`.

### Ошибки

```json
{"error": "validation_error", "message": "Поле title обязательно и должно быть непустой строкой"}
```

| Код | `error` | Когда |
|---|---|---|
| 400 | `bad_json`, `bad_query`, `guild_required` | неверный запрос |
| 401 | `unauthorized` | нет или неверный `API_TOKEN` |
| 403 | `discord_forbidden` | у бота нет прав в канале |
| 404 | `not_found`, `guild_not_found` | новость или сервер не найдены |
| 409 | `news_channel_not_set` | не передан `channel_id`, и канал новостей не выбран в `/setup` |
| 410 | `gone` | сообщение новости уже удалено в Discord |
| 422 | `validation_error` | поля не прошли проверку |

## Вебхук на сайт (Discord → сайт)

Когда задан `SITE_WEBHOOK_URL`, бот отправляет `POST` на этот адрес:

```
POST {SITE_WEBHOOK_URL}
Content-Type: application/json
X-Kraken-Event: news.created
X-Kraken-Signature: sha256=<hex HMAC-SHA256 тела запроса ключом SITE_WEBHOOK_SECRET>

{"event": "news.created", "data": { …объект новости… }}
```

| Событие | Когда |
|---|---|
| `news.created` | новость опубликована кнопкой в Discord |
| `news.deleted` | сообщение новости удалили в Discord |

Сайт должен ответить кодом `2xx`. При ошибке или таймауте (15 с) бот повторяет отправку через 5 с, 30 с и 2 мин. Одно событие может прийти дважды, поэтому сайту нужно проверять `data.id` на дубли.

Проверка подписи (Node.js):

```js
const crypto = require("crypto");
const expected = "sha256=" + crypto.createHmac("sha256", process.env.SITE_WEBHOOK_SECRET).update(rawBody).digest("hex");
const ok = crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(req.headers["x-kraken-signature"] || ""));
```

PHP:

```php
$expected = 'sha256=' . hash_hmac('sha256', file_get_contents('php://input'), getenv('SITE_WEBHOOK_SECRET'));
$ok = hash_equals($expected, $_SERVER['HTTP_X_KRAKEN_SIGNATURE'] ?? '');
```

Подпись считается от **сырого тела запроса**: не пересобирайте JSON перед проверкой.

## Пример

```bash
curl -X POST https://<домен-бота>/api/news \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Тест","text":"Проверка API","external_id":"test-1"}'
```
