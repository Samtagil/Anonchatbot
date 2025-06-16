import asyncio
import re
from datetime import datetime
from typing import List, Optional

import bleach
from aiolimiter import AsyncLimiter
from cachetools import TTLCache
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from achievements import ACHIEVEMENTS
from config import get_config
from database import Database

# Инициализация логирования и лимитеров
import structlog

logger = structlog.get_logger()
command_limiter = AsyncLimiter(10, 60)  # 10 команд в минуту
message_limiter = AsyncLimiter(30, 60)  # 30 сообщений в минуту

# Инициализация кэша с TTL (5 минут)
user_cache = TTLCache(maxsize=1000, ttl=300)
poll_cache = TTLCache(maxsize=100, ttl=300)

def validate_nickname(nick: str) -> bool:
    """Проверяет, соответствует ли ник допустимому формату.

    Args:
        nick: Ник пользователя.

    Returns:
        bool: True, если ник валиден, иначе False.
    """
    pattern = r'^[\w\s-]{1,50}$'
    return bool(re.match(pattern, nick))

async def validate_user(db: Database, user_id: int, chat_id: int) -> Optional[dict]:
    """Проверяет, активен ли пользователь в чате.

    Args:
        db: Экземпляр базы данных.
        user_id: ID пользователя.
        chat_id: ID чата.

    Returns:
        Optional[dict]: Данные пользователя или None, если пользователь неактивен.
    """
    user = await db.get_user(user_id)
    if not user or user['exit_time']:
        logger.warning(
            "User validation failed: user not found or inactive",
            user_id=user_id,
            chat_id=chat_id
        )
        return None
    return user

class UserManagementCommands:
    """Команды для управления пользователями."""

    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Добавляет пользователя в чат.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Start command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "start", user_id)
            user = await db.get_user(user_id)
            if user and not user['exit_time']:
                await update.message.reply_text("❌ Ты уже в чате!")
                return
            nick = update.effective_user.username or f"User{user_id}"
            async with db.get_connection() as conn:
                await conn.execute(
                    '''
                    INSERT OR REPLACE INTO users (
                        user_id, nick, role, join_time, exit_time
                    )
                    VALUES (?, ?, ?, ?, NULL)
                    ''',
                    (user_id, nick, 'user', datetime.utcnow().isoformat())
                )
                await conn.commit()
            user_cache[user_id] = {
                'user_id': user_id,
                'nick': nick,
                'role': 'user',
                'join_time': datetime.utcnow().isoformat(),
                'exit_time': None,
            }
            await db.add_achievement(user_id, 'welcome')
            await update.message.reply_text(
                f"✅ Добро пожаловать, {nick}! Ты в чате."
            )

    @staticmethod
    async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Удаляет пользователя из чата.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Stop command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "stop", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET exit_time = ? WHERE user_id = ?',
                    (datetime.utcnow().isoformat(), user_id)
                )
                await conn.commit()
            user['exit_time'] = datetime.utcnow().isoformat()
            user_cache[user_id] = user
            await update.message.reply_text(
                f"👋 {user['nick']}, ты покинул чат."
            )

    @staticmethod
    async def nick(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Изменяет ник пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Nick command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "nick", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if user['frozen_nick']:
                await update.message.reply_text("❌ Твой ник заморожен!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи новый ник: /nick <новый_ник>"
                )
                return
            new_nick = bleach.clean(' '.join(context.args), tags=[], attributes={})
            if not validate_nickname(new_nick):
                await update.message.reply_text(
                    "❌ Ник должен быть от 1 до 50 символов и содержать только "
                    "буквы, цифры, пробелы или дефисы!"
                )
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET nick = ? WHERE user_id = ?',
                    (new_nick, user_id)
                )
                await conn.commit()
            user['nick'] = new_nick
            user_cache[user_id] = user
            await update.message.reply_text(
                f"✅ Твой ник изменён на {new_nick}!"
            )

class MessagingCommands:
    """Команды для обмена сообщениями."""

    @staticmethod
    async def last(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает последние 10 сообщений в чате.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Last command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "last", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    '''
                    SELECT m.message_id, m.user_id, u.nick, m.content, m.timestamp
                    FROM messages m
                    JOIN users u ON m.user_id = u.user_id
                    WHERE m.is_private = 0
                    ORDER BY m.timestamp DESC
                    LIMIT 10
                    '''
                )
                messages = await cursor.fetchall()
            if not messages:
                await update.message.reply_text("📭 Нет сообщений в чате!")
                return
            lines = [
                f"[{row[4]}] {row[2]}: {row[3]}" for row in messages
            ]
            await update.message.reply_text("\n".join(lines))

    @staticmethod
    async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает список активных пользователей.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "List users command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "list_users", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id, nick FROM users WHERE exit_time IS NULL'
                )
                users = await cursor.fetchall()
            if not users:
                await update.message.reply_text("📭 Нет активных пользователей!")
                return
            lines = [f"#{row[0]}: {row[1]}" for row in users]
            await update.message.reply_text("\n".join(lines))

    @staticmethod
    async def msg(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет личное сообщение пользователю.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Msg command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "msg", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Укажи ID и сообщение: /msg <ID> <текст>"
                )
                return
            try:
                target_user_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ ID должен быть числом!")
                return
            content = bleach.clean(' '.join(context.args[1:]), tags=[], attributes={})
            if len(content) > 1000:
                await update.message.reply_text("❌ Сообщение слишком длинное!")
                return
            target_user = await db.get_user(target_user_id)
            if not target_user or target_user['exit_time']:
                await update.message.reply_text("❌ Пользователь не в чате!")
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    '''
                    INSERT INTO messages (
                        user_id, target_user_id, content, is_private, timestamp
                    )
                    VALUES (?, ?, ?, 1, ?)
                    ''',
                    (
                        user_id,
                        target_user_id,
                        content,
                        datetime.utcnow().isoformat()
                    )
                )
                await conn.commit()
            async with message_limiter:
                try:
                    await context.bot.send_message(
                        target_user_id,
                        f"📩 Личное сообщение от {user['nick']}: {content}"
                    )
                except TelegramError as e:
                    logger.error(
                        "Failed to send private message",
                        target_user_id=target_user_id,
                        error=str(e)
                    )
            await update.message.reply_text(
                f"✅ Сообщение отправлено пользователю #{target_user_id}!"
            )

    @staticmethod
    async def getmsg(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает последние 10 личных сообщений.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Getmsg command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "getmsg", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    '''
                    SELECT m.message_id, u.nick, m.content, m.timestamp
                    FROM messages m
                    JOIN users u ON m.user_id = u.user_id
                    WHERE m.target_user_id = ? AND m.is_private = 1
                    ORDER BY m.timestamp DESC
                    LIMIT 10
                    ''',
                    (user_id,)
                )
                messages = await cursor.fetchall()
            if not messages:
                await update.message.reply_text("📭 Нет личных сообщений!")
                return
            lines = [
                f"[{row[3]}] {row[1]}: {row[2]}" for row in messages
            ]
            await update.message.reply_text("\n".join(lines))

class PollCommands:
    """Команды для управления опросами."""

    @staticmethod
    async def poll(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Создаёт новый опрос.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Poll command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "poll", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Укажи вопрос и варианты: /poll <вопрос> <вариант1> <вариант2> ..."
                )
                return
            question = bleach.clean(context.args[0], tags=[], attributes={})
            options = [
                bleach.clean(opt, tags=[], attributes={})
                for opt in context.args[1:][:10]
            ]
            if len(question) > 255 or any(len(opt) > 100 for opt in options):
                await update.message.reply_text(
                    "❌ Вопрос или варианты слишком длинные!"
                )
                return
            if len(options) < 2:
                await update.message.reply_text(
                    "❌ Нужно минимум 2 варианта ответа!"
                )
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    '''
                    INSERT INTO polls (
                        user_id, question, options, start_time
                    )
                    VALUES (?, ?, ?, ?)
                    ''',
                    (
                        user_id,
                        question,
                        ','.join(options),
                        datetime.utcnow().isoformat()
                    )
                )
                poll_id = cursor.lastrowid
                await conn.commit()
            poll_cache[poll_id] = {
                'user_id': user_id,
                'question': question,
                'options': options,
                'start_time': datetime.utcnow().isoformat(),
            }
            await update.message.reply_text(
                f"🗳 Опрос #{poll_id} создан: {question}\n"
                f"Варианты: {', '.join(options)}"
            )

    @staticmethod
    async def vote(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Голосование в опросе.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Vote command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "vote", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if len(context.args) != 2:
                await update.message.reply_text(
                    "❌ Укажи ID опроса и номер варианта: /vote <poll_id> <option>"
                )
                return
            try:
                poll_id = int(context.args[0])
                option_idx = int(context.args[1]) - 1
            except ValueError:
                await update.message.reply_text(
                    "❌ ID опроса и номер варианта должны быть числами!"
                )
                return
            poll = poll_cache.get(poll_id)
            if not poll:
                async with db.get_connection() as conn:
                    cursor = await conn.execute(
                        'SELECT user_id, question, options, end_time FROM polls '
                        'WHERE id = ?',
                        (poll_id,)
                    )
                    poll_data = await cursor.fetchone()
                if not poll_data or poll_data[3]:
                    await update.message.reply_text("❌ Опрос не найден или завершён!")
                    return
                poll = {
                    'user_id': poll_data[0],
                    'question': poll_data[1],
                    'options': poll_data[2].split(','),
                    'end_time': poll_data[3],
                }
                poll_cache[poll_id] = poll
            if option_idx < 0 or option_idx >= len(poll['options']):
                await update.message.reply_text("❌ Неверный номер варианта!")
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id FROM poll_votes WHERE poll_id = ? AND user_id = ?',
                    (poll_id, user_id)
                )
                if await cursor.fetchone():
                    await update.message.reply_text("❌ Ты уже голосовал в этом опросе!")
                    return
                await conn.execute(
                    '''
                    INSERT INTO poll_votes (poll_id, user_id, option_index)
                    VALUES (?, ?, ?)
                    ''',
                    (poll_id, user_id, option_idx)
                )
                await conn.commit()
            await update.message.reply_text(
                f"✅ Голос учтён за вариант '{poll['options'][option_idx]}' "
                f"в опросе #{poll_id}!"
            )

    @staticmethod
    async def polldown(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Завершает опрос, созданный пользователем.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Polldown command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "polldown", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи ID опроса: /polldown <poll_id>"
                )
                return
            try:
                poll_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ ID опроса должен быть числом!")
                return
            poll = poll_cache.get(poll_id)
            if not poll:
                async with db.get_connection() as conn:
                    cursor = await conn.execute(
                        'SELECT user_id, question, options, end_time FROM polls '
                        'WHERE id = ?',
                        (poll_id,)
                    )
                    poll_data = await cursor.fetchone()
                if not poll_data:
                    await update.message.reply_text("❌ Опрос не найден!")
                    return
                poll = {
                    'user_id': poll_data[0],
                    'question': poll_data[1],
                    'options': poll_data[2].split(','),
                    'end_time': poll_data[3],
                }
                poll_cache[poll_id] = poll
            if poll['end_time']:
                await update.message.reply_text("❌ Опрос уже завершён!")
                return
            if poll['user_id'] != user_id and user['role'] not in ['moderator', 'admin', 'owner']:
                await update.message.reply_text(
                    "❌ Только создатель или модератор может завершить опрос!"
                )
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE polls SET end_time = ? WHERE id = ?',
                    (datetime.utcnow().isoformat(), poll_id)
                )
                cursor = await conn.execute(
                    '''
                    SELECT option_index, COUNT(*) as votes
                    FROM poll_votes
                    WHERE poll_id = ?
                    GROUP BY option_index
                    ''',
                    (poll_id,)
                )
                votes = await cursor.fetchall()
                await conn.commit()
            results = [0] * len(poll['options'])
            for vote in votes:
                results[vote[0]] = vote[1]
            lines = [
                f"🗳 Результаты опроса #{poll_id}: {poll['question']}",
                *[f"{i+1}. {opt}: {votes} голосов" for i, (opt, votes) in enumerate(zip(poll['options'], results))],
            ]
            await update.message.reply_text("\n".join(lines))
            if poll_id in poll_cache:
                del poll_cache[poll_id]

    @staticmethod
    async def handle_poll_vote(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Обрабатывает голосование в Telegram-опросе.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        user_id = update.poll_answer.user.id
        chat_id = update.effective_chat.id if update.effective_chat else 0
        poll_id = update.poll_answer.poll_id
        option_idx = update.poll_answer.option_ids[0] if update.poll_answer.option_ids else None
        logger.info(
            "Poll vote received",
            user_id=user_id,
            chat_id=chat_id,
            poll_id=poll_id
        )
        await db.log_action(user_id, "poll_vote", user_id)
        if option_idx is None:
            return
        async with db.get_connection() as conn:
            cursor = await conn.execute(
                'SELECT user_id FROM poll_votes WHERE poll_id = ? AND user_id = ?',
                (poll_id, user_id)
            )
            if await cursor.fetchone():
                return
            await conn.execute(
                '''
                INSERT INTO poll_votes (poll_id, user_id, option_index)
                VALUES (?, ?, ?)
                ''',
                (poll_id, user_id, option_idx)
            )
            await conn.commit()

class InteractionCommands:
    """Команды для взаимодействия пользователей."""

    @staticmethod
    async def hug(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет обнимашку пользователю.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Hug command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "hug", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи ID пользователя: /hug <ID>"
                )
                return
            try:
                target_user_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ ID должен быть числом!")
                return
            target_user = await db.get_user(target_user_id)
            if not target_user or target_user['exit_time']:
                await update.message.reply_text("❌ Пользователь не в чате!")
                return
            await update.message.reply_text(
                f"🤗 {user['nick']} обнял {target_user['nick']}!"
            )

    @staticmethod
    async def slap(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет шлепок пользователю.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Slap command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "slap", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи ID пользователя: /slap <ID>"
                )
                return
            try:
                target_user_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ ID должен быть числом!")
                return
            target_user = await db.get_user(target_user_id)
            if not target_user or target_user['exit_time']:
                await update.message.reply_text("❌ Пользователь не в чате!")
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT slap_text FROM settings WHERE user_id = ?',
                    (user_id,)
                )
                slap_text = (await cursor.fetchone())[0] if await cursor.fetchone() else "шлёп!"
            await update.message.reply_text(
                f"👋 {user['nick']} {slap_text} {target_user['nick']}!"
            )

class InfoCommands:
    """Команды для получения информации."""

    @staticmethod
    async def info(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает информацию о пользователе.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Info command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "info", user_id)
            target_user_id = user_id
            if context.args:
                try:
                    target_user_id = int(context.args[0])
                except ValueError:
                    await update.message.reply_text("❌ ID должен быть числом!")
                    return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            achievements = (
                target_user['achievements'].split(',')
                if target_user['achievements']
                else []
            )
            ach_lines = [
                f"- {ACHIEVEMENTS[ach_id]['title']}: {ACHIEVEMENTS[ach_id]['desc']}"
                for ach_id in achievements if ach_id in ACHIEVEMENTS
            ]
            lines = [
                f"ℹ️ Информация о {target_user['nick']}:",
                f"ID: {target_user['user_id']}",
                f"Роль: {target_user['role']}",
                f"Дата входа: {target_user['join_time']}",
                f"Достижения ({len(achievements)}):",
                *ach_lines or ["- Нет достижений"],
            ]
            await update.message.reply_text("\n".join(lines))

    @staticmethod
    async def search(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Ищет сообщения по ключевому слову.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Search command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "search", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи ключевое слово: /search <слово>"
                )
                return
            keyword = bleach.clean(' '.join(context.args), tags=[], attributes={})
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    '''
                    SELECT m.message_id, u.nick, m.content, m.timestamp
                    FROM messages m
                    JOIN users u ON m.user_id = u.user_id
                    WHERE m.content LIKE ? AND m.is_private = 0
                    ORDER BY m.timestamp DESC
                    LIMIT 5
                    ''',
                    (f'%{keyword}%',)
                )
                messages = await cursor.fetchall()
            if not messages:
                await update.message.reply_text("📭 Сообщения не найдены!")
                return
            lines = [
                f"[{row[3]}] {row[1]}: {row[2]}" for row in messages
            ]
            await update.message.reply_text("\n".join(lines))

    @staticmethod
    async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Проверяет доступность бота.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Ping command initiated",
                user_id=user_id,
                "chat_id=chat_id
            )

            await db.log_action(user_id, "ping", user_id)
            await update.message.reply_text("Pong! 🏓")

    @staticmethod
    async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает правила чата.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Rules command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "rules", user_id)
            config = get_config()
            await update.message.reply_text(config['rules_text'])

    @staticmethod
    async def about(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает информацию о боте.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "About command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "about", user_id)
            config = get_config()
            await update.message.reply_text(config['about_text'])

class SpecialMessageCommands:
    """Команды для специальных типов сообщений."""

    @staticmethod
    async def third_person(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет сообщение от третьего лица.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Third person command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "third_person", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if user['text_only']:
                await update.message.reply_text("❌ У тебя текстовый режим!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи действие: /me <действие>"
                )
                return
            content = bleach.clean(' '.join(context.args), tags=[]), attributes={}
            if len(content) > 500:
                await update.message.reply_text("❌ Сообщение слишком длинное!")
                return
            await update.message.reply_text(f"🌟 {user['nick']} {content}")

    @staticmethod
    async def hidden_message(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет скрытое сообщение.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Hidden message command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "hidden_message", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if user['text_only']:
                await update.message.reply_text("❌ У тебя текстовый режим!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи текст: /hide <текст>"
                )
                return
            content = bleach.clean(' '.join(context.args), tags=['spoiler'], attributes={})
            if len(content) > 500:
                await update.message.reply_text("❌ Сообщение слишком длинное!")
                return
            await update.message.reply_text(f"🙈 ||{content}||", parse_mode="MarkdownV2")

    @staticmethod
    async def protected_message(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет защищённое сообщение.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Protected message command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "protected_message", user_id)
            user = await validate_user(db, user_id, chat_id)
            if not user:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if user['text_only']:
                await update.message.reply_text("❌ У тебя текстовый режим!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи текст: /protect <текст>"
                )
                return
            content = bleach.clean(' '.join(context.args), tags=['code'], attributes={})
            if len(content) > 500:
                await update.message.reply_text("❌ Сообщение слишком длинное!")
                return
            await update.message.reply_text(f"```text\n{content}\n```", parse_mode="MarkdownV2")

def register_handlers(db: Database) -> List[Handler]:
    """Регистрирует все обработчики пользовательских команд.

    Args:
        db: Экземпляр базы данных.

    Returns:
        List[Handler]: Список обработчиков команд и сообщений.
    """
    handlers = [
        CommandHandler("start", lambda u, c: UserManagementCommands.start(u, c, db)),
        CommandHandler("stop", lambda u, c: UserManagementCommands.stop(u, c, db)),
        CommandHandler("nick", lambda u, c: UserManagementCommands.nick(u, c, db)),
        CommandHandler("last", lambda u, c: MessagingCommands.last(u, c, db)),
        CommandHandler("list", lambda u, c: MessagingCommands.list_users(u, c, db)),
        CommandHandler("msg", lambda u, c: MessagingCommands.msg(u, c, db)),
        CommandHandler("getmsg", lambda u, c: MessagingCommands.getmsg(u, c, db)),
        CommandHandler("poll", lambda u, c: PollCommands.poll(u, c, db)),
        CommandHandler("vote", lambda u, c: PollCommands.vote(u, c, db)),
        CommandHandler("polldown", lambda u, c: PollCommands.polldown(u, c, db)),
        CommandHandler("hug", lambda u, c: InteractionCommands.hug(u, c, db)),
        CommandHandler("slap", lambda u, c: InteractionCommands.slap(u, c, db)),
        CommandHandler("info", lambda u, c: InfoCommands.info(u, c, db)),
        CommandHandler("search", lambda u, c: InfoCommands.search(u, c, db)),
        CommandHandler("ping", lambda u, c: InfoCommands.ping(u, c, db)),
        CommandHandler("rules", lambda u, c: InfoCommands.rules(u, c, db)),
        CommandHandler("about", lambda u, c: InfoCommands.about(u, c, db)),
        CommandHandler("me", lambda u, c: SpecialMessageCommands.third_person(u, c, db)),
        CommandHandler("hide", lambda u, c: SpecialMessageCommands.hidden_message(u, c, db)),
        CommandHandler(
            "protect", lambda u, c: SpecialMessageCommands.protected_message(u, c, db)
        ),
        PollAnswerHandler(
            lambda u, c: PollCommands.handle_poll_vote(u, c, db)
        ),
    ]
    return handlers