Telegram Chatbot
📖 Описание
Это Telegram-бот для управления чатом, обеспечивающий модерацию, голосование за мут, логирование действий и статистику. Бот поддерживает пользовательские, модераторские и административные команды. Репозиторий приватный, доступ предоставляется только приглашённым пользователям.
✨ Функционал

Пользовательские команды:
/start: Приветствие и регистрация.
/nick <имя>: Смена ника.
/poll <вопрос>: Создание опроса.
/rules: Показ правил чата.
/about: Информация о боте.
/ping: Проверка отклика.


Модераторские команды:
/mute <@username>: Мут пользователя.
/unmute <@username>: Снятие мута.
/vote_mute <@username>: Голосование за мут.


Административные команды:
/ban <@username> <причина>: Бан пользователя.
/unban <@username>: Разбан.
/set_role <@username> <роль>: Установка роли (admin, moderator, user).
/view_logs <@username> [лимит]: Просмотр логов.
/set_mute_duration <минуты>: Настройка длительности мута.


Особенности:
SQLite база данных для хранения пользователей и логов.
Шифрование логов с использованием cryptography.
Ограничение частоты команд с aiolimiter.
Структурированное логирование с structlog.



🛠 Требования

Python 3.12+
Git, curl
Telegram-токен от @BotFather
Ключ шифрования (генерируется через cryptography)
Доступ к приватному репозиторию (SSH-ключ или токен GitHub)
VPS с Linux (рекомендуется Ubuntu 22.04/24.04 LTS)

🔒 Доступ к репозиторию
Репозиторий приватный. Для доступа:

Получите приглашение от владельца (Samtagil).
Настройте SSH-ключ:ssh-keygen -t ed25519 -C "your_email@example.com"
cat ~/.ssh/id_ed25519.pub

Добавьте ключ в GitHub: Settings > SSH and GPG keys.
Проверьте доступ:ssh -T git@github.com


Альтернатива: используйте токен GitHub (Settings > Developer settings > Personal access tokens).

🚀 Установка
Подробные инструкции в INSTALL.md. Кратко:

Подключитесь к VPS:
ssh root@your_vps_ip


Скачайте setup.sh:
curl -O https://raw.githubusercontent.com/Samtagil/Anonchatbot/main/setup.sh
chmod +x setup.sh

Для приватного репозитория используйте токен:
curl -H "Authorization: token your_token" -O https://raw.githubusercontent.com/Samtagil/Anonchatbot/main/setup.sh


Получите токен и ключ:

Токен: через @BotFather (/newbot).
Ключ:python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"




Запустите установку:
sudo bash setup.sh git@github.com:Samtagil/Anonchatbot.git your_bot_token your_encryption_key


Проверьте статус:
systemctl status chatbot.service



🕹 Использование

Управление ботом:
sudo systemctl start chatbot.service   # Запуск
sudo systemctl stop chatbot.service    # Остановка
sudo systemctl restart chatbot.service # Перезапуск
journalctl -u chatbot.service -f       # Логи


Тестирование:

Отправьте /start в чат с ботом.
Проверьте /ping (ответ: "Pong! 🏓").
Назначьте себе роль админа:sqlite3 /opt/chatbot/chatbot.db "UPDATE users SET role = 'admin' WHERE user_id = your_telegram_id;"


Попробуйте /ban @username Спам.



🛠 Разработка

Структура:

bot.py: Основной файл, инициализация.
config.py: Загрузка .env.
database.py: Работа с SQLite.
user_commands.py: Пользовательские команды.
admin_commands.py: Административные команды.
.env: Конфигурация (токен, ключ).
.gitignore: Исключение чувствительных файлов.


Зависимости:
pip install -r requirements.txt


Форматирование:
pip install black
black .


Тестирование:
pip install pytest
pytest



🔍 Устранение неполадок

Бот не запускается:journalctl -u chatbot.service -n 100
cat /var/log/chatbot_setup.log


Нет доступа к репозиторию:ssh -T git@github.com


См. INSTALL.md для подробностей.

📜 Лицензия
Приватный проект, использование только с разрешения владельца.
📬 Контакты
Для доступа или вопросов: свяжитесь с владельцем репозитория (Samtagil).
