# Telegram Chatbot

## 📖 Описание
Это Telegram-бот для управления чатом, обеспечивающий модерацию, голосование за мут, логирование действий и статистику. Бот поддерживает пользовательские, модераторские и административные команды. Репозиторий **приватный**, доступ предоставляется только приглашённым пользователям.

## ✨ Функционал
- **Пользовательские команды**:
  - `/start`: Приветствие и регистрация.
  - `/nick <имя>`: Смена ника.
  - `/poll <вопрос>`: Создание опроса.
  - `/rules`: Показ правил чата.
  - `/about`: Информация о боте.
  - `/ping`: Проверка отклика.
- **Модераторские команды**:
  - `/mute <@username>`: Мут пользователя.
  - `/unmute <@username>`: Снятие мута.
  - `/vote_mute <@username>`: Голосование за мут.
- **Административные команды**:
  - `/ban <@username> <причина>`: Бан пользователя.
  - `/unban <@username>`: Разбан.
  - `/set_role <@username> <роль>`: Установка роли (admin, moderator, user).
  - `/view_logs <@username> [лимит]`: Просмотр логов.
  - `/set_mute_duration <минуты>`: Настройка длительности мута.
- **Особенности**:
  - SQLite база данных для хранения пользователей и логов.
  - Шифрование логов с использованием `cryptography`.
  - Ограничение частоты команд с `aiolimiter`.
  - Структурированное логирование с `structlog`.

## 🛠 Требования
- Python 3.12+
- Git, curl
- Telegram-токен от [@BotFather](https://t.me/BotFather)
- Ключ шифрования (генерируется через `cryptography`)
- Доступ к приватному репозиторию (SSH-ключ)
- VPS с Linux (рекомендуется Ubuntu 22.04/24.04 LTS)

## 🔒 Доступ к репозиторию
Репозиторий приватный. Для доступа:
1. Получите приглашение от владельца (`Samtagil`).
2. Настройте SSH-ключ:
   ```bash
   ssh-keygen -t ed25519 -C "your_email@example.com"
   cat ~/.ssh/id_ed25519.pub
   ```
   Добавьте ключ в GitHub: Settings > SSH and GPG keys.
3. Проверьте доступ:
   ```bash
   ssh -T git@github.com
   ```

## 🚀 Установка
Подробные инструкции в [`INSTALL.md`](./INSTALL.md). Кратко:

1. Подключитесь к VPS:
   ```bash
   ssh root@your_vps_ip
   ```

2. Скачайте `setup.sh`:
   ```bash
   curl -O https://raw.githubusercontent.com/Samtagil/Anonchatbot/main/setup.sh
   chmod +x setup.sh
   ```

3. Получите токен и ключ:
   - Токен: через @BotFather (`/newbot`).
   - Ключ:
     ```bash
     python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```

4. Запустите установку, указав данные:
   ```bash
   sudo bash setup.sh git@github.com:Samtagil/Anonchatbot.git your_bot_token your_encryption_key
   ```

5. Проверьте статус:
   ```bash
   systemctl status chatbot.service
   ```

## 🕹 Использование
- **Управление ботом**:
  ```bash
  sudo systemctl start chatbot.service   # Запуск
  sudo systemctl stop chatbot.service    # Остановка
  sudo systemctl restart chatbot.service # Перезапуск
  journalctl -u chatbot.service -f       # Логи
  ```

- **Тестирование**:
  - Отправьте `/start` в чат с ботом.
  - Проверьте `/ping` (ответ: "Pong! 🏓").
  - Назначьте себе роль админа:
    ```sql
    sqlite3 /opt/chatbot/chatbot.db "UPDATE users SET role = 'admin' WHERE user_id = your_telegram_id;"
    ```
  - Попробуйте `/ban @username Спам`.

## 🛠 Разработка
- **Структура**:
  - `bot.py`: Основной файл, инициализация.
  - `config.py`: Загрузка `.env`.
  - `database.py`: Работа с SQLite.
  - `user_commands.py`: Пользовательские команды.
  - `admin_commands.py`: Административные команды.
  - `.env`: Конфигурация (токен, ключ).
  - `.gitignore`: Исключение чувствительных файлов.

- **Зависимости**:
  ```bash
  pip install -r requirements.txt
  ```

- **Форматирование**:
  ```bash
  pip install black
  black .
  ```

- **Тестирование**:
  ```bash
  pip install pytest
  pytest
  ```

## 🔍 Устранение неполадок
- **Бот не запускается**:
  ```bash
  journalctl -u chatbot.service -n 100
  cat /var/log/chatbot_setup.log
  ```
- **Нет доступа к репозиторию**:
  ```bash
  ssh -T git@github.com
  ```
- См. [`INSTALL.md`](./INSTALL.md) для подробностей.

## 📜 Лицензия
Приватный проект, использование только с разрешения владельца.

## 📬 Контакты
Для доступа или вопросов: свяжитесь с владельцем репозитория (`Samtagil`).
