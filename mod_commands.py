import asyncio
import re
from datetime import datetime, timedelta
from typing import List, Optional

import bleach
from aiolimiter import AsyncLimiter
from cachetools import TTLCache
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

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

def validate_nickname(nick: str) -> bool:
    """Проверяет, соответствует ли ник допустимому формату.

    Args:
        nick: Ник пользователя.

    Returns:
        bool: True, если ник валиден, иначе False.
    """
    pattern = r'^[\w\s-]{1,50}$'
    return bool(re.match(pattern, nick))

async def check_moderator(db: Database, user_id: int, chat_id: int) -> bool:
    """Проверяет, является ли пользователь модератором или выше.

    Args:
        db: Экземпляр базы данных.
        user_id: ID пользователя.
        chat_id: ID чата.

    Returns:
        bool: True, если пользователь модератор или выше, иначе False.
    """
    user = await db.get_user(user_id)
    if not user:
        logger.warning(
            "Moderator check failed: user not found",
            user_id=user_id,
            chat_id=chat_id
        )
        return False
    is_moderator = user['role'] in ['moderator', 'admin', 'owner']
    logger.debug(
        "Moderator check",
        user_id=user_id,
        chat_id=chat_id,
        result=is_moderator
    )
    return is_moderator

async def get_target_user_id(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    """Получает ID целевого пользователя из аргументов команды.

    Args:
        update: Объект обновления.
        context: Контекст команды.

    Returns:
        Optional[int]: ID пользователя или None, если ID некорректен.
    """
    if not context.args:
        await update.message.reply_text("❌ Укажи ID пользователя!")
        return None
    try:
        return int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом!")
        return None

class NotificationCommands:
    """Команды для отправки уведомлений."""

    @staticmethod
    async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет уведомление всем активным пользователям.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Notify command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "notify", user_id)
            user = await db.get_user(user_id)
            if not user or user['exit_time']:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи текст уведомления: /notify <текст>"
                )
                return
            text = bleach.clean(' '.join(context.args), tags=[], attributes={})
            if len(text) > 1000:
                await update.message.reply_text("❌ Текст слишком длинный!")
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id FROM users WHERE exit_time IS NULL'
                )
                users = await cursor.fetchall()
                for (recipient_id,) in users:
                    async with message_limiter:
                        try:
                            await context.bot.send_message(
                                recipient_id, f"📢 Уведомление: {text}"
                            )
                        except TelegramError as e:
                            logger.error(
                                "Failed to notify user",
                                user_id=recipient_id,
                                error=str(e)
                            )
                await db.log_action(
                    0, "notify", user_id, details=f"Text: {text[:50]}..."
                )
            await update.message.reply_text(
                "✅ Уведомление отправлено всем пользователям!"
            )

class UserModerationCommands:
    """Команды для модерации пользователей."""

    @staticmethod
    async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Мутит пользователя на указанное время.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Mute command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "mute", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if target_user['role'] in ['moderator', 'admin', 'owner']:
                await update.message.reply_text(
                    "❌ Нельзя мутить модераторов или выше!"
                )
                return
            duration = 60
            if len(context.args) > 1:
                try:
                    duration = int(context.args[1])
                    if duration <= 0 or duration > 1440:
                        raise ValueError
                except ValueError:
                    await update.message.reply_text(
                        "❌ Длительность должна быть числом от 1 до 1440 минут!"
                    )
                    return
            mute_end_time = datetime.utcnow() + timedelta(minutes=duration)
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET mute_time = ? WHERE user_id = ?',
                    (mute_end_time.isoformat(), target_user_id)
                )
                await conn.commit()
            target_user['mute_time'] = mute_end_time.isoformat()
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "mute",
                user_id,
                details=f"Duration: {duration} minutes"
            )
            await update.message.reply_text(
                f"🔶 {target_user['nick']} замучен на {duration} минут!"
            )

    @staticmethod
    async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Банит пользователя на указанное время.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Ban command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "ban", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if target_user['role'] in ['moderator', 'admin', 'owner']:
                await update.message.reply_text(
                    "❌ Нельзя банить модераторов или выше!"
                )
                return
            duration = 1440
            if len(context.args) > 1:
                try:
                    duration = int(context.args[1])
                    if duration <= 0 or duration > 10080:
                        raise ValueError
                except ValueError:
                    await update.message.reply_text(
                        "❌ Длительность должна быть числом от 1 до 10080 минут!"
                    )
                    return
            ban_end_time = datetime.utcnow() + timedelta(minutes=duration)
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET banned = 1, ban_time = ?, exit_time = ? '
                    'WHERE user_id = ?',
                    (
                        ban_end_time.isoformat(),
                        datetime.utcnow().isoformat(),
                        target_user_id
                    )
                )
                await conn.commit()
            target_user['banned'] = 1
            target_user['ban_time'] = ban_end_time.isoformat()
            target_user['exit_time'] = datetime.utcnow().isoformat()
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "ban",
                user_id,
                details=f"Duration: {duration} minutes"
            )
            await update.message.reply_text(
                f"🔴 {target_user['nick']} забанен на {duration} минут!"
            )

    @staticmethod
    async def rename(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Переименовывает пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Rename command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "rename", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Укажи новый ник: /rename <ID> <новый_ник>"
                )
                return
            new_nick = bleach.clean(' '.join(context.args[1:]), tags=[], attributes={})
            if not validate_nickname(new_nick):
                await update.message.reply_text(
                    "❌ Ник должен быть от 1 до 50 символов и содержать только "
                    "буквы, цифры, пробелы или дефисы!"
                )
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET nick = ? WHERE user_id = ?',
                    (new_nick, target_user_id)
                )
                await conn.commit()
            target_user['nick'] = new_nick
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "rename",
                user_id,
                details=f"New nick: {new_nick}"
            )
            await update.message.reply_text(
                f"✅ Ник пользователя #{target_user_id} изменён на {new_nick}"
            )

    @staticmethod
    async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
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
                "Kick command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "kick", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if target_user['role'] in ['moderator', 'admin', 'owner']:
                await update.message.reply_text(
                    "❌ Нельзя кикать модераторов или выше!"
                )
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET exit_time = ? WHERE user_id = ?',
                    (datetime.utcnow().isoformat(), target_user_id)
                )
                await conn.commit()
            target_user['exit_time'] = datetime.utcnow().isoformat()
            user_cache[target_user_id] = target_user
            await db.log_action(target_user_id, "kick", user_id)
            await update.message.reply_text(
                f"🚪 {target_user['nick']} кикнут из чата!"
            )

    @staticmethod
    async def resident(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Назначает пользователю статус резидента.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Resident command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "resident", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if target_user['role'] != 'user':
                await update.message.reply_text("❌ Пользователь уже не новичок!")
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET role = ? WHERE user_id = ?',
                    ('resident', target_user_id)
                )
                await conn.commit()
            target_user['role'] = 'resident'
            user_cache[target_user_id] = target_user
            await db.log_action(target_user_id, "resident", user_id)
            await update.message.reply_text(
                f"🏡 {target_user['nick']} теперь резидент!"
            )

    @staticmethod
    async def freeze(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Замораживает или размораживает ник пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Freeze command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "freeze", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            new_state = 0 if target_user['frozen_nick'] else 1
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET frozen_nick = ? WHERE user_id = ?',
                    (new_state, target_user_id)
                )
                await conn.commit()
            target_user['frozen_nick'] = new_state
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "freeze",
                user_id,
                details=f"State: {'frozen' if new_state else 'unfrozen'}"
            )
            await update.message.reply_text(
                f"🧊 Ник пользователя #{target_user_id} "
                f"{'заморожен' if new_state else 'разморозен'}!"
            )

    @staticmethod
    async def textual(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Ограничивает пользователя текстовым режимом.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Textual command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "textual", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            new_state = 0 if target_user['text_only'] else 1
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET text_only = ? WHERE user_id = ?',
                    (new_state, target_user_id)
                )
                await conn.commit()
            target_user['text_only'] = new_state
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "textual",
                user_id,
                details=f"State: {'text_only' if new_state else 'normal'}"
            )
            await update.message.reply_text(
                f"📝 Пользователь #{target_user_id} "
                f"{'ограничен текстом' if new_state else 'освобождён от ограничения'}!"
            )

    @staticmethod
    async def rm(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Удаляет данные пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Rm command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "rm", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if target_user['role'] in ['moderator', 'admin', 'owner']:
                await update.message.reply_text(
                    "❌ Нельзя удалять модераторов или выше!"
                )
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'DELETE FROM users WHERE user_id = ?', (target_user_id,)
                )
                await conn.commit()
            if target_user_id in user_cache:
                del user_cache[target_user_id]
            await db.log_action(target_user_id, "rm", user_id)
            await update.message.reply_text(
                f"🗑 Данные пользователя #{target_user_id} удалены!"
            )

    @staticmethod
    async def harakiri(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Позволяет модератору уйти в отставку.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Harakiri command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "harakiri", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            user = await db.get_user(user_id)
            if user['role'] == 'owner':
                await update.message.reply_text(
                    "❌ Владелец не может уйти в отставку!"
                )
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET role = ? WHERE user_id = ?',
                    ('resident', user_id)
                )
                await conn.commit()
            user['role'] = 'resident'
            user_cache[user_id] = user
            await update.message.reply_text(
                f"🙇 {user['nick']} ушёл в отставку с поста модератора!"
            )

    @staticmethod
    async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Разбанивает пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Unban command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "unban", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if not target_user['banned']:
                await update.message.reply_text("❌ Пользователь не забанен!")
                return
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET banned = 0, ban_time = NULL, '
                    'exit_time = NULL WHERE user_id = ?',
                    (target_user_id,)
                )
                await conn.commit()
            target_user['banned'] = 0
            target_user['ban_time'] = None
            target_user['exit_time'] = None
            user_cache[target_user_id] = target_user
            await db.log_action(target_user_id, "unban", user_id)
            await update.message.reply_text(
                f"🟢 {target_user['nick']} разбанен!"
            )

    @staticmethod
    async def say(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Отправляет сообщение от имени бота.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Say command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "say", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            if not context.args:
                await update.message.reply_text("❌ Укажи текст: /say <текст>")
                return
            text = bleach.clean(' '.join(context.args), tags=[], attributes={})
            if len(text) > 1000:
                await update.message.reply_text("❌ Текст слишком длинный!")
                return
            await update.message.reply_text(text)
            await db.log_action(0, "say", user_id, details=f"Text: {text[:50]}...")

    @staticmethod
    async def vote_mute(
        update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database
    ):
        """Голосование за мут пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            config = get_config()
            logger.info(
                "Vote mute command initiated",
                user_id"=user_id,
                "chat_id"=chat_id
            )
            await db.log_action(user_id, "vote_mute", user_id)
            user = await db.get_user(user_id)
            if not user or user['exit_time']:
                await update.message.reply_text("❌ Ты не в чате!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if target_user['role'] in ['moderator', 'admin', 'owner']:
                await update.message.reply_text(
                    "❌ Нельзя голосовать за мут модераторов или выше!"
                )
                return
            if user_id == target_user_id:
                await update.message.reply_text("❌ Нельзя голосовать за свой мут!")
                return
            await db.add_mute_vote(target_user_id, user_id)
            vote_count = await db.count_mute_votes(
                target_user_id, config['mute_vote_window']
            )
            await update.message.reply_text(
                f"🗳 Голос за мут {target_user['nick']} учтён! "
                f"Текущие голоса: {vote_count}/{config['mute_vote_threshold']}"
            )
            if vote_count >= config['mute_vote_threshold']:
                mute_end_time = datetime.utcnow() + timedelta(
                    minutes=config['mute_duration']
                )
                async with db.get_connection() as conn:
                    await conn.execute('
                    '''
                        UPDATE users SET mute_time = ? WHERE user_id = ?
                    ',
                    (mute_end_time.iso(), target_user_id)
                )

                await conn.commit()
                target_user['mute_time'] = mute_end_time.iso()
                user_cache[target_user_id] = target_user
                await db.log_action(
                    target_user_id,
                    "mute",
                    0,
                    details=f"Vote mute: {config['mute_duration']} minutes"
                )
                await update.message.reply_text(
                    f"🔶 {target_user['nick']} "
                    f"замучен на {config['mute_duration']} минут по голосованию!"
                )

class AchievementCommands:
    """Команды для управления достижениями пользователей."""

    @staticmethod
    async def addach(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Добавляет достижение пользователю.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Addach command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "addach", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Укажи ID достижения: /addach <ID> <achievement_id>"
                )
                return
            ach_id = context.args[1]
            if ach_id not in ACHIEVEMENTS:
                await update.message.reply_text("❌ Неверный ID достижения!")
                return
            achievements = (
                target_user['achievements'].split(',')
                if target_user['achievements']
                else []
            )
            if ach_id in achievements:
                await update.message.reply_text(
                    "❌ У пользователя уже есть это достижение!"
                )
                return
            achievements.append(ach_id)
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET achievements = ? WHERE user_id = ?',
                    (','.join(achievements), target_user_id)
                )
                await conn.commit()
            target_user['achievements'] = ','.join(achievements)
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "addach",
                user_id,
                details=f"Achievement: {ach_id}"
            )
            await update.message.reply_text(
                f"🏆 Достижение '{ACHIEVEMENTS[ach_id]['title']}' "
                f"добавлено пользователю #{target_user_id}"
            )

    @staticmethod
    async def rmach(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Удаляет достижение у пользователя.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Rmach command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "rmach", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            target_user_id = await get_target_user_id(update, context)
            if not target_user_id:
                return
            target_user = await db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            if len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Укажи ID достижения: /rmach <ID> <achievement_id>"
                )
                return
            ach_id = context.args[1]
            if ach_id not in ACHIEVEMENTS:
                await update.message.reply_text("❌ Неверный ID достижения!")
                return
            achievements = (
                target_user['achievements'].split(',')
                if target_user['achievements']
                else []
            )
            if ach_id not in achievements:
                await update.message.reply_text(
                    "❌ У пользователя нет этого достижения!"
                )
                return
            achievements.remove(ach_id)
            async with db.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET achievements = ? WHERE user_id = ?',
                    (
                        ','.join(achievements) if achievements else None,
                        target_user_id
                    )
                )
                await conn.commit()
            target_user['achievements'] = (
                ','.join(achievements) if achievements else None
            )
            user_cache[target_user_id] = target_user
            await db.log_action(
                target_user_id,
                "rmach",
                user_id,
                details=f"Achievement: {ach_id}"
            )
            await update.message.reply_text(
                f"🗑 Достижение '{ACHIEVEMENTS[ach_id]['title']}' "
                f"удалено у пользователя #{target_user_id}"
            )

class PollCommands:
    """Команды для управления опросами."""

    @staticmethod
    async def poll_kill(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Удаляет опрос.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Poll kill command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "poll_kill", user_id)
            if not await check_moderator(db, user_id, chat_id):
                await update.message.reply_text("❌ Только для модераторов!")
                return
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи ID опроса: /poll_kill <poll_id>"
                )
                return
            try:
                poll_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text(
                    "❌ ID опроса должен быть числом!"
                )
                return
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT id FROM polls WHERE id = ?', (poll_id,)
                )
                poll = await cursor.fetchone()
                if not poll:
                    await update.message.reply_text("❌ Опрос не найден!")
                    return
                await conn.execute(
                    'UPDATE polls SET end_time = ? WHERE id = ?',
                    (datetime.utcnow().isoformat(), poll_id)
                )
                await conn.commit()
            await update.message.reply_text(f"🗑️ Опрос #{poll_id} удалён!")

class StatsCommands:
    """Команды для отображения статистики чата."""

    @staticmethod
    async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database):
        """Показывает статистику чата.

        Args:
            update: Объект обновления.
            context: Контекст команды.
            db: Экземпляр базы данных.
        """
        async with command_limiter:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            logger.info(
                "Stats command initiated",
                user_id=user_id,
                chat_id=chat_id
            )
            await db.log_action(user_id, "stats", user_id)
            stats = await db.get_chat_stats()
            lines = [
                "📈 Статистика чата:",
                f"Активные пользователи: {stats['active_users']}",
                f"Забаненные пользователи: {stats['banned_users']}",
                f"Всего сообщений: {stats['total_messages']}",
                f"Личных сообщений: {stats['total_pms']}",
                f"Активных опросов: {stats['active_polls']}",
            ]
            await update.message.reply_text("\n".join(lines))

def register_handlers(db: Database) -> List[CommandHandler]:
    """Регистрирует все обработчики модераторских команд.

    Args:
        db: Экземпляр базы данных.

    Returns:
        List[CommandHandler]: Список обработчиков команд.
    """
    handlers = [
        CommandHandler("notify", lambda u, c: NotificationCommands.notify(u, c, db)),
        CommandHandler("poll_kill", lambda u, c: PollCommands.poll_kill(u, c, db)),
        CommandHandler("mute", lambda u, c: UserModerationCommands.mute(u, c, db)),
        CommandHandler("ban", lambda u, c: UserModerationCommands.ban(u, c, db)),
        CommandHandler("rename", lambda u, c: UserModerationCommands.rename(u, c, db)),
        CommandHandler("addach", lambda u, c: AchievementCommands.addach(u, c, db)),
        CommandHandler("rmach", lambda u, c: AchievementCommands.rmach(u, c, db)),
        CommandHandler("kick", lambda u, c: UserModerationCommands.kick(u, c, db)),
        CommandHandler("resident", lambda u, c: UserModerationCommands.resident(u, c, db)),
        CommandHandler("freeze", lambda u, c: UserModerationCommands.freeze(u, c, db)),
        CommandHandler("textual", lambda u, c: UserModerationCommands.textual(u, c, db)),
        CommandHandler("rm", lambda u, c: UserModerationCommands.rm(u, c, db)),
        CommandHandler(
            "harakiri", lambda u, c: UserModerationCommands.harakiri(u, c, db)
        ),
        CommandHandler("unban", lambda u, c: UserModerationCommands.unban(u, c, db)),
        CommandHandler("say", lambda u, c: UserModerationCommands.say(u, c, db)),
        CommandHandler(
            "vote_mute", lambda u, c: UserModerationCommands.vote_mute(u, c, db)
        ),
        CommandHandler("stats", lambda u, c: StatsCommands.stats(u, c, db)),
    ]
    return handlers