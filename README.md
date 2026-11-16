# KrakenBot — тикеты для Discord

## Запуск
1. https://discord.com/developers/applications → New Application → Bot → скопировать токен.
2. Пригласить бота (OAuth2 → URL Generator, scope `bot` + `applications.commands`) с правами:
   Manage Channels, Manage Roles, Manage Threads, Create Private Threads,
   Send Messages, Send Messages in Threads, Embed Links, Read Message History, View Channels.
3. `pip install -r requirements.txt`
4. Скопировать `.env.example` в `.env` и вписать токен.
5. `python bot.py`

При запуске бот сам создаёт канал `#тикеты` (если его нет), выставляет права и публикует панель с кнопкой.

## Как работает
- **Создать тикет** → форма: категория (выпадающий список), тема, описание, версия/платформа.
- Создаётся приватная ветка `#0001 · 🐞 Баг`, туда добавляется автор, пингуется роль поддержки.
- Видят ветку только автор и роль `898463898582253568` (через право «Управлять ветками» в канале).
- Кнопки **Взят в работу / Исправлен / Не актуально** работают только у этой роли.
  «Исправлен» и «Не актуально» архивируют и блокируют ветку; «Взят в работу» открывает её снова.

Настройки (роль, имя канала, категории) — в начале `bot.py`. Номера тикетов хранятся в `data.json`.
