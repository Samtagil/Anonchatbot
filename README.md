# Установка и настройка чат-бота на VPS с Linux

## Описание
Этот мануал описывает процесс установки Telegram чат-бота на VPS с Linux (например, Ubuntu 22.04/24.04 LTS), клонированного с GitHub. Бот предоставляет функционал для управления чатом, включая пользовательские команды (`/start`, `/nick`, `/poll`), модерацию (`/mute`, `/ban`), логирование и статистику. Основные файлы: `bot.py`, `config.py`, `database.py`, `mod_commands.py`, `user_commands.py`.

Установка выполняется одним скриптом (`setup.sh`), который автоматизирует все шаги: клонирование репозитория, установка зависимостей, настройка окружения, создание конфигурации и запуск бота как службы systemd. Последующие действия (запуск, остановка, обновление) выполняются через консольные команды.

## Подготовка
Перед началом убедитесь, что:
- У вас есть VPS с Linux (рекомендуется Ubuntu 22.04/24.04 LTS).
- Вы имеете root-доступ (или доступ через `sudo`).
- Установлен SSH-доступ к VPS (`ssh root@your_vps_ip`).
- У вас есть Telegram-бот, созданный через [@BotFather](https://t.me/BotFather), и его токен (`TELEGRAM_BOT_TOKEN`).
- Сгенерирован ключ шифрования (`ENCRYPTION_KEY`) для логов:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Доступ к GitHub-репозиторию (публичному или приватному). Для приватного репозитория настройте SSH-ключ:
  ```bash
  ssh-keygen -t ed25519 -C "your_email@example.com"
  cat ~/.ssh/id_ed25519.pub # Добавьте ключ в GitHub (Settings > SSH and GPG keys)
  ssh -T git@github.com # Проверка доступа
  ```

## Установка
Установка выполняется одним скриптом `setup.sh`, который требует три аргумента: URL репозитория, токен бота и ключ шифрования.

### Шаги
1. **Подключитесь к VPS**:
   ```bash
   ssh root@your_vps_ip
   ```

2. **Скачайте скрипт установки**:
   ```bash
   curl -O https://github.com/Samtagil/Anonchatbot/main/setup.sh
   chmod +x setup.sh
   ```

3. **Запустите скрипт установки**:
   ```bash
   sudo bash setup.sh git@github.com:your_username/your_repo.git your_bot_token your_encryption_key
   ```
   Замените:
   - `git@github.com:your_username/your_repo.git` на URL вашего репозитория.
   - `your_bot_token` на токен от @BotFather.
   - `your_encryption_key` на сгенерированный ключ шифрования.

   Скрипт выполнит:
   - Установку системных пакетов (`python3`, `git`, `curl`).
   - Создание непривилегированного пользователя (`chatbot`).
   - Клонирование репозитория в `/opt/chatbot`.
   - Настройку виртуального окружения и установку зависимостей.
   - Создание `.env` с указанными параметрами.
   - Настройку службы systemd (`chatbot.service`) для автозапуска.
   - Запуск бота и проверку его статуса.

4. **Проверьте логи установки**:
   ```bash
   cat /var/log/chatbot_setup.log
   ```

## Управление ботом
После установки бот запускается как служба systemd. Используйте следующие команды:

- **Проверка статуса**:
  ```bash
  systemctl status chatbot.service
  ```

- **Запуск**:
  ```bash
  sudo systemctl start chatbot.service
  ```

- **Остановка**:
  ```bash
  sudo systemctl stop chatbot.service
  ```

- **Перезапуск**:
  ```bash
  sudo systemctl restart chatbot.service
  ```

- **Просмотр логов**:
  ```bash
  journalctl -u chatbot.service -f
  ```

## Обновление бота
Для обновления кода бота:
1. Перейдите в директорию:
   ```bash
   cd /opt/chatbot
   ```

2. Обновите репозиторий:
   ```bash
   sudo -u chatbot git pull origin main
   ```

3. Переустановите зависимости (если изменился `requirements.txt`):
   ```bash
   sudo -u chatbot bash -c "source venv/bin/activate && pip install -r requirements.txt"
   ```

4. Перезапустите службу:
   ```bash
   sudo systemctl restart chatbot.service
   ```

## Структура проекта
- **bot.py**: Основной файл, инициализирует бот и регистрирует обработчики.
- **config.py**: Загружает конфигурацию из `.env`.
- **database.py**: Управляет SQLite базой данных (пользователи, логи, статистика).
- **mod_commands.py**: Модераторские команды (`/mute`, `/ban`, `/unmute`).
- **user_commands.py**: Пользовательские команды (`/start`, `/nick`, `/poll`).
- **requirements.txt**: Зависимости Python.
- **.env**: Конфигурация (токен, ключ шифрования, настройки).
- **setup.sh**: Скрипт установки.
- **chatbot.service**: Служба systemd.

## Процессы
1. **Инициализация**:
   - `bot.py` загружает конфигурацию через `config.py`.
   - Инициализируется база данных (`database.py`) в `/opt/chatbot/chatbot.db`.
   - Регистрируются обработчики команд (`mod_commands.py`, `user_commands.py`).

2. **Обработка команд**:
   - Пользовательские команды обрабатываются асинхронно с ограничением частоты (`aiolimiter`).
   - Данные сохраняются в SQLite, логи шифруются (`cryptography`).
   - Действия логируются с `structlog`.

3. **Модерация**:
   - Модераторы используют команды для управления чатом.
   - Проверяются роли и права доступа через базу данных.

4. **Фоновые процессы**:
   - Бот работает как служба systemd, автоматически перезапускается при сбоях.
   - Логи сохраняются в `/var/log/chatbot_setup.log` (установка) и через `journalctl` (работа).

## Устранение неполадок
- **Бот не запускается**:
  - Проверьте логи:
    ```bash
    journalctl -u chatbot.service -n 100
    ```
  - Убедитесь, что `.env` содержит корректные `TELEGRAM_BOT_TOKEN` и `ENCRYPTION_KEY`.
  - Проверьте права доступа:
    ```bash
    ls -l /opt/chatbot/.env
    ```

- **Ошибка клонирования репозитория**:
  - Проверьте SSH-доступ:
    ```bash
    ssh -T git@github.com
    ```
  - Убедитесь, что SSH-ключ добавлен в GitHub.

- **Зависимости не установились**:
  - Проверьте версию Python:
    ```bash
    python3 --version
    ```
  - Удалите и пересоздайте виртуальное окружение:
    ```bash
    rm -rf /opt/chatbot/venv
    sudo -u chatbot bash -c "cd /opt/chatbot && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    ```

- **База данных не инициализируется**:
  - Удалите файл базы данных и перезапустите бот:
    ```bash
    rm /opt/chatbot/chatbot.db
    sudo systemctl restart chatbot.service
    ```

## Безопасность
- Храните `.env` с правами `600`:
  ```bash
  chmod 600 /opt/chatbot/.env
  ```
- Не добавляйте `.env` в Git (уже исключён через `.gitignore`).
- Регулярно ротируйте `ENCRYPTION_KEY` и обновляйте `.env`.
- Используйте брандмауэр (например, `ufw`):
  ```bash
  sudo ufw allow 22
  sudo ufw enable
  ```

## Тестирование
После установки протестируйте бот:
1. Отправьте `/start` в Telegram-чат с ботом.
2. Проверьте ответ: "✅ Добро пожаловать, <ник>! Ты в чате."
3. Используйте `/ping` для проверки отклика: "Pong! 🏓".
4. Проверьте логи:
   ```bash
   journalctl -u chatbot.service -f
   ```

## Дополнительные рекомендации
- **Резервное копирование**:
  ```bash
  cp /opt/chatbot/chatbot.db /backups/chatbot_$(date +%F).db
  ```

- **Мониторинг**:
  Установите `prometheus` или `grafana` для отслеживания метрик.

- **Форматирование кода**:
  Примените Black для разработки:
  ```bash
  sudo -u chatbot bash -c "cd /opt/chatbot && source venv/bin/activate && pip install black && black ."
  ```

- **Обновление системы**:
  Регулярно обновляйте пакеты:
  ```bash
  sudo apt-get update && sudo apt-get upgrade -y
  ```
