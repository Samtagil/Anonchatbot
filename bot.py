"""Основной модуль для запуска Telegram-бота чат-приложения.

Инициализирует бот, базу данных и регистрирует обработчики команд.
"""

import asyncio
import os
from typing import List

import structlog
from telegram.ext import Application, ApplicationBuilder, Handler
from telegram.error import TelegramError

from config import get_config
from database import Database

logger = structlog.get_logger()

class HandlerRegistry:
    """Класс для регистрации обработчиков команд бота.

    Attributes:
        db: Экземпляр базы данных.
        handlers: Список зарегистрированных обработчиков.
    """

    def __init__(self, db: Database):
        """Инициализирует реестр обработчиков.

        Args:
            db: Экземпляр базы данных.
        """
        self.db = db
        self.handlers: List[Handler] = []
        logger.info("HandlerRegistry initialized")

    async def register_user_commands(self) -> None:
        """Регистрирует пользовательские команды."""
        try:
            from commands.user_commands import register_handlers
            self.handlers.extend(await asyncio.to_thread(register_handlers, self.db))
            logger.info("User commands registered")
        except ImportError as e:
            logger.error("Failed to register user commands", error=str(e))
            raise

    async def register_moderator_commands(self) -> None:
        """Регистрирует модераторские команды."""
        try:
            from commands.mod_commands import register_handlers
            self.handlers.extend(await asyncio.to_thread(register_handlers, self.db))
            logger.info("Moderator commands registered")
        except ImportError as e:
            logger.error("Failed to register moderator commands", error=str(e))
            raise

    async def register_all(self) -> List[Handler]:
        """Регистрирует все обработчики команд.

        Returns:
            List[Handler]: Список зарегистрированных обработчиков.
        """
        await asyncio.gather(
            self.register_user_commands(),
            self.register_moderator_commands(),
        )
        return self.handlers

class Bot:
    """Класс для управления Telegram-ботом.

    Attributes:
        config: Конфигурация бота.
        db: Экземпляр базы данных.
        app: Экземпляр приложения Telegram.
    """

    def __init__(self):
        """Инициализирует бот."""
        self.config = get_config()
        self._validate_config()
        self.db = Database()
        self.app = ApplicationBuilder().token(self.config['telegram_bot_token']).build()
        logger.info("Bot initialized")

    def _validate_config(self) -> None:
        """Проверяет наличие обязательных параметров конфигурации.

        Raises:
            ValueError: Если отсутствует обязательный параметр.
        """
        required_keys = ['telegram_bot_token', 'db_path']
        missing = [key for key in required_keys if key not in self.config]
        if missing:
            logger.error("Missing configuration keys", missing_keys=missing)
            raise ValueError(f"Missing configuration keys: {', '.join(missing)}")
        if not os.getenv("ENCRYPTION_KEY"):
            logger.warning("ENCRYPTION_KEY not set. Ensure it is configured.")

    async def init_db(self) -> None:
        """Инициализирует базу данных."""
        try:
            await self.db.init_db()
            logger.info("Database initialized")
        except Exception as e:
            logger.error("Database initialization failed", error=str(e))
            raise

    async def setup_handlers(self) -> None:
        """Настраивает обработчики команд."""
        registry = HandlerRegistry(self.db)
        handlers = await registry.register_all()
        for handler in handlers:
            self.app.add_handler(handler)
        logger.info("All handlers registered", handler_count=len(handlers))

    async def run(self) -> None:
        """Запускает бот."""
        try:
            await self.init_db()
            await self.setup_handlers()
            logger.info("Starting bot polling")
            await self.app.run_polling()
        except TelegramError as e:
            logger.error("Bot polling failed", error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error during bot run", error=str(e))
            raise

async def main() -> None:
    """Основная функция для запуска бота."""
    bot = Bot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())