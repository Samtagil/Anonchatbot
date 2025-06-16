"""Модуль конфигурации для Telegram чат-бота.

Содержит загрузку переменных окружения из файла .env и определение констант,
используемых в приложении.
"""

from dotenv import dotenv_values
from typing import Dict, Any

# Загрузка переменных окружения из файла .env
_config: Dict[str, str] = dotenv_values(".env")

# Основные конфигурационные параметры
telegram_bot_token: str = _config.get("TELEGRAM_BOT_TOKEN", "")
db_file: str = _config.get("DB_FILE", "chatbot.db")
mute_vote_threshold: int = int(_config.get("MUTE_VOTE_THRESHOLD", "5"))
mute_vote_window: int = int(_config.get("MUTE_VOTE_WINDOW", "60"))
mute_duration: int = int(_config.get("MUTE_DURATION", "30"))
rules_text: str = _config.get(
    "RULES_TEXT",
    "📜 Правила чата:\n\n1. Будьте вежливы.\n2. Без спама.\n3. Соблюдайте законы.\n4. Чат не для секса и мд.",
)
about_text: str = _config.get(
    "Чат для общения группы лиц без комнат и приваток.",
    "🤖 Бот для управления чатом.\n\nВерсия: 1.0\nАвтор: K-luch",
)

# Валидация критически важных переменных
if not telegram_bot_token:
    raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")
if not db_file:
    raise ValueError("DB_FILE must be set in .env file")
# Примечание: Для db_file рекомендуется шифрование, если содержит чувствительные данные

def get_config() -> Dict[str, Any]:
    """Возвращает словарь с текущими конфигурационными параметрами.

    Returns:
        Dict[str, Any]: Словарь конфигурации.
    """
    return {
        "telegram_bot_token": telegram_bot_token,
        "db_file": db_file,
        "mute_vote_threshold": mute_vote_threshold,
        "mute_vote_window": mute_vote_window,
        "mute_duration": mute_duration,
        "rules_text": rules_text,
        "about_text": about_text,
    }