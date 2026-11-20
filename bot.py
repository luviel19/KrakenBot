"""KrakenBot — бот тикетов для Discord-сервера разработчика игры."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands, ui

# ---------------------------------------------------------------- настройки

# Каждый сервер настраивается мастером (/setup или сообщение в ЛС при добавлении бота),
# настройки хранятся в data.json. Значения ниже — для сервера, где бот работал до мастера:
# при первом запуске они переносятся в его настройки автоматически.
STAFF_ROLE_ID = 898463898582253568
TICKET_CHANNEL_ID = 1571844747327316069

# имена каналов, которые бот создаёт, если в мастере выбрано «Создать новый»
TICKET_CHANNEL_NAME = "тикеты"
STAFF_CHANNEL_NAME = "функционал-бота"
DATA_FILE = Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / "data.json"
TICKET_COOLDOWN = 120  # секунд между тикетами одного игрока (на роль поддержки не действует)

GAME_NAME = "FISHING UNBOUND"
GAME_INFO = (
    f"Добро пожаловать в сообщество {GAME_NAME}!\n\n"
    "Мы создаём рыболовную игру с акцентом на реалистичные снасти, "
    "поведение рыбы и атмосферу настоящей рыбалки.\n\n"
    "Здесь можно следить за официальными новостями разработки, "
    "предлагать идеи и сообщать об ошибках."
)
HUB_GREETING = "Добро пожаловать, рыболов! 🎣"
GAME_COLOR = discord.Color.from_rgb(36, 116, 132)

# для новых серверов, где в мастере не задали своё описание
DEFAULT_GAME_NAME = "Наша игра"
DEFAULT_GAME_INFO = (
    "Добро пожаловать в сообщество {name}!\n\n"
    "Здесь можно следить за новостями разработки, предлагать идеи и сообщать об ошибках."
)
DEFAULT_HUB_GREETING = "Добро пожаловать! 👋"

# ключ -> (название, эмодзи, описание в выпадающем списке)
CATEGORIES = {
    "bug": ("Баг", "🐞", "Ошибка в игре, которая мешает, но не ломает её"),
    "critical": ("Критичный баг", "🔥", "Вылет, потеря прогресса, игра не запускается"),
    "suggestion": ("Предложение", "💡", "Идея по улучшению игры"),
    "balance": ("Баланс", "⚖️", "Слишком сильно / слабо / нечестно"),
    "account": ("Аккаунт и покупки", "💳", "Проблемы с аккаунтом, оплатой, донатом"),
    "player": ("Жалоба на игрока", "🚩", "Читы, оскорбления, нарушение правил"),
    "question": ("Вопрос", "❓", "Вопрос по игре или серверу"),
    "other": ("Другое", "📦", "Всё, что не подошло выше"),
}

# ключ -> (подпись, цвет эмбеда)
STATUSES = {
    "open": ("🟢 Открыт", discord.Color.green()),
    "in_progress": ("🟡 Взят в работу", discord.Color.gold()),
    "fixed": ("✅ Исправлен", discord.Color.blurple()),
    "not_relevant": ("⛔ Не актуально", discord.Color.dark_grey()),
}
# категория тикетов из кнопки «Есть идея» под опросом (не показывается в форме тикета)
POLL_IDEA_CATEGORY = ("Идея к опросу", "💬", "")
ALL_CATEGORIES = {**CATEGORIES, "poll_idea": POLL_IDEA_CATEGORY}

MENTIONS = {
    "everyone": "@everyone",
    "here": "@here",
    "none": "Без упоминания",
}

CLOSED_STATUSES = {"fixed", "not_relevant"}

log = logging.getLogger("krakenbot")


# ---------------------------------------------------------------- хранилище


class Storage:
    """Счётчики тикетов и id сообщений-панелей в data.json."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.data = {
            "counters": {}, "panels": {}, "staff_panels": {}, "tickets": {}, "polls": {},
            "news": {}, "progress": {}, "config": {}, "setup_drafts": {},
        }
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    async def _save(self):
        # снимок данных берём сразу, а сам файл пишем в отдельном потоке — бот в это время
        # продолжает отвечать на нажатия; lock гарантирует, что записи не перепутаются
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._write, text)

    def _write(self, text: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)

    async def next_ticket_number(self, guild_id: int) -> int:
        async with self.lock:
            number = self.data["counters"].get(str(guild_id), 0) + 1
            self.data["counters"][str(guild_id)] = number
            await self._save()
            return number

    async def pop(self, section: str, key: int):
        async with self.lock:
            if self.data[section].pop(str(key), None) is not None:
                await self._save()

    async def set(self, section: str, key: int, value):
        async with self.lock:
            self.data[section][str(key)] = value
            await self._save()

    def get(self, section: str, key: int):
        return self.data[section].get(str(key))


storage = Storage(DATA_FILE)
last_ticket_at: dict[tuple[int, int], float] = {}


def guild_config(guild_id: int) -> dict:
    return storage.get("config", guild_id) or {}


def staff_role_id(guild_id: int) -> int | None:
    return guild_config(guild_id).get("staff_role_id")


def staff_mention(guild_id: int) -> str:
    role_id = staff_role_id(guild_id)
    return f"<@&{role_id}>" if role_id else "команда сервера"


def game_name(guild_id: int) -> str:
    return guild_config(guild_id).get("game_name") or DEFAULT_GAME_NAME


def game_info(guild_id: int) -> str:
    return guild_config(guild_id).get("game_info") or DEFAULT_GAME_INFO.format(name=game_name(guild_id))


def is_staff(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    role_id = staff_role_id(member.guild.id)
    return role_id is not None and member.get_role(role_id) is not None


def can_configure(member: discord.Member | None) -> bool:
    return member is not None and (member.guild_permissions.manage_guild or member.guild_permissions.administrator)


# ---------------------------------------------------------------- форма тикета


class TicketModal(ui.Modal, title="Новый тикет"):
    category = ui.Label(
        text="Категория",
        component=ui.Select(
            placeholder="Выберите категорию",
            options=[
                discord.SelectOption(label=label, value=key, emoji=emoji, description=desc)
                for key, (label, emoji, desc) in CATEGORIES.items()
            ],
        ),
    )
    subject = ui.Label(
        text="Тема",
        component=ui.TextInput(placeholder="Коротко: что случилось", max_length=100),
    )
    description = ui.Label(
        text="Описание",
        description="Для бага: шаги воспроизведения, что ожидали и что получили",
        component=ui.TextInput(style=discord.TextStyle.paragraph, max_length=2000),
    )
    version = ui.Label(
        text="Версия игры / платформа",
        component=ui.TextInput(placeholder="Например: 1.4.2, Windows", required=False, max_length=100),
    )

    def __init__(self, category: str | None = None):
        super().__init__()
        if category:
            for option in self.category.component.options:
                option.default = option.value == category

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(
            interaction,
            category_key=self.category.component.values[0],
            subject=self.subject.component.value,
            description=self.description.component.value,
            version=self.version.component.value,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ошибка при создании тикета", exc_info=error)
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send("Не удалось создать тикет, попробуйте ещё раз.", ephemeral=True)


async def create_ticket(interaction: discord.Interaction, *, category_key, subject, description,
                        version=None, poll_message: discord.Message | None = None):
    """Создаёт приватную ветку-тикет в канале тикетов и отправляет в неё карточку с кнопками статуса."""
    channel = ticket_channel(interaction.guild)
    if channel is None:
        await interaction.response.send_message("Канал тикетов не найден, напишите администрации.", ephemeral=True)
        return

    key = (interaction.guild.id, interaction.user.id)
    wait = TICKET_COOLDOWN - (time.monotonic() - last_ticket_at.get(key, -TICKET_COOLDOWN))
    if wait > 0 and not is_staff(interaction.user):
        await interaction.response.send_message(
            f"⏳ Следующий тикет можно создать через {int(wait) + 1} сек.", ephemeral=True
        )
        return
    last_ticket_at[key] = time.monotonic()

    await interaction.response.defer(ephemeral=True, thinking=True)

    label, emoji, _ = ALL_CATEGORIES[category_key]
    number = await storage.next_ticket_number(interaction.guild.id)

    thread = await channel.create_thread(
        name=f"#{number:04d} · {emoji} {label}",
        type=discord.ChannelType.private_thread,
        invitable=False,
        auto_archive_duration=10080,
        reason=f"Тикет #{number} от {interaction.user}",
    )
    await thread.add_user(interaction.user)

    embed = ticket_embed(
        number=number,
        category_key=category_key,
        author=interaction.user,
        subject=subject,
        description=description,
        version=version,
        poll_message=poll_message,
    )
    await thread.send(
        content=f"{interaction.user.mention} создал тикет. {staff_mention(interaction.guild.id)}",
        embed=embed,
        view=StatusView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
    )
    await storage.set("tickets", thread.id, {
        "number": number,
        "author_id": interaction.user.id,
        "category": category_key,
        "status": "open",
        "poll_message_id": poll_message.id if poll_message else None,
    })

    await interaction.followup.send(
        f"Тикет **#{number:04d}** ({emoji} {label}) создан: {thread.mention}", ephemeral=True
    )


def ticket_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return config_channel(guild, "ticket_channel_id")


def staff_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return config_channel(guild, "staff_channel_id")


def config_channel(guild: discord.Guild, key: str) -> discord.TextChannel | None:
    # каналы ищутся только по id, поэтому их можно переименовывать
    channel = guild.get_channel(guild_config(guild.id).get(key) or 0)
    return channel if isinstance(channel, discord.TextChannel) else None


def ticket_embed(*, number, category_key, author, subject, description, version,
                 poll_message: discord.Message | None = None) -> discord.Embed:
    label, emoji, _ = ALL_CATEGORIES[category_key]
    status_text, color = STATUSES["open"]
    embed = discord.Embed(
        title=f"Тикет #{number:04d} — {subject}",
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Категория", value=f"{emoji} {label}")
    embed.add_field(name="Статус", value=status_text)
    embed.add_field(name="Автор", value=author.mention)
    if version:
        embed.add_field(name="Версия / платформа", value=version, inline=False)
    if poll_message:
        poll = storage.get("polls", poll_message.id)
        title = poll["title"] if poll else "опрос"
        embed.add_field(name="К опросу", value=f"[{title}]({poll_message.jump_url})", inline=False)
    embed.set_footer(text=f"ID автора: {author.id}")
    return embed


# ---------------------------------------------------------------- кнопки


class PanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Создать тикет", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ticket:create")
    async def create(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TicketModal())


class StatusView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_staff(interaction.user):
            return True
        await interaction.response.send_message(
            f"Менять статус тикета может только {staff_mention(interaction.guild.id)}.", ephemeral=True
        )
        return False

    @ui.button(label="Взят в работу", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="ticket:in_progress")
    async def in_progress(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_status(interaction, "in_progress")

    @ui.button(label="Исправлен", emoji="✅", style=discord.ButtonStyle.success, custom_id="ticket:fixed")
    async def fixed(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_status(interaction, "fixed")

    @ui.button(label="Не актуально", emoji="⛔", style=discord.ButtonStyle.secondary, custom_id="ticket:not_relevant")
    async def not_relevant(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_status(interaction, "not_relevant")

    async def set_status(self, interaction: discord.Interaction, status: str):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread) or not interaction.message.embeds:
            await interaction.response.send_message("Это не сообщение тикета.", ephemeral=True)
            return

        status_text, color = STATUSES[status]
        embed = interaction.message.embeds[0]
        embed.color = color
        for i, field in enumerate(embed.fields):
            if field.name == "Статус":
                embed.set_field_at(i, name="Статус", value=status_text, inline=field.inline)
            elif field.name == "Ответственный":
                embed.set_field_at(i, name="Ответственный", value=interaction.user.mention, inline=field.inline)
        if not any(f.name == "Ответственный" for f in embed.fields):
            embed.insert_field_at(3, name="Ответственный", value=interaction.user.mention)

        await interaction.response.defer()
        if thread.archived or thread.locked:
            await thread.edit(archived=False, locked=False)
        await interaction.message.edit(embed=embed)

        ticket = storage.get("tickets", thread.id)
        author_mention = f"<@{ticket['author_id']}>" if ticket else ""
        await thread.send(
            f"{author_mention} Статус тикета: **{status_text}** ({interaction.user.mention})",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        if ticket:
            ticket["status"] = status
            await storage.set("tickets", thread.id, ticket)

        if status in CLOSED_STATUSES:
            await thread.edit(archived=True, locked=True, reason=f"{status_text} — {interaction.user}")


# ---------------------------------------------------------------- опросы


class PostModal(ui.Modal):
    """Пост от команды: опрос (с кнопкой «Есть идея») или новость."""

    target = ui.Label(
        text="Канал",
        description="Куда отправить пост",
        component=ui.ChannelSelect(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Выберите канал",
            required=True,
        ),
    )
    mention = ui.Label(
        text="Упоминание",
        component=ui.Select(
            options=[
                discord.SelectOption(label=label, value=key, default=key == "everyone")
                for key, label in MENTIONS.items()
            ],
        ),
    )
    poll_title = ui.Label(
        text="Заголовок",
        component=ui.TextInput(placeholder="Например: Клубная система", max_length=200),
    )
    text = ui.Label(
        text="Текст",
        description="Концепция, вопросы к игрокам — всё, что нужно рассказать",
        component=ui.TextInput(style=discord.TextStyle.paragraph, max_length=4000),
    )
    images = ui.Label(
        text="Референсы",
        description="Картинки или видео, до 10 файлов",
        component=ui.FileUpload(required=False, max_values=10),
    )

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(title="Новый опрос" if kind == "poll" else "Новая новость")
        if kind == "news":
            self.poll_title.component.placeholder = "Например: Обновление 0.3 — новые снасти"
            self.text.description = "Что изменилось, что исправили, что дальше"
            self.images.text = "Картинки"

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(self.target.component.values[0].id)
        if channel is None:
            await interaction.response.send_message("Канал не найден.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        is_poll = self.kind == "poll"
        title = self.poll_title.component.value
        embed = discord.Embed(
            title=f"{'📢' if is_poll else '📰'} {title}",
            description=self.text.component.value,
            color=discord.Color.blurple() if is_poll else discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(
            text="Есть мысли? Нажмите «Есть идея» — ответ увидит только команда"
            if is_poll else f"Новости {game_name(interaction.guild.id)}"
        )

        files = [await a.to_file() for a in self.images.component.values]
        for i, f in enumerate(files, 1):
            # вставленные из буфера картинки все называются image.png — attachment:// перепутал бы их
            f.filename = f"ref{i}{Path(f.filename).suffix.lower()}"
        embeds = gallery_embeds(embed, [f.filename for f in files if is_image(f.filename)], channel.jump_url)

        mention_key = self.mention.component.values[0]
        try:
            message = await channel.send(
                content=MENTIONS[mention_key] if mention_key != "none" else None,
                embeds=embeds,
                files=files,
                view=PollView() if is_poll else None,
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except discord.Forbidden:
            await interaction.followup.send(f"У бота нет прав писать в {channel.mention}.", ephemeral=True)
            return

        if is_poll:
            await storage.set("polls", message.id, {
                "title": title,
                "channel_id": channel.id,
                "author_id": interaction.user.id,
                "ideas": 0,
            })
        else:
            news = storage.get("news", interaction.guild.id) or []
            news.insert(0, {"title": title, "text": self.text.component.value, "url": message.jump_url,
                            "date": discord.utils.utcnow().isoformat()})
            await storage.set("news", interaction.guild.id, news[:10])
        await interaction.followup.send(
            f"{'Опрос' if is_poll else 'Новость'} опубликован{'' if is_poll else 'а'}: {message.jump_url}",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ошибка при публикации поста", exc_info=error)
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send("Не удалось опубликовать пост, попробуйте ещё раз.", ephemeral=True)


def is_image(filename: str) -> bool:
    return filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def gallery_embeds(main: discord.Embed, images: list[str], base_url: str) -> list[discord.Embed]:
    """Картинки внутри поста сеткой: Discord склеивает до 4 эмбедов с одинаковым url в одну галерею."""
    embeds = [main]
    for start in range(0, len(images), 4):
        url = base_url if start == 0 else f"{base_url}?gallery={start // 4}"
        for i, filename in enumerate(images[start:start + 4]):
            if start == 0 and i == 0:
                embed = main
            else:
                embed = discord.Embed(url=url, color=main.color)
                embeds.append(embed)
            embed.url = url
            embed.set_image(url=f"attachment://{filename}")
    return embeds


class IdeaModal(ui.Modal, title="Ваша идея"):
    idea = ui.Label(
        text="Идея",
        description="Что добавить, убрать или сделать иначе",
        component=ui.TextInput(style=discord.TextStyle.paragraph, max_length=2000),
    )

    def __init__(self, poll_message: discord.Message):
        super().__init__()
        self.poll_message = poll_message

    async def on_submit(self, interaction: discord.Interaction):
        poll = storage.get("polls", self.poll_message.id)
        await create_ticket(
            interaction,
            category_key="poll_idea",
            subject=poll["title"] if poll else "опрос",
            description=self.idea.component.value,
            poll_message=self.poll_message,
        )
        if poll:
            poll["ideas"] += 1
            await storage.set("polls", self.poll_message.id, poll)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ошибка при отправке идеи", exc_info=error)
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send("Не удалось отправить идею, попробуйте ещё раз.", ephemeral=True)


class PollView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Есть идея", emoji="💡", style=discord.ButtonStyle.success, custom_id="poll:idea")
    async def idea(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(IdeaModal(interaction.message))


class StaffPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_staff(interaction.user):
            return True
        await interaction.response.send_message(f"Доступно только {staff_mention(interaction.guild.id)}.",
                                                ephemeral=True)
        return False

    @ui.button(label="Создать опрос", emoji="📢", style=discord.ButtonStyle.primary, custom_id="staff:poll")
    async def poll(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PostModal("poll"))

    @ui.button(label="Опубликовать новость", emoji="📰", style=discord.ButtonStyle.primary, custom_id="staff:news")
    async def news(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PostModal("news"))

    @ui.button(label="Ход разработки", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="staff:progress")
    async def progress(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ProgressModal(interaction.guild.id))

    @ui.button(label="Панель сообщества", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="staff:hub")
    async def hub(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(HubModal())


class ProgressModal(ui.Modal, title="Ход разработки"):
    text = ui.Label(
        text="Сводка",
        description="Что сейчас в работе. Игроки увидят это по кнопке «Разработка» и /progress",
        component=ui.TextInput(style=discord.TextStyle.paragraph, max_length=3500),
    )

    def __init__(self, guild_id: int):
        super().__init__()
        current = storage.get("progress", guild_id)
        if current:
            self.text.component.default = current["text"]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await storage.set("progress", interaction.guild.id, {
            "text": self.text.component.value,
            "date": discord.utils.utcnow().isoformat(),
        })
        await interaction.followup.send("✅ Сводка обновлена.", embed=progress_embed(interaction.guild.id),
                                        ephemeral=True)


class HubModal(ui.Modal, title="Панель сообщества"):
    target = ui.Label(
        text="Канал",
        description="Куда отправить панель с кнопками для игроков",
        component=ui.ChannelSelect(channel_types=[discord.ChannelType.text], required=True),
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(self.target.component.values[0].id)
        try:
            message = await channel.send(embed=hub_embed(interaction.guild.id), view=HubView())
        except (discord.Forbidden, AttributeError):
            await interaction.followup.send("У бота нет прав писать в этот канал.", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Панель опубликована: {message.jump_url}\nЕё можно закрепить.", ephemeral=True
        )


# ---------------------------------------------------------------- центр сообщества


def hub_embed(guild_id: int) -> discord.Embed:
    greeting = guild_config(guild_id).get("hub_greeting") or DEFAULT_HUB_GREETING
    embed = discord.Embed(
        title=f"🐙 {game_name(guild_id)} | Центр сообщества",
        description=(
            f"**{greeting}**\n\n"
            "Читайте новости, следите за разработкой, делитесь идеями "
            "и сообщайте об ошибках с помощью кнопок ниже."
        ),
        color=GAME_COLOR,
    )
    embed.set_footer(text="Идеи и ошибки уходят приватным тикетом — их видите только вы и команда")
    return embed


def info_embed(guild_id: int) -> discord.Embed:
    name = game_name(guild_id)
    embed = discord.Embed(title=f"🎮 {name}", description=game_info(guild_id), color=GAME_COLOR)
    embed.set_footer(text=f"{name} • Новости и статусы публикует команда разработки")
    return embed


def news_embed(guild_id: int) -> discord.Embed:
    embed = discord.Embed(title="📰 Новости разработки", color=discord.Color.gold())
    news = storage.get("news", guild_id) or []
    if not news:
        embed.description = "Официальных публикаций пока нет. Загляните позже!"
    for item in news[:4]:
        text = item["text"] if len(item["text"]) <= 300 else item["text"][:300].rstrip() + "…"
        date = discord.utils.format_dt(datetime.fromisoformat(item["date"]), "d")
        embed.add_field(name=item["title"][:256], value=f"{text}\n{date} · [Читать]({item['url']})", inline=False)
    return embed


def progress_embed(guild_id: int) -> discord.Embed:
    embed = discord.Embed(title="🛠️ Ход разработки", color=discord.Color.teal())
    progress = storage.get("progress", guild_id)
    if progress:
        embed.description = progress["text"]
        embed.timestamp = datetime.fromisoformat(progress["date"])
        embed.set_footer(text="Обновлено")
    else:
        embed.description = "Команда пока не опубликовала сводку разработки."
    return embed


class HubView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Об игре", emoji="🎣", style=discord.ButtonStyle.secondary, custom_id="hub:about")
    async def about(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(embed=info_embed(interaction.guild.id), ephemeral=True)

    @ui.button(label="Новости", emoji="📰", style=discord.ButtonStyle.secondary, custom_id="hub:news")
    async def news(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(embed=news_embed(interaction.guild.id), ephemeral=True)

    @ui.button(label="Разработка", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="hub:progress")
    async def progress(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(embed=progress_embed(interaction.guild.id), ephemeral=True)

    @ui.button(label="Идея", emoji="💡", style=discord.ButtonStyle.success, custom_id="hub:idea")
    async def idea(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TicketModal("suggestion"))

    @ui.button(label="Ошибка", emoji="🐞", style=discord.ButtonStyle.primary, custom_id="hub:bug")
    async def bug(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TicketModal("bug"))



# ---------------------------------------------------------------- мастер настройки
#
# Работает и в ЛС, и в /setup. В ЛС Discord не может показать роли и каналы сервера
# во встроенных списках, поэтому списки заполняются самим ботом (до 25 пунктов).
# id сервера зашит в custom_id, так что кнопки работают и после перезапуска бота.

NEW_CHANNEL = "new"


def setup_draft(guild: discord.Guild) -> dict:
    """Черновик настроек: то, что выбрано в мастере, поверх текущих настроек сервера."""
    draft = storage.get("setup_drafts", guild.id)
    if draft is None:
        cfg = guild_config(guild.id)
        draft = {
            "staff_role_id": cfg.get("staff_role_id"),
            "ticket_channel": cfg.get("ticket_channel_id") or NEW_CHANNEL,
            "staff_channel": cfg.get("staff_channel_id") or NEW_CHANNEL,
            "game_name": cfg.get("game_name"),
            "game_info": cfg.get("game_info"),
            "hub_greeting": cfg.get("hub_greeting"),
        }
    return draft


def setup_embed(guild: discord.Guild, draft: dict, note: str | None = None) -> discord.Embed:
    def channel_text(value, default_name):
        if value == NEW_CHANNEL:
            return f"➕ будет создан **#{default_name}**"
        channel = guild.get_channel(value)
        return channel.mention + f" (#{channel.name})" if channel else "⚠️ канал удалён — выберите другой"

    role = guild.get_role(draft["staff_role_id"] or 0)
    embed = discord.Embed(
        title=f"⚙️ Настройка KrakenBot — {guild.name}",
        description=(
            "Выберите в списках ниже, как бот будет работать на сервере, и нажмите **«Сохранить и запустить»**. "
            "Настройки можно изменить позже командой `/setup` на сервере."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="1. Роль поддержки",
        value=(f"{role.mention} (@{role.name})" if role else "❗ не выбрана")
        + "\nВидит все тикеты, меняет статусы, публикует опросы и новости",
        inline=False,
    )
    embed.add_field(
        name="2. Канал тикетов",
        value=channel_text(draft["ticket_channel"], TICKET_CHANNEL_NAME)
        + "\nКнопка «Создать тикет», внутри — приватные ветки",
        inline=False,
    )
    embed.add_field(
        name="3. Канал команды",
        value=channel_text(draft["staff_channel"], STAFF_CHANNEL_NAME)
        + "\nВидит только роль поддержки: опросы, новости, ход разработки",
        inline=False,
    )
    embed.add_field(
        name="4. Игра",
        value=f"**{draft['game_name'] or DEFAULT_GAME_NAME}** — кнопка «✏️ Игра», чтобы изменить название и описание",
        inline=False,
    )
    embed.set_footer(text="Бот выставит права в выбранных каналах. Нужны права «Управлять каналами» и «Управлять ролями».")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if note:
        embed.add_field(name="\u200b", value=note, inline=False)
    return embed


def setup_view(guild: discord.Guild, draft: dict) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(SetupSelect(guild, "role", draft))
    view.add_item(SetupSelect(guild, "tickets", draft))
    view.add_item(SetupSelect(guild, "staff", draft))
    view.add_item(SetupButton(guild.id, "save"))
    view.add_item(SetupButton(guild.id, "game"))
    return view


async def configurer(interaction: discord.Interaction, guild: discord.Guild) -> discord.Member | None:
    """Участник сервера, который нажал кнопку мастера (в ЛС его нужно запросить у Discord)."""
    if isinstance(interaction.user, discord.Member) and interaction.user.guild == guild:
        return interaction.user
    try:
        return guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)
    except discord.NotFound:
        return None


async def check_setup_access(interaction: discord.Interaction, guild: discord.Guild | None) -> bool:
    if guild is None:
        await interaction.response.send_message("Бота уже нет на этом сервере.", ephemeral=True)
        return False
    if not can_configure(await configurer(interaction, guild)):
        await interaction.response.send_message(
            "Настраивать бота могут участники с правом «Управлять сервером».", ephemeral=True
        )
        return False
    return True


class SetupSelect(ui.DynamicItem[ui.Select], template=r"setup:(?P<field>role|tickets|staff):(?P<gid>\d+)"):
    def __init__(self, guild: discord.Guild, field: str, draft: dict | None = None):
        self.guild = guild
        self.field = field
        draft = draft or setup_draft(guild)
        if field == "role":
            roles = [r for r in reversed(guild.roles) if not r.is_default() and not r.managed][:25]
            options = [
                discord.SelectOption(label=r.name[:100], value=str(r.id), default=r.id == draft["staff_role_id"])
                for r in roles
            ] or [discord.SelectOption(label="На сервере нет ролей — создайте роль и откройте /setup", value="0")]
            placeholder = "1. Роль поддержки"
        else:
            key = "ticket_channel" if field == "tickets" else "staff_channel"
            name = TICKET_CHANNEL_NAME if field == "tickets" else STAFF_CHANNEL_NAME
            channels = sorted(guild.text_channels, key=lambda c: c.position)[:24]
            options = [discord.SelectOption(label=f"➕ Создать новый #{name}", value=NEW_CHANNEL,
                                            default=draft[key] == NEW_CHANNEL)]
            options += [
                discord.SelectOption(
                    label=f"#{c.name}"[:100],
                    value=str(c.id),
                    description=(c.category.name if c.category else None),
                    default=c.id == draft[key],
                )
                for c in channels
            ]
            placeholder = "2. Канал тикетов" if field == "tickets" else "3. Канал команды"
        super().__init__(ui.Select(custom_id=f"setup:{field}:{guild.id}", placeholder=placeholder, options=options))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match):
        guild = interaction.client.get_guild(int(match["gid"]))
        if guild is None:
            raise ValueError("бот не на сервере")
        return cls(guild, match["field"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_setup_access(interaction, self.guild)

    async def callback(self, interaction: discord.Interaction):
        draft = setup_draft(self.guild)
        value = self.item.values[0]
        if self.field == "role":
            draft["staff_role_id"] = int(value) or None
        else:
            key = "ticket_channel" if self.field == "tickets" else "staff_channel"
            draft[key] = value if value == NEW_CHANNEL else int(value)
        await storage.set("setup_drafts", self.guild.id, draft)
        await interaction.response.edit_message(embed=setup_embed(self.guild, draft), view=setup_view(self.guild, draft))


class SetupButton(ui.DynamicItem[ui.Button], template=r"setup:(?P<action>save|game):(?P<gid>\d+)"):
    def __init__(self, guild_id: int, action: str):
        self.guild_id = guild_id
        self.action = action
        if action == "save":
            button = ui.Button(label="Сохранить и запустить", emoji="✅", style=discord.ButtonStyle.success,
                               custom_id=f"setup:save:{guild_id}")
        else:
            button = ui.Button(label="Игра", emoji="✏️", style=discord.ButtonStyle.secondary,
                               custom_id=f"setup:game:{guild_id}")
        super().__init__(button)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls(int(match["gid"]), match["action"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_setup_access(interaction, interaction.client.get_guild(self.guild_id))

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        draft = setup_draft(guild)
        if self.action == "game":
            await interaction.response.send_modal(GameModal(guild, draft))
            return

        if not guild.get_role(draft["staff_role_id"] or 0):
            await interaction.response.edit_message(
                embed=setup_embed(guild, draft, "❗ Сначала выберите **роль поддержки**."), view=setup_view(guild, draft)
            )
            return

        await interaction.response.defer()
        config = guild_config(guild.id)
        config.update({
            "staff_role_id": draft["staff_role_id"],
            "ticket_channel_id": None if draft["ticket_channel"] == NEW_CHANNEL else draft["ticket_channel"],
            "staff_channel_id": None if draft["staff_channel"] == NEW_CHANNEL else draft["staff_channel"],
            "game_name": draft["game_name"],
            "game_info": draft["game_info"],
            "hub_greeting": draft["hub_greeting"],
        })
        await storage.set("config", guild.id, config)

        error = await interaction.client.setup_guild(guild)
        if error:
            await interaction.edit_original_response(
                embed=setup_embed(guild, draft, f"❌ {error}\nИсправьте и нажмите «Сохранить и запустить» ещё раз."),
                view=setup_view(guild, draft),
            )
            return

        await storage.pop("setup_drafts", guild.id)
        tickets, staff = ticket_channel(guild), staff_channel(guild)
        done = discord.Embed(
            title=f"✅ KrakenBot настроен — {guild.name}",
            description=(
                f"**Тикеты:** {tickets.mention}\n"
                f"**Канал команды:** {staff.mention}\n"
                f"**Роль поддержки:** {staff_mention(guild.id)}\n\n"
                f"Дальше: в {staff.mention} нажмите **📌 Панель сообщества**, чтобы опубликовать "
                "кнопки для игроков, и **🛠️ Ход разработки**, чтобы заполнить сводку.\n\n"
                "Изменить настройки — команда `/setup` на сервере."
            ),
            color=discord.Color.green(),
        )
        await interaction.edit_original_response(embed=done, view=None)


class GameModal(ui.Modal, title="Игра"):
    name = ui.Label(
        text="Название игры",
        component=ui.TextInput(placeholder="Например: FISHING UNBOUND", max_length=60),
    )
    greeting = ui.Label(
        text="Приветствие на панели сообщества",
        component=ui.TextInput(placeholder=DEFAULT_HUB_GREETING, required=False, max_length=100),
    )
    info = ui.Label(
        text="Об игре",
        description="Показывается по кнопке «Об игре» и команде /info",
        component=ui.TextInput(style=discord.TextStyle.paragraph, required=False, max_length=2000),
    )

    def __init__(self, guild: discord.Guild, draft: dict):
        super().__init__()
        self.guild = guild
        self.name.component.default = draft["game_name"]
        self.greeting.component.default = draft["hub_greeting"]
        self.info.component.default = draft["game_info"]

    async def on_submit(self, interaction: discord.Interaction):
        draft = setup_draft(self.guild)
        draft["game_name"] = self.name.component.value.strip() or None
        draft["hub_greeting"] = self.greeting.component.value.strip() or None
        draft["game_info"] = self.info.component.value.strip() or None
        await storage.set("setup_drafts", self.guild.id, draft)
        await interaction.response.edit_message(embed=setup_embed(self.guild, draft), view=setup_view(self.guild, draft))


async def send_setup_dm(client: discord.Client, guild: discord.Guild) -> bool:
    """Отправляет мастер в ЛС тому, кто добавил бота (или владельцу сервера)."""
    user = None
    try:
        async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
            if entry.target and entry.target.id == client.user.id:
                user = entry.user
                break
    except discord.Forbidden:
        pass  # нет права «Просматривать журнал аудита» — напишем владельцу
    if user is None:
        user = guild.owner or await client.fetch_user(guild.owner_id)

    draft = setup_draft(guild)
    intro = (
        f"👋 Привет! Меня добавили на сервер **{guild.name}**.\n"
        "Давайте настроим тикеты, роль поддержки и каналы — это займёт минуту."
    )
    try:
        await user.send(intro, embed=setup_embed(guild, draft), view=setup_view(guild, draft))
        log.info("Сервер %s: мастер настройки отправлен в ЛС %s", guild, user)
        return True
    except discord.HTTPException:
        log.warning("Сервер %s: не удалось написать %s в ЛС", guild, user)

    channel = guild.system_channel or next(
        (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
    )
    if channel:
        await channel.send(
            f"👋 {user.mention}, я не смог написать вам в личные сообщения. "
            "Настройте бота командой **`/setup`** (нужно право «Управлять сервером»).",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    return False


# ---------------------------------------------------------------- бот


class KrakenBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.ready_once = False
        self.tree = app_commands.CommandTree(self)
        self.add_player_commands()

    async def setup_hook(self):
        # кнопки продолжают работать после перезапуска бота
        self.add_view(PanelView())
        self.add_view(StatusView())
        self.add_view(PollView())
        self.add_view(StaffPanelView())
        self.add_view(HubView())
        self.add_dynamic_items(SetupSelect, SetupButton)

    def add_player_commands(self):
        @self.tree.command(name="info", description="Информация об игре")
        @app_commands.guild_only()
        async def info(interaction: discord.Interaction):
            await interaction.response.send_message(embed=info_embed(interaction.guild.id), ephemeral=True)

        @self.tree.command(name="news", description="Последние новости разработки")
        @app_commands.guild_only()
        async def news(interaction: discord.Interaction):
            await interaction.response.send_message(embed=news_embed(interaction.guild.id), ephemeral=True)

        @self.tree.command(name="progress", description="Ход разработки")
        @app_commands.guild_only()
        async def progress(interaction: discord.Interaction):
            await interaction.response.send_message(embed=progress_embed(interaction.guild.id), ephemeral=True)

        @self.tree.command(name="idea", description="Предложить идею для игры")
        @app_commands.guild_only()
        async def idea(interaction: discord.Interaction):
            await interaction.response.send_modal(TicketModal("suggestion"))

        @self.tree.command(name="bug", description="Сообщить об ошибке в игре")
        @app_commands.guild_only()
        async def bug(interaction: discord.Interaction):
            await interaction.response.send_modal(TicketModal("bug"))

        @self.tree.command(name="ticket", description="Создать тикет")
        @app_commands.guild_only()
        async def ticket(interaction: discord.Interaction):
            await interaction.response.send_modal(TicketModal())

        @self.tree.command(name="setup", description="Настроить бота на этом сервере")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def setup(interaction: discord.Interaction):
            if not can_configure(interaction.user):
                await interaction.response.send_message("Нужно право «Управлять сервером».", ephemeral=True)
                return
            draft = setup_draft(interaction.guild)
            await interaction.response.send_message(
                embed=setup_embed(interaction.guild, draft), view=setup_view(interaction.guild, draft), ephemeral=True
            )

    async def on_ready(self):
        log.info("Вошёл как %s", self.user)
        if self.ready_once:
            return
        self.ready_once = True
        # серверы настраиваются параллельно, а не по очереди
        await asyncio.gather(*(self.start_guild(guild) for guild in self.guilds))

    async def on_guild_join(self, guild: discord.Guild):
        log.info("Добавлен на сервер %s", guild)
        await self.start_guild(guild)

    async def start_guild(self, guild: discord.Guild):
        await self.sync_commands(guild)
        await migrate_legacy_config(guild)
        if guild_config(guild.id).get("staff_role_id"):
            if error := await self.setup_guild(guild):
                log.error("Сервер %s: %s", guild, error)
        elif not guild_config(guild.id).get("setup_dm_sent"):
            # новый сервер: отправляем мастер настройки один раз
            await send_setup_dm(self, guild)
            await storage.set("config", guild.id, {**guild_config(guild.id), "setup_dm_sent": True})
        else:
            log.info("Сервер %s: не настроен — ждём /setup", guild)

    async def sync_commands(self, guild: discord.Guild):
        # регистрация на конкретном сервере появляется сразу, глобальная — до часа
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("Сервер %s: зарегистрировано команд: %s", guild, len(synced))
        except discord.Forbidden:
            log.error("Сервер %s: нет доступа к командам — пригласите бота со scope applications.commands", guild)

    async def setup_guild(self, guild: discord.Guild) -> str | None:
        """Создаёт/обновляет каналы и панели по настройкам сервера. Возвращает текст ошибки или None."""
        if guild.get_role(staff_role_id(guild.id) or 0) is None:
            return "Роль поддержки не найдена на сервере — выберите её в /setup."
        try:
            channel = await self.ensure_channel(guild)
            await self.ensure_panel(channel)
            await self.ensure_staff_panel(await self.ensure_staff_channel(guild))
        except discord.Forbidden:
            return ("Боту не хватает прав. Выдайте роли бота «Управлять каналами», «Управлять ролями» "
                    "и «Управлять ветками».")
        return None

    async def ensure_channel(self, guild: discord.Guild) -> discord.TextChannel:
        staff = guild.get_role(staff_role_id(guild.id) or 0)

        overwrites = {
            # все видят канал и кнопку, но не пишут в него и не создают ветки сами
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=False,
                add_reactions=False,
                create_public_threads=False,
                create_private_threads=False,
                send_messages_in_threads=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
                create_private_threads=True,
                send_messages_in_threads=True,
                manage_threads=True,
                manage_messages=True,
            ),
        }
        if staff is not None:
            # «Управлять ветками» даёт роли доступ ко всем приватным веткам-тикетам
            overwrites[staff] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages_in_threads=True,
                manage_threads=True,
            )

        channel = ticket_channel(guild)
        if channel is None:
            channel = await guild.create_text_channel(
                TICKET_CHANNEL_NAME,
                overwrites=overwrites,
                topic="Нашли баг или есть идея? Нажмите «Создать тикет».",
                reason="Канал тикетов",
            )
            await storage.set("config", guild.id, {**guild_config(guild.id), "ticket_channel_id": channel.id})
            log.info("Сервер %s: создан канал #%s", guild, channel.name)
        else:
            await channel.edit(overwrites=overwrites, reason="Права канала тикетов")
        return channel

    async def ensure_staff_channel(self, guild: discord.Guild) -> discord.TextChannel:
        staff = guild.get_role(staff_role_id(guild.id))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            staff: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, read_message_history=True
            ),
        }
        channel = staff_channel(guild)
        if channel is None:
            channel = await guild.create_text_channel(
                STAFF_CHANNEL_NAME, overwrites=overwrites, topic="Функционал бота для команды", reason="Канал команды"
            )
            await storage.set("config", guild.id, {**guild_config(guild.id), "staff_channel_id": channel.id})
            log.info("Сервер %s: создан канал #%s", guild, channel.name)
        else:
            await channel.edit(overwrites=overwrites, reason="Права канала команды")
        return channel

    async def ensure_staff_panel(self, channel: discord.TextChannel):
        tickets = ticket_channel(channel.guild)
        tickets = tickets.mention if tickets else "#" + TICKET_CHANNEL_NAME
        embed = discord.Embed(
            title="🛠️ Функционал бота",
            description=(
                "**📢 Создать опрос** — пост в выбранный канал с кнопкой **«💡 Есть идея»**. "
                f"Идеи игроков приходят тикетами в {tickets} со ссылкой на опрос.\n\n"
                "**📰 Опубликовать новость** — пост в выбранный канал. Последние новости игроки "
                "видят по кнопке «Новости» и команде `/news`.\n\n"
                "**🛠️ Ход разработки** — сводка, которую игроки видят по кнопке «Разработка» и `/progress`.\n\n"
                "**📌 Панель сообщества** — сообщение с кнопками для игроков: "
                "Об игре · Новости · Разработка · Идея · Ошибка."
            ),
            color=discord.Color.dark_teal(),
        )
        await upsert_panel(channel, "staff_panels", "staff:poll", embed, StaffPanelView())

    async def ensure_panel(self, channel: discord.TextChannel):
        embed = discord.Embed(
            title="🎫 Тикеты",
            description=(
                "Нашли баг, хотите что-то предложить или нужна помощь?\n"
                "Нажмите **«Создать тикет»**, выберите категорию и опишите проблему.\n\n"
                "Тикет увидите только вы и команда разработки."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Категории",
            value="\n".join(f"{emoji} **{label}** — {desc}" for label, emoji, desc in CATEGORIES.values()),
            inline=False,
        )
        await upsert_panel(channel, "panels", "ticket:create", embed, PanelView())


async def migrate_legacy_config(guild: discord.Guild):
    """Сервер, где бот работал до мастера настройки: переносим старые константы в его настройки."""
    if guild_config(guild.id).get("staff_role_id") or guild.get_role(STAFF_ROLE_ID) is None:
        return

    def known_channel(*ids):
        return next((cid for cid in ids if cid and isinstance(guild.get_channel(cid), discord.TextChannel)), None)

    panels, staff_panels = storage.get("panels", guild.id), storage.get("staff_panels", guild.id)
    await storage.set("config", guild.id, {
        **guild_config(guild.id),
        "staff_role_id": STAFF_ROLE_ID,
        "ticket_channel_id": known_channel(TICKET_CHANNEL_ID, panels and panels["channel_id"]),
        "staff_channel_id": known_channel(staff_panels and staff_panels["channel_id"]),
        "game_name": GAME_NAME,
        "game_info": GAME_INFO,
        "hub_greeting": HUB_GREETING,
    })
    log.info("Сервер %s: старые настройки перенесены в config", guild)


async def upsert_panel(channel: discord.TextChannel, section: str, custom_id: str,
                       embed: discord.Embed, view: ui.View):
    """Обновляет сообщение-панель бота в канале или отправляет новое, если его нет."""
    message = await find_panel(channel, section, custom_id)
    if message is None:
        message = await channel.send(embed=embed, view=view)
    else:
        await message.edit(embed=embed, view=view)
    await storage.set(section, channel.guild.id, {"channel_id": channel.id, "message_id": message.id})


async def find_panel(channel: discord.TextChannel, section: str, custom_id: str) -> discord.Message | None:
    """Сообщение-панель бота с кнопкой custom_id: сначала по запомненному id, потом поиском в истории."""
    panel = storage.get(section, channel.guild.id)
    if panel and panel["channel_id"] == channel.id:
        try:
            return await channel.fetch_message(panel["message_id"])
        except discord.NotFound:
            pass

    async for message in channel.history(limit=100):
        if message.author == channel.guild.me and any(
            getattr(item, "custom_id", None) == custom_id
            for row in message.components
            for item in getattr(row, "children", [])
        ):
            return message
    return None


def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def main():
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Укажите DISCORD_TOKEN в .env или переменных окружения")
    discord.utils.setup_logging()
    KrakenBot().run(token, log_handler=None)


if __name__ == "__main__":
    main()
