#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import time
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ContextTypes, filters
)

from config import BOT_TOKEN, ADMIN_CHATS, DIRECTIONS, APPLICATION_FORM_SEND, APPLICATION_FORM_RECEIVE
from database import KPDatabase
from models.rate_limiter import RateLimiter
from handlers.user_handler import UserHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# Простой HTTP-сервер для Render.com
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running')
    
    def log_message(self, format, *args):
        pass  # Отключаем логи HTTP-сервера


def start_health_server():
    """Запускает HTTP-сервер для проверки здоровья на Render"""
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"Health check server started on port {port}")
    server.serve_forever()


class ApplicationBot:
    def __init__(self):
        self.user_states = {}
        self.rate_limiter = RateLimiter(max_requests=15, time_window=60)
        self.user_applications = {}
        self.admin_states = {}
        self.db = KPDatabase()
        
        self.user_handler = UserHandler(self.db, self.user_states, self.user_applications)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        await self.user_handler.start(update, context)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на inline кнопки"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем блокировку пользователя
        if self.db.is_user_blocked(user_id):
            await query.answer("❌ Вы заблокированы и не можете использовать бота.", show_alert=True)
            return
        
        # Обновляем информацию о пользователе
        user_data = {
            'user_id': user_id,
            'username': query.from_user.username,
            'first_name': query.from_user.first_name,
            'last_name': query.from_user.last_name
        }
        self.db.add_or_update_user(user_data)
        
        # Rate limiting
        if not self.rate_limiter.is_allowed(user_id):
            remaining_time = self.rate_limiter.get_remaining_time(user_id)
            await query.answer(f"⏳ Слишком много запросов. Попробуйте через {remaining_time} секунд.", show_alert=True)
            return
        
        await query.answer()
        
        # Обработка callback'ов
        if query.data == "restart":
            await self.user_handler.start(update, context)
        elif query.data.startswith("operation_"):
            await self.user_handler.handle_operation_selection(update, context)
        elif query.data.startswith("direction_"):
            await self.user_handler.handle_direction_selection(update, context)
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Проверяем блокировку пользователя
        if self.db.is_user_blocked(user_id):
            await update.message.reply_text("❌ Вы заблокированы и не можете использовать бота.")
            return
        
        # Обновляем информацию о пользователе
        user_data = {
            'user_id': user_id,
            'username': update.effective_user.username,
            'first_name': update.effective_user.first_name,
            'last_name': update.effective_user.last_name
        }
        self.db.add_or_update_user(user_data)
        
        # Rate limiting
        if not self.rate_limiter.is_allowed(user_id):
            remaining_time = self.rate_limiter.get_remaining_time(user_id)
            await update.message.reply_text(
                f"⏳ Слишком много запросов. Попробуйте через {remaining_time} секунд."
            )
            return
        
        # Проверяем, это админская команда
        if update.message.text.startswith('/'):
            await self.handle_admin_command(update, context)
            return
        
        # Проверяем, это админский чат
        if str(chat_id) in [str(chat_id_config) for chat_id_config in ADMIN_CHATS.values()]:
            logger.info(f"Это админский чат {chat_id}")
            
            # Проверяем ответ на сообщение бота
            if update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot:
                logger.info(f"Ответ администратора на сообщение бота - обрабатываем для пересылки")
                await self.handle_admin_response(update, context)
                return
            
            # Любое другое сообщение в админском чате игнорируем
            return
        
        # Обработка заявок от пользователей
        await self.process_application(update, context)
    
    async def process_application(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает заявку пользователя"""
        user_id = update.effective_user.id
        
        # Проверяем состояние пользователя
        if user_id not in self.user_states or self.user_states[user_id]['state'] != 'waiting_application':
            await update.message.reply_text(
                "❌ Неожиданное состояние. Начните заново командой /start"
            )
            return
        
        user_state = self.user_states[user_id]
        direction = user_state['direction']
        operation = user_state.get('operation', 'send')
        application_text = update.message.text.strip()
        
        # Определяем тип операции для админа
        operation_text = "КЛИЕНТ ПЛАТИТ" if operation == 'send' else "ПЛАТИМ НА КЛИЕНТА"
        
        # Сохраняем заявку для валидации
        self.user_applications[user_id] = {
            'direction': direction,
            'operation': operation,
            'application_text': application_text,
            'timestamp': time.time(),
            'admin_chat_id': ADMIN_CHATS[direction]
        }
        
        # Отправляем заявку админу
        admin_message = f"""
📋 НОВАЯ ЗАЯВКА

💸 <b>{operation_text}</b>

👤 Пользователь: @{update.effective_user.username or update.effective_user.first_name}
🆔 ID: {user_id}
🏗️ Направление: {DIRECTIONS[direction]}
📝 Заявка: {application_text}

⏰ Время: {time.strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHATS[direction],
                text=admin_message,
                parse_mode='HTML'
            )
            
            # Отправляем подтверждение пользователю
            await update.message.reply_text(
                f"✅ Заявка отправлена в направление '{DIRECTIONS[direction]}'!\n\n"
                f"💸 Тип операции: {operation_text}\n\n"
                "⏳ Ожидайте ответа от администратора."
            )
            
            # Очищаем состояние пользователя
            del self.user_states[user_id]
                
        except Exception as e:
            logger.error(f"Ошибка отправки заявки: {e}")
            await update.message.reply_text(
                "❌ Ошибка отправки заявки. Попробуйте позже."
            )
    
    async def handle_admin_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает ответ администратора"""
        reply_to_message = update.message.reply_to_message
        
        if not reply_to_message or not reply_to_message.from_user.is_bot:
            return
        
        # Извлекаем user_id из текста сообщения бота
        bot_message_text = reply_to_message.text
        if "ID: " not in bot_message_text:
            return
        
        try:
            user_id_line = [line for line in bot_message_text.split('\n') if 'ID:' in line][0]
            user_id = int(user_id_line.split('ID:')[1].strip())
            
            # Отправляем ответ пользователю
            response_text = update.message.text
            await context.bot.send_message(
                chat_id=user_id,
                text=f"💬 Ответ от администратора:\n\n{response_text}"
            )
            
            # Подтверждаем админу
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Ответ отправлен пользователю {user_id}"
            )
            
        except Exception as e:
            logger.error(f"Ошибка обработки ответа админа: {e}")
    
    async def handle_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команд админа"""
        message_text = update.message.text.lower()
        chat_id = update.effective_chat.id
        
        # Определяем направление для админского чата
        is_admin_chat = False
        admin_direction = None
        for direction, chat_id_config in ADMIN_CHATS.items():
            if str(chat_id) == str(chat_id_config):
                is_admin_chat = True
                admin_direction = direction
                break
        
        if not is_admin_chat:
            return
        
        logger.info(f"Обрабатываем админскую команду: {message_text} в чате {chat_id}")
        
        # Обработка команд
        if message_text.startswith('/help_admin'):
            help_text = """
🔧 ДОСТУПНЫЕ КОМАНДЫ АДМИНА:

👥 /users - Список всех пользователей
🆕 /new_users - Новые пользователи (за 7 дней)
🚫 /block <user_id> - Заблокировать пользователя
✅ /unblock <user_id> - Разблокировать пользователя
❓ /help_admin - Эта справка
            """
            await context.bot.send_message(chat_id=chat_id, text=help_text)
        
        elif message_text.startswith('/users'):
            await self._handle_users_command(update, context)
        
        elif message_text.startswith('/new_users'):
            await self._handle_new_users_command(update, context)
        
        elif message_text.startswith('/block'):
            await self._handle_block_command(update, context)
        
        elif message_text.startswith('/unblock'):
            await self._handle_unblock_command(update, context)
    
    async def _handle_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список всех пользователей"""
        chat_id = update.effective_chat.id
        users = self.db.get_all_users()
        
        if not users:
            await context.bot.send_message(chat_id=chat_id, text="❌ Пользователи не найдены.")
            return
            
        message = "👥 ВСЕ ПОЛЬЗОВАТЕЛИ БОТА:\n\n"
        
        for i, user in enumerate(users[:50], 1):  # Показываем только первых 50
            status = "🚫 ЗАБЛОКИРОВАН" if user['is_blocked'] else "✅ АКТИВЕН"
            username = f"@{user['username']}" if user['username'] else "Без username"
            name = f"{user['first_name']} {user['last_name']}".strip() if user['last_name'] else user['first_name']
            
            message += f"{i}. {username}\n"
            message += f"   Имя: {name}\n"
            message += f"   ID: {user['user_id']}\n"
            message += f"   Статус: {status}\n"
            message += f"   Первый вход: {user['first_seen']}\n"
            message += f"   Последняя активность: {user['last_activity']}\n\n"
        
        if len(users) > 50:
            message += f"... и еще {len(users) - 50} пользователей"
        
        await context.bot.send_message(chat_id=chat_id, text=message)
    
    async def _handle_new_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает новых пользователей за последние 7 дней"""
        chat_id = update.effective_chat.id
        users = self.db.get_new_users(7)
        
        if not users:
            await context.bot.send_message(chat_id=chat_id, text="❌ Новых пользователей за последние 7 дней не найдено.")
            return
        
        message = f"🆕 НОВЫЕ ПОЛЬЗОВАТЕЛИ (за 7 дней): {len(users)}\n\n"
        
        for i, user in enumerate(users, 1):
            status = "🚫 ЗАБЛОКИРОВАН" if user['is_blocked'] else "✅ АКТИВЕН"
            username = f"@{user['username']}" if user['username'] else "Без username"
            name = f"{user['first_name']} {user['last_name']}".strip() if user['last_name'] else user['first_name']
            
            message += f"{i}. {username}\n"
            message += f"   Имя: {name}\n"
            message += f"   ID: {user['user_id']}\n"
            message += f"   Статус: {status}\n"
            message += f"   Регистрация: {user['first_seen']}\n\n"
        
        await context.bot.send_message(chat_id=chat_id, text=message)
    
    async def _handle_block_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Блокирует пользователя"""
        chat_id = update.effective_chat.id
        message_text = update.message.text.strip()
        
        # Извлекаем user_id из команды
        parts = message_text.split()
        if len(parts) < 2:
            await context.bot.send_message(chat_id=chat_id, text="❌ Использование: /block <user_id>")
            return
        
        try:
            user_id = int(parts[1])
        except ValueError:
            await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат user_id. Используйте число.")
            return
        
        if self.db.block_user(user_id):
            await context.bot.send_message(chat_id=chat_id, text=f"🚫 Пользователь {user_id} заблокирован.")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка блокировки пользователя {user_id}.")
    
    async def _handle_unblock_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Разблокирует пользователя"""
        chat_id = update.effective_chat.id
        message_text = update.message.text.strip()
        
        # Извлекаем user_id из команды
        parts = message_text.split()
        if len(parts) < 2:
            await context.bot.send_message(chat_id=chat_id, text="❌ Использование: /unblock <user_id>")
            return
        
        try:
            user_id = int(parts[1])
        except ValueError:
            await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат user_id. Используйте число.")
            return
        
        if self.db.unblock_user(user_id):
            await context.bot.send_message(chat_id=chat_id, text=f"✅ Пользователь {user_id} разблокирован.")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка разблокировки пользователя {user_id}.")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка при обработке обновления: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка. Попробуйте позже."
            )


def main():
    """Основная функция запуска бота"""
    # Проверяем конфигурацию
    print("Проверяем конфигурацию...")
    
    if not BOT_TOKEN:
        print("ОШИБКА: BOT_TOKEN не настроен!")
        return
    
    print(f"BOT_TOKEN: {'*' * 10}{BOT_TOKEN[-4:]}")
    
    print("\nАдминские чаты:")
    for direction, chat_id in ADMIN_CHATS.items():
        status = "OK" if chat_id else "НЕ НАСТРОЕН"
        print(f"  {status} {direction}: {chat_id or 'Не настроен'}")
    
    if not any(ADMIN_CHATS.values()):
        print("ОШИБКА: Ни один админский чат не настроен!")
        return
    
    # Запускаем HTTP-сервер в отдельном потоке для Render.com
    health_thread = Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    print("\nЗапускаем бота...")
    
    bot = ApplicationBot()
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    # Обработчик команд (все чаты)
    application.add_handler(MessageHandler(
        filters.TEXT & filters.COMMAND, 
        bot.handle_text_message
    ))
    
    # Обработчик текстовых сообщений (не команды, все чаты)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        bot.handle_text_message
    ))
    
    # Обработчик ошибок
    application.add_error_handler(bot.error_handler)
    
    # Запускаем бота
    print("Бот запущен и готов к работе!")
    application.run_polling()


if __name__ == '__main__':
    main()
