"""KrakenBot — бот тикетов для Discord-сервера разработчика игры."""

import asyncio
import json
import logging
import os
from pathlib import Path

import discord
from discord import ui

# ---------------------------------------------------------------- настройки

STAFF_ROLE_ID = 898463898582253568
TICKET_CHANNEL_NAME = "тикеты"  # Discord хранит имена текстовых каналов в нижнем регистре
STAFF_CHANNEL_NAME = "функционал-бота"  # видит только STAFF_ROLE_ID
DATA_FILE = Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / "data.json"

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
        self.data = {"counters": {}, "panels": {}, "staff_panels": {}, "tickets": {}, "polls": {}}
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    async def next_ticket_number(self, guild_id: int) -> int:
        async with self.lock:
            number = self.data["counters"].get(str(guild_id), 0) + 1
            self.data["counters"][str(guild_id)] = number
            self._save()
            return number

    async def set(self, section: str, key: int, value):
        async with self.lock:
            self.data[section][str(key)] = value
            self._save()

    def get(self, section: str, key: int):
        return self.data[section].get(str(key))


storage = Storage(DATA_FILE)


def is_staff(member: discord.abc.User) -> bool:
    return isinstance(member, discord.Member) and member.get_role(STAFF_ROLE_ID) is not None


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
        content=f"{interaction.user.mention} создал тикет. <@&{STAFF_ROLE_ID}>",
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
    panel = storage.get("panels", guild.id)
    channel = guild.get_channel(panel["channel_id"]) if panel else None
    return channel or discord.utils.get(guild.text_channels, name=TICKET_CHANNEL_NAME)


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
            f"Менять статус тикета может только <@&{STAFF_ROLE_ID}>.", ephemeral=True
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


class PollModal(ui.Modal, title="Новый опрос"):
    target = ui.Label(
        text="Канал",
        description="Куда отправить опрос",
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

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(self.target.component.values[0].id)
        if channel is None:
            await interaction.response.send_message("Канал не найден.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        title = self.poll_title.component.value
        embed = discord.Embed(
            title=f"📢 {title}",
            description=self.text.component.value,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text="Есть мысли? Нажмите «Есть идея» — ответ увидит только команда")

        files = [await a.to_file() for a in self.images.component.values]
        image = next((f for f in files if f.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))), None)
        if image is not None:
            embed.set_image(url=f"attachment://{image.filename}")

        mention_key = self.mention.component.values[0]
        try:
            message = await channel.send(
                content=MENTIONS[mention_key] if mention_key != "none" else None,
                embed=embed,
                files=files,
                view=PollView(),
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except discord.Forbidden:
            await interaction.followup.send(f"У бота нет прав писать в {channel.mention}.", ephemeral=True)
            return

        await storage.set("polls", message.id, {
            "title": title,
            "channel_id": channel.id,
            "author_id": interaction.user.id,
            "ideas": 0,
        })
        await interaction.followup.send(f"Опрос опубликован: {message.jump_url}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ошибка при создании опроса", exc_info=error)
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send("Не удалось опубликовать опрос, попробуйте ещё раз.", ephemeral=True)


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
        await interaction.response.send_message(f"Доступно только <@&{STAFF_ROLE_ID}>.", ephemeral=True)
        return False

    @ui.button(label="Создать опрос", emoji="📢", style=discord.ButtonStyle.primary, custom_id="staff:poll")
    async def poll(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PollModal())


# ---------------------------------------------------------------- бот


class KrakenBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.ready_once = False

    async def setup_hook(self):
        # кнопки продолжают работать после перезапуска бота
        self.add_view(PanelView())
        self.add_view(StatusView())
        self.add_view(PollView())
        self.add_view(StaffPanelView())

    async def on_ready(self):
        log.info("Вошёл как %s", self.user)
        if self.ready_once:
            return
        self.ready_once = True
        for guild in self.guilds:
            await self.setup_guild(guild)

    async def on_guild_join(self, guild: discord.Guild):
        await self.setup_guild(guild)

    async def setup_guild(self, guild: discord.Guild):
        try:
            channel = await self.ensure_channel(guild)
            await self.ensure_panel(channel)
            staff_channel = await self.ensure_staff_channel(guild)
            if staff_channel is not None:
                await self.ensure_staff_panel(staff_channel)
        except discord.Forbidden:
            log.error("Сервер %s: боту не хватает прав (нужны «Управлять каналами» и «Управлять ролями»)", guild)

    async def ensure_channel(self, guild: discord.Guild) -> discord.TextChannel:
        staff = guild.get_role(STAFF_ROLE_ID)
        if staff is None:
            log.warning("Сервер %s: роль %s не найдена", guild, STAFF_ROLE_ID)

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

        channel = discord.utils.get(guild.text_channels, name=TICKET_CHANNEL_NAME)
        if channel is None:
            channel = await guild.create_text_channel(
                TICKET_CHANNEL_NAME,
                overwrites=overwrites,
                topic="Нашли баг или есть идея? Нажмите «Создать тикет».",
                reason="Канал тикетов",
            )
            log.info("Сервер %s: создан канал #%s", guild, channel.name)
        else:
            await channel.edit(overwrites=overwrites, reason="Права канала тикетов")
        return channel

    async def ensure_staff_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        staff = guild.get_role(STAFF_ROLE_ID)
        if staff is None:
            return None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            staff: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, read_message_history=True
            ),
        }
        channel = discord.utils.get(guild.text_channels, name=STAFF_CHANNEL_NAME)
        if channel is None:
            channel = await guild.create_text_channel(
                STAFF_CHANNEL_NAME, overwrites=overwrites, topic="Функционал бота для команды", reason="Канал команды"
            )
            log.info("Сервер %s: создан канал #%s", guild, channel.name)
        else:
            await channel.edit(overwrites=overwrites, reason="Права канала команды")
        return channel

    async def ensure_staff_panel(self, channel: discord.TextChannel):
        if await panel_exists(channel, "staff_panels"):
            return
        tickets = ticket_channel(channel.guild)
        embed = discord.Embed(
            title="🛠️ Функционал бота",
            description=(
                "**📢 Создать опрос** — публикует пост в выбранный канал с кнопкой **«💡 Есть идея»**.\n"
                f"Игрок нажимает кнопку, пишет идею — и она приходит тикетом в {tickets.mention if tickets else '#' + TICKET_CHANNEL_NAME} "
                "со ссылкой на опрос. Статусы у таких тикетов те же, что и у обычных."
            ),
            color=discord.Color.dark_teal(),
        )
        message = await channel.send(embed=embed, view=StaffPanelView())
        await storage.set("staff_panels", channel.guild.id, {"channel_id": channel.id, "message_id": message.id})

    async def ensure_panel(self, channel: discord.TextChannel):
        if await panel_exists(channel, "panels"):
            return

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
        message = await channel.send(embed=embed, view=PanelView())
        await storage.set("panels", channel.guild.id, {"channel_id": channel.id, "message_id": message.id})


async def panel_exists(channel: discord.TextChannel, section: str) -> bool:
    panel = storage.get(section, channel.guild.id)
    if not panel or panel["channel_id"] != channel.id:
        return False
    try:
        await channel.fetch_message(panel["message_id"])
        return True
    except discord.NotFound:
        return False


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
