"""Модуль для обработки административных команд чат-бота.

Содержит команды для управления пользователями, ролями, настройками чата
и просмотра логов. Доступны только пользователям с ролью 'admin'.
"""

import asyncio
from typing import List

import bleach
import structlog
from aiolimiter import AsyncLimiter
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Handler,
)

from config import get_config
from database import Database

logger = structlog.get_logger()
limiter = AsyncLimiter(1, 1)  # 1 команда в секунду на пользователя

async def check_admin(update: Update, db: Database) -> bool:
    """Проверяет, является ли пользователь администратором.

    Args:
        update: Объект обновления Telegram.
        db: Экземпляр базы данных.

    Returns:
        bool: True, если пользователь администратор, иначе False.
    """
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    if not user or user['role'] != 'admin':
        await update.message.reply_text("🚫 У вас нет прав администратора.")
        logger.warning(
            "Admin command access denied",
            user_id=user_id,
            chat_id=update.effective_chat.id
        )
        return False
    return True

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Банит пользователя по ID или имени.

    Использование: /ban <user_id или @username> <причина>
    Пример: /ban @username Спам
    """
    async with limiter:
        db = context.bot_data['db']
        if not await check_admin(update, db):
            return

        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Укажите ID/@username и причину. Пример: /ban @username Спам"
            )
            return

        target = bleach.clean(args[0])
        reason = bleach.clean(' '.join(args[1:]))
        user_id = None

        if target.startswith('@'):
            # Поиск по имени (упрощённо, предполагается, что ник уникален)
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id FROM users WHERE nick = ?',
                    (target[1:],)
                )
                result = await cursor.fetchone()
                user_id = result['user_id'] if result else None
        else:
            try:
                user_id = int(target)
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID или имени.")
                return

        if not user_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return

        user = await db.get_user(user_id)
        if not user:
            await update.message.reply_text("❌ Пользователь не найден в базе.")
            return

        if user['role'] == 'admin':
            await update.message.reply_text("🚫 Нельзя забанить администратора.")
            return

        async with db.get_connection() as conn:
            await conn.execute(
                '''
                UPDATE users
                SET banned = 1, ban_time = ?
                WHERE user_id = ?
                ''',
                (asyncio.get_event_loop().time(), user_id)
            )
            await conn.commit()

        await db.log_action(
            user_id=update.effective_user.id,
            action='ban',
            target_id=user_id,
            details=f"Reason: {reason}"
        )
        await update.message.reply_text(
            f"✅ Пользователь {target} забанен. Причина: {reason}"
        )
        logger.info(
            "User banned",
            admin_id=update.effective_user.id,
            target_id=user_id,
            reason=reason,
            chat_id=update.effective_chat.id
        )

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Разбанивает пользователя по ID или имени.

    Использование: /unban <user_id или @username>
    Пример: /unban @username
    """
    async with limiter:
        db = context.bot_data['db']
        if not await check_admin(update, db):
            return

        args = context.args
        if not args:
            await update.message.reply_text(
                "❌ Укажите ID/@username. Пример: /unban @username"
            )
            return

        target = bleach.clean(args[0])
        user_id = None

        if target.startswith('@'):
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id FROM users WHERE nick = ?',
                    (target[1:],)
                )
                result = await cursor.fetchone()
                user_id = result['user_id'] if result else None
        else:
            try:
                user_id = int(target)
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID или имени.")
                return

        if not user_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return

        user = await db.get_user(user_id)
        if not user or not user['banned']:
            await update.message.reply_text("❌ Пользователь не забанен.")
            return

        async with db.get_connection() as conn:
            await conn.execute(
                'UPDATE users SET banned = 0, ban_time = NULL WHERE user_id = ?',
                (user_id,)
            )
            await conn.commit()

        await db.log_action(
            user_id=update.effective_user.id,
            action='unban',
            target_id=user_id,
            details="User unbanned"
        )
        await update.message.reply_text(f"✅ Пользователь {target} разбанен.")
        logger.info(
            "User unbanned",
            admin_id=update.effective_user.id,
            target_id=user_id,
            chat_id=update.effective_chat.id
        )

async def set_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Устанавливает роль пользователя (admin, moderator, user).

    Использование: /set_role <user_id или @username> <роль>
    Пример: /set_role @username admin
    """
    async with limiter:
        db = context.bot_data['db']
        if not await check_admin(update, db):
            return

        args = context.args
        if len(args) != 2:
            await update.message.reply_text(
                "❌ Укажите ID/@username и роль. Пример: /set_role @username admin"
            )
            return

        target = bleach.clean(args[0])
        role = bleach.clean(args[1]).lower()
        if role not in ['admin', 'moderator', 'user']:
            await update.message.reply_text(
                "❌ Роль должна быть: admin, moderator или user."
            )
            return

        user_id = None
        if target.startswith('@'):
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id FROM users WHERE nick = ?',
                    (target[1:],)
                )
                result = await cursor.fetchone()
                user_id = result['user_id'] if result else None
        else:
            try:
                user_id = int(target)
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID или имени.")
                return

        if not user_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return

        user = await db.get_user(user_id)
        if not user:
            await update.message.reply_text("❌ Пользователь не найден в базе.")
            return

        async with db.get_connection() as conn:
            await conn.execute(
                'UPDATE users SET role = ? WHERE user_id = ?',
                (role, user_id)
            )
            await conn.commit()

        await db.log_action(
            user_id=update.effective_user.id,
            action='set_role',
            target_id=user_id,
            details=f"New role: {role}"
        )
        await update.message.reply_text(
            f"✅ Пользователю {target} установлена роль {role}."
        )
        logger.info(
            "Role set",
            admin_id=update.effective_user.id,
            target_id=user_id,
            role=role,
            chat_id=update.effective_chat.id
        )

async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Просматривает последние логи действий пользователя.

    Использование: /view_logs <user_id или @username> [лимит]
    Пример: /view_logs @username 5
    """
    async with limiter:
        db = context.bot_data['db']
        if not await check_admin(update, db):
            return

        args = context.args
        if not args:
            await update.message.reply_text(
                "❌ Укажите ID/@username. Пример: /view_logs @username 5"
            )
            return

        target = bleach.clean(args[0])
        limit = 5
        if len(args) > 1:
            try:
                limit = int(args[1])
                if limit < 1 or limit > 20:
                    await update.message.reply_text("❌ Лимит должен быть от 1 до 20.")
                    return
            except ValueError:
                await update.message.reply_text("❌ Неверный формат лимита.")
                return

        user_id = None
        if target.startswith('@'):
            async with db.get_connection() as conn:
                cursor = await conn.execute(
                    'SELECT user_id FROM users WHERE nick = ?',
                    (target[1:],)
                )
                result = await cursor.fetchone()
                user_id = result['user_id'] if result else None
        else:
            try:
                user_id = int(target)
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID или имени.")
                return

        if not user_id:
            await update.message.reply_text("❌ Пользователь не найден.")
            return

        logs = await db.get_user_logs(user_id, limit)
        if not logs:
            await update.message.reply_text("📜 Логи для этого пользователя отсутствуют.")
            return

        response = f"📜 Логи для {target} (последние {limit}):\n\n"
        for log in logs:
            details = log['details'] or 'Нет деталей'
            response += (
                f"ID: {log['log_id']}\n"
                f"Действие: {log['action']}\n"
                f"Цель: {log['target_id']}\n"
                f"Детали: {details}\n"
                f"Время: {log['timestamp']}\n"
                f"---\n"
            )

        await update.message.reply_text(response[:4096])  # Ограничение Telegram
        logger.info(
            "Logs viewed",
            admin_id=update.effective_user.id,
            target_id=user_id,
            limit=limit,
            chat_id=update.effective_chat.id
        )

async def set_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Устанавливает глобальную длительность мута.

    Использование: /set_mute_duration <минуты>
    Пример: /set_mute_duration 60
    """
    async with limiter:
        db = context.bot_data['db']
        if not await check_admin(update, db):
            return

        args = context.args
        if not args:
            await update.message.reply_text(
                "❌ Укажите длительность в минутах. Пример: /set_mute_duration 60"
            )
            return

        try:
            duration = int(args[0])
            if duration < 1:
                await update.message.reply_text("❌ Длительность должна быть положительной.")
                return
        except ValueError:
            await update.message.reply_text("❌ Неверный формат длительности.")
            return

        # Обновление конфигурации (предполагается, что config.py поддерживает динамическое обновление)
        config = get_config()
        config['mute_duration'] = duration

        await db.log_action(
            user_id=update.effective_user.id,
            action='set_mute_duration',
            target_id=0,
            details=f"New mute duration: {duration} minutes"
        )
        await update.message.reply_text(
            f"✅ Длительность мута установлена: {duration} минут."
        )
        logger.info(
            "Mute duration set",
            admin_id=update.effective_user.id,
            duration=duration,
            chat_id=update.effective_chat.id
        )

async def register_handlers(app: Application, db: Database) -> List[Handler]:
    """Регистрирует обработчики административных команд.

    Args:
        app: Экземпляр приложения Telegram.
        db: Экземпляр базы данных.

    Returns:
        List[Handler]: Список зарегистрированных обработчиков.
    """
    app.bot_data['db'] = db
    handlers = [
        CommandHandler('ban', ban_user),
        CommandHandler('unban', unban_user),
        CommandHandler('set_role', set_role),
        CommandHandler('view_logs', view_logs),
        CommandHandler('set_mute_duration', set_mute_duration),
    ]
    logger.info("Admin command handlers registered", count=len(handlers))
    return handlers