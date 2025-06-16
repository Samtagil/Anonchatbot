"""Модуль для управления SQLite базой данных чат-бота.

Содержит классы для работы с пользователями, логами, настройками и статистикой.
Использует aiosqlite для асинхронных операций и шифрование для логов.
"""

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import structlog
from cachetools import TTLCache
from cryptography.fernet import Fernet, InvalidToken

from config import get_config

logger = structlog.get_logger()

class BaseDatabase:
    """Базовый класс для управления соединением с SQLite базой данных.

    Attributes:
        db_path: Путь к файлу базы данных.
        cache: Кэш для хранения данных с TTL.
        cipher: Объект для шифрования/дешифрования данных.
    """

    def __init__(self, db_path: str):
        """Инициализирует базовый класс базы данных.

        Args:
            db_path: Путь к файлу базы данных.
        """
        self.db_path = db_path
        self.cache = TTLCache(maxsize=1000, ttl=300)
        self.cipher = self._init_cipher()
        logger.info("BaseDatabase initialized", db_path=db_path)

    def _init_cipher(self) -> Fernet:
        """Инициализирует шифрование с использованием ключа из окружения.

        Returns:
            Fernet: Объект шифрования.

        Raises:
            ValueError: Если ключ шифрования не задан.
        """
        key = os.getenv("ENCRYPTION_KEY")
        if not key:
            key = Fernet.generate_key().decode()
            logger.warning(
                "ENCRYPTION_KEY not set, generated new key. Store it securely!",
                key=key
            )
        return Fernet(key.encode())

    async def get_connection(self) -> aiosqlite.Connection:
        """Возвращает асинхронное соединение с базой данных.

        Returns:
            aiosqlite.Connection: Соединение с базой данных.
        """
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        return conn

class UserDatabase(BaseDatabase):
    """Класс для управления данными пользователей."""

    async def init_db(self) -> None:
        """Инициализирует таблицы пользователей и создаёт индексы."""
        async with self.get_connection() as conn:
            await conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    nick TEXT NOT NULL,
                    role TEXT DEFAULT 'user',
                    join_time TEXT NOT NULL,
                    exit_time TEXT,
                    mute_time TEXT,
                    banned INTEGER DEFAULT 0,
                    ban_time TEXT,
                    frozen_nick INTEGER DEFAULT 0,
                    text_only INTEGER DEFAULT 0,
                    achievements TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_users_user_id
                ON users(user_id);
                CREATE INDEX IF NOT EXISTS idx_users_exit_time
                ON users(exit_time);
                '''
            )
            await conn.commit()
        logger.info("User tables initialized")

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получает данные пользователя по ID.

        Args:
            user_id: ID пользователя.

        Returns:
            Optional[Dict[str, Any]]: Данные пользователя или None, если не найден.
        """
        if user_id in self.cache:
            return self.cache[user_id]
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                'SELECT * FROM users WHERE user_id = ?', (user_id,)
            )
            user = await cursor.fetchone()
        if user:
            user_dict = dict(user)
            self.cache[user_id] = user_dict
            return user_dict
        logger.debug("User not found", user_id=user_id)
        return None

    async def add_achievement(self, user_id: int, achievement_id: str) -> None:
        """Добавляет достижение пользователю.

        Args:
            user_id: ID пользователя.
            achievement_id: ID достижения.
        """
        user = await self.get_user(user_id)
        if not user:
            logger.warning("Cannot add achievement: user not found", user_id=user_id)
            return
        achievements = user['achievements'].split(',') if user['achievements'] else []
        if achievement_id not in achievements:
            achievements.append(achievement_id)
            async with self.get_connection() as conn:
                await conn.execute(
                    'UPDATE users SET achievements = ? WHERE user_id = ?',
                    (','.join(achievements), user_id)
                )
                await conn.commit()
            user['achievements'] = ','.join(achievements)
            self.cache[user_id] = user
            logger.info(
                "Achievement added",
                user_id=user_id,
                achievement_id=achievement_id
            )

class LogDatabase(BaseDatabase):
    """Класс для управления логами действий."""

    async def init_db(self) -> None:
        """Инициализирует таблицы логов и создаёт индексы."""
        async with self.get_connection() as conn:
            await conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    target_id INTEGER,
                    details TEXT,
                    timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_logs_timestamp
                ON logs(timestamp);
                CREATE INDEX IF NOT EXISTS idx_logs_user_id
                ON logs(user_id);
                '''
            )
            await conn.commit()
        logger.info("Log tables initialized")

    async def log_action(
        self,
        user_id: int,
        action: str,
        target_id: int,
        details: Optional[str] = None
    ) -> None:
        """Логирует действие пользователя.

        Args:
            user_id: ID пользователя, выполнившего действие.
            action: Название действия.
            target_id: ID целевого пользователя.
            details: Дополнительные детали (шифруются).
        """
        if not isinstance(action, str) or len(action) > 100:
            logger.error("Invalid action format", action=action)
            return
        encrypted_details = None
        if details:
            try:
                encrypted_details = self.cipher.encrypt(details.encode()).decode()
            except Exception as e:
                logger.error("Encryption failed", error=str(e))
                return
        async with self.get_connection() as conn:
            await conn.execute(
                '''
                INSERT INTO logs (
                    user_id, action, target_id, details, timestamp
                )
                VALUES (?, ?, ?, ?, ?)
                ''',
                (
                    user_id,
                    action,
                    target_id,
                    encrypted_details,
                    datetime.utcnow().isoformat()
                )
            )
            await conn.commit()
        logger.info(
            "Action logged",
            user_id=user_id,
            action=action,
            target_id=target_id
        )

    async def get_user_logs(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Получает последние логи действий пользователя.

        Args:
            user_id: ID пользователя.
            limit: Максимальное количество логов.

        Returns:
            List[Dict[str, Any]]: Список логов.
        """
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                '''
                SELECT log_id, user_id, action, target_id, details, timestamp
                FROM logs
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                ''',
                (user_id, limit)
            )
            logs = await cursor.fetchall()
        result = []
        for log in logs:
            log_dict = dict(log)
            if log_dict['details']:
                try:
                    log_dict['details'] = self.cipher.decrypt(
                        log_dict['details'].encode()
                    ).decode()
                except InvalidToken:
                    logger.error("Decryption failed for log", log_id=log_dict['log_id'])
                    log_dict['details'] = "[DECRYPTION FAILED]"
            result.append(log_dict)
        return result

    async def purge_logs(self, days: int = 30) -> int:
        """Удаляет логи старше указанного количества дней.

        Args:
            days: Количество дней.

        Returns:
            int: Количество удалённых логов.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                'DELETE FROM logs WHERE timestamp < ?',
                (cutoff,)
            )
            deleted = cursor.rowcount
            await conn.commit()
        logger.info("Logs purged", deleted_count=deleted, days=days)
        return deleted

class SettingsDatabase(BaseDatabase):
    """Класс для управления настройками пользователей."""

    async def init_db(self) -> None:
        """Инициализирует таблицы настроек и создаёт индексы."""
        async with self.get_connection() as conn:
            await conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS settings (
                    user_id INTEGER PRIMARY KEY,
                    hug_text TEXT,
                    slap_text TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_settings_user_id
                ON settings(user_id);
                '''
            )
            await conn.commit()
        logger.info("Settings tables initialized")

    async def get_setting(self, user_id: int, setting: str) -> Optional[str]:
        """Получает значение настройки пользователя.

        Args:
            user_id: ID пользователя.
            setting: Название настройки (hug_text, slap_text).

        Returns:
            Optional[str]: Значение настройки или None.
        """
        if setting not in ['hug_text', 'slap_text']:
            logger.error("Invalid setting", setting=setting)
            return None
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                f'SELECT {setting} FROM settings WHERE user_id = ?',
                (user_id,)
            )
            result = await cursor.fetchone()
        return result[setting] if result else None

class StatsDatabase(BaseDatabase):
    """Класс для управления статистикой чата."""

    async def init_db(self) -> None:
        """Инициализирует таблицы статистики и создаёт индексы."""
        async with self.get_connection() as conn:
            await conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    target_user_id INTEGER,
                    content TEXT NOT NULL,
                    is_private INTEGER DEFAULT 0,
                    timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT
                );
                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_id INTEGER,
                    user_id INTEGER,
                    option_index INTEGER,
                    PRIMARY KEY (poll_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                ON messages(timestamp);
                CREATE INDEX IF NOT EXISTS idx_polls_start_time
                ON polls(start_time);
                '''
            )
            await conn.commit()
        logger.info("Stats tables initialized")

    async def get_chat_stats(self) -> Dict[str, int]:
        """Получает статистику чата.

        Returns:
            Dict[str, int]: Статистика чата.
        """
        cache_key = "chat_stats"
        if cache_key in self.cache:
            return self.cache[cache_key]
        async with self.get_connection() as conn:
            active_users = await conn.execute(
                'SELECT COUNT(*) FROM users WHERE exit_time IS NULL'
            )
            banned_users = await conn.execute(
                'SELECT COUNT(*) FROM users WHERE banned = 1'
            )
            total_messages = await conn.execute(
                'SELECT COUNT(*) FROM messages'
            )
            total_pms = await conn.execute(
                'SELECT COUNT(*) FROM messages WHERE is_private = 1'
            )
            active_polls = await conn.execute(
                'SELECT COUNT(*) FROM polls WHERE end_time IS NULL'
            )
            stats = {
                'active_users': (await active_users.fetchone())[0],
                'banned_users': (await banned_users.fetchone())[0],
                'total_messages': (await total_messages.fetchone())[0],
                'total_pms': (await total_pms.fetchone())[0],
                'active_polls': (await active_polls.fetchone())[0],
            }
            await conn.commit()
        self.cache[cache_key] = stats
        logger.info("Chat stats retrieved", stats=stats)
        return stats

    async def add_mute_vote(self, target_user_id: int, voter_id: int) -> None:
        """Добавляет голос за мут пользователя.

        Args:
            target_user_id: ID пользователя, за которого голосуют.
            voter_id: ID голосующего пользователя.
        """
        async with self.get_connection() as conn:
            await conn.execute(
                '''
                INSERT INTO poll_votes (poll_id, user_id, option_index)
                VALUES (?, ?, 0)
                ''',
                (f"mute_{target_user_id}", voter_id)
            )
            await conn.commit()
        logger.info(
            "Mute vote added",
            target_user_id=target_user_id,
            voter_id=voter_id
        )

    async def count_mute_votes(self, target_user_id: int, window_minutes: int) -> int:
        """Подсчитывает голоса за мут в заданном временном окне.

        Args:
            target_user_id: ID пользователя.
            window_minutes: Временное окно в минутах.

        Returns:
            int: Количество голосов.
        """
        cutoff = (
            datetime.utcnow() - timedelta(minutes=window_minutes)
        ).isoformat()
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                '''
                SELECT COUNT(*) FROM poll_votes
                WHERE poll_id = ? AND timestamp > ?
                ''',
                (f"mute_{target_user_id}", cutoff)
            )
            count = (await cursor.fetchone())[0]
        logger.debug(
            "Mute votes counted",
            target_user_id=target_user_id,
            count=count
        )
        return count

class Database:
    """Основной класс для взаимодействия с базой данных чат-бота.

    Attributes:
        user_db: Модуль для управления пользователями.
        log_db: Модуль для управления логами.
        settings_db: Модуль для управления настройками.
        stats_db: Модуль для управления статистикой.
    """

    def __init__(self):
        """Инициализирует класс базы данных."""
        config = get_config()
        db_path = config['db_path']
        self.user_db = UserDatabase(db_path)
        self.log_db = LogDatabase(db_path)
        self.settings_db = SettingsDatabase(db_path)
        self.stats_db = StatsDatabase(db_path)
        logger.info("Database initialized")

    async def init_db(self) -> None:
        """Инициализирует все таблицы базы данных."""
        try:
            await asyncio.gather(
                self.user_db.init_db(),
                self.log_db.init_db(),
                self.settings_db.init_db(),
                self.stats_db.init_db(),
            )
            logger.info("All database tables initialized")
        except aiosqlite.Error as e:
            logger.error("Database initialization failed", error=str(e))
            raise

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получает данные пользователя.

        Args:
            user_id: ID пользователя.

        Returns:
            Optional[Dict[str, Any]]: Данные пользователя или None.
        """
        return await self.user_db.get_user(user_id)

    async def add_achievement(self, user_id: int, achievement_id: str) -> None:
        """Добавляет достижение пользователю.

        Args:
            user_id: ID пользователя.
            achievement_id: ID достижения.
        """
        await self.user_db.add_achievement(user_id, achievement_id)

    async def log_action(
        self,
        user_id: int,
        action: str,
        target_id: int,
        details: Optional[str] = None
    ) -> None:
        """Логирует действие.

        Args:
            user_id: ID пользователя.
            action: Название действия.
            target_id: ID целевого пользователя.
            details: Дополнительные детали.
        """
        await self.log_db.log_action(user_id, action, target_id, details)

    async def get_user_logs(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Получает логи пользователя.

        Args:
            user_id: ID пользователя.
            limit: Максимальное количество логов.

        Returns:
            List[Dict[str, Any]]: Список логов.
        """
        return await self.log_db.get_user_logs(user_id, limit)

    async def purge_logs(self, days: int = 30) -> int:
        """Удаляет старые логи.

        Args:
            days: Количество дней.

        Returns:
            int: Количество удалённых логов.
        """
        return await self.log_db.purge_logs(days)

    async def get_setting(self, user_id: int, setting: str) -> Optional[str]:
        """Получает настройку пользователя.

        Args:
            user_id: ID пользователя.
            setting: Название настройки.

        Returns:
            Optional[str]: Значение настройки или None.
        """
        return await self.settings_db.get_setting(user_id, setting)

    async def get_chat_stats(self) -> Dict[str, int]:
        """Получает статистику чата.

        Returns:
            Dict[str, int]: Статистика чата.
        """
        return await self.stats_db.get_chat_stats()

    async def add_mute_vote(self, target_user_id: int, voter_id: int) -> None:
        """Добавляет голос за мут.

        Args:
            target_user_id: ID пользователя.
            voter_id: ID голосующего.
        """
        await self.stats_db.add_mute_vote(target_user_id, voter_id)

    async def count_mute_votes(self, target_user_id: int, window_minutes: int) -> int:
        """Подсчитывает голоса за мут.

        Args:
            target_user_id: ID пользователя.
            window_minutes: Временное окно в минутах.

        Returns:
            int: Количество голосов.
        """
        return await self.stats_db.count_mute_votes(target_user_id, window_minutes)