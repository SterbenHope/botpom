#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import time
import asyncio
from typing import Dict, Any, Optional, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ContextTypes, filters
)

from config import BOT_TOKEN, ADMIN_CHATS, DIRECTIONS, APPLICATION_FORM_SEND, APPLICATION_FORM_RECEIVE, OWNER_CHAT_ID
from database import KPDatabase
from models.rate_limiter import RateLimiter
from handlers.user_handler import UserHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Константы для состояний и типов
class UserStates:
    WAITING_APPLICATION = 'waiting_application'

class FeedbackTypes:
    YES = 'yes'
    NO = 'no'

class CallbackPrefixes:
    OPERATION = 'operation_'
    DIRECTION = 'direction_'
    FEEDBACK = 'feedback_'
    FEEDBACK_SHORT = 'fb_'
    SEND_KP = 'send_kp_'
    KP_PAGE = 'kp_page_'

class ApplicationBot:
    def __init__(self) -> None:
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self.rate_limiter = RateLimiter(max_requests=15, time_window=60)
        self.user_applications: Dict[int, Dict[str, Any]] = {}
        self.admin_states: Dict[int, Dict[str, Any]] = {}
        self.db = KPDatabase()
        
        self.user_handler = UserHandler(self.db, self.user_states, self.user_applications)
    
        # Флаг для запуска ежедневной очистки
        self._cleanup_task = None
    
    def start_daily_cleanup(self) -> None:
        """Запускает ежедневную очистку БД (вызывается после запуска event loop)"""
        async def daily_cleanup():
            while True:
                try:
                    # Ждем до следующего дня в 02:00
                    await asyncio.sleep(3600)  # Проверяем каждый час
                    
                    current_hour = time.localtime().tm_hour
                    if current_hour == 2:  # 02:00
                        logger.info("Starting daily database cleanup...")
                        cleanup_result = self.db.cleanup_old_data()
                        logger.info(f"Daily cleanup completed: {cleanup_result}")
                        
                        # Уведомляем владельца о очистке
                        if OWNER_CHAT_ID:
                            try:
                                from telegram import Bot
                                bot = Bot(token=BOT_TOKEN)
                                await bot.send_message(
                                    chat_id=OWNER_CHAT_ID,
                                    text=f"Ежедневная очистка БД завершена:\n"
                                         f"• Удалено уведомлений: {cleanup_result['notifications_deleted']}\n"
                                         f"• Удалено отрицательных отзывов: {cleanup_result['feedback_no_deleted']}"
                                )
                            except Exception as e:
                                logger.error(f"Error sending cleanup notification: {e}")
                        
                        # Ждем 23 часа до следующей проверки
                        await asyncio.sleep(82800)
                        
                except Exception as e:
                    logger.error(f"Error in daily cleanup: {e}")
                    await asyncio.sleep(3600)  # Ждем час при ошибке
        
        # Запускаем задачу в фоне
        self._cleanup_task = asyncio.create_task(daily_cleanup())
    
    def _validate_user_state(self, user_id: int, expected_state: str = UserStates.WAITING_APPLICATION) -> bool:
        """Проверяет состояние пользователя"""
        return (user_id in self.user_states and 
                self.user_states[user_id].get('state') == expected_state)
    
    async def _send_error_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
        """Отправляет сообщение об ошибке"""
        if update.message:
            await update.message.reply_text(message)
        elif update.callback_query:
            await update.callback_query.answer(message, show_alert=True)
    
    def _parse_callback_data(self, callback_data: str, expected_parts: int) -> Optional[List[str]]:
        """Парсит callback_data и возвращает части или None при ошибке"""
        if not callback_data or len(callback_data) < 10:
            return None
        
        parts = callback_data.split('_')
        if len(parts) < expected_parts:
            return None
        
        return parts
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /start"""
        await self.user_handler.start(update, context)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        elif query.data.startswith(CallbackPrefixes.OPERATION):
            await self.user_handler.handle_operation_selection(update, context)
        elif query.data.startswith(CallbackPrefixes.DIRECTION):
            await self.user_handler.handle_direction_selection(update, context)
        elif query.data.startswith(CallbackPrefixes.FEEDBACK) or query.data.startswith(CallbackPrefixes.FEEDBACK_SHORT):
            await self.handle_feedback(update, context)
        elif query.data.startswith(CallbackPrefixes.SEND_KP):
            await self.handle_send_kp(update, context)
        elif query.data.startswith(CallbackPrefixes.KP_PAGE):
            await self.handle_kp_pagination(update, context)
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            logger.info(f"This is admin chat {chat_id}")
            
            # Проверяем состояние админа для добавления/редактирования КП
            if user_id in self.admin_states:
                await self.handle_admin_kp_state(update, context)
                return
            
            # Проверяем ответ на сообщение бота
            if update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot:
                logger.info(f"Admin reply to bot message - processing for forwarding")
                await self.handle_admin_response(update, context)
                return
        
            # Любое другое сообщение в админском чате игнорируем
            return
        
        # Обработка заявок от пользователей
        await self.process_application(update, context)
    
    async def process_application(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает заявку пользователя"""
        user_id = update.effective_user.id
        
        # Проверяем состояние пользователя
        if not self._validate_user_state(user_id):
            await self._send_error_message(
                update, context, "❌ Неожиданное состояние. Начните заново командой /start"
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
        
        # Парсим заявку на строки
        app_lines = application_text.split('\n')
        
        # Формируем детальное сообщение
        if len(app_lines) >= 9:
            description = app_lines[8] if len(app_lines) > 8 else "Не указано"
            detailed_app = f"""
🏢 Фирма: {app_lines[0] if len(app_lines) > 0 else ''}
🔢 ИНН: {app_lines[1] if len(app_lines) > 1 else ''}
🏦 Банк: {app_lines[2] if len(app_lines) > 2 else ''}
📊 НДС: {app_lines[3] if len(app_lines) > 3 else ''}%
💰 Категория: {app_lines[4] if len(app_lines) > 4 else ''}
📝 Назначение: {app_lines[5] if len(app_lines) > 5 else ''}
💵 Сумма: {app_lines[6] if len(app_lines) > 6 else ''} руб.
🔧 Тип: {app_lines[7] if len(app_lines) > 7 else ''}
📄 Описание: {description}
"""
        else:
            detailed_app = application_text
        
        # Сохраняем заявку в БД
        application_data = {
            'user_id': user_id,
            'direction': direction,
            'company_name': app_lines[0] if len(app_lines) > 0 else '',
            'inn': app_lines[1] if len(app_lines) > 1 else '',
            'bank': app_lines[2] if len(app_lines) > 2 else '',
            'nds_rate': int(app_lines[3]) if len(app_lines) > 3 and app_lines[3].isdigit() else 0,
            'category': app_lines[4] if len(app_lines) > 4 else '',
            'payment_purpose': app_lines[5] if len(app_lines) > 5 else '',
            'amount': int(app_lines[6]) if len(app_lines) > 6 and app_lines[6].isdigit() else 0,
            'equipment_type': app_lines[7] if len(app_lines) > 7 else '',
            'description': description,
            'operation_type': operation
        }
        
        app_id = self.db.add_client_application(application_data)
        
        # Отправляем заявку админу
        admin_message = f"""
📋 НОВАЯ ЗАЯВКА (ID: {app_id})

💸 <b>{operation_text}</b>

👤 Пользователь: @{update.effective_user.username or update.effective_user.first_name}
🆔 ID: {user_id}
🏗️ Направление: {DIRECTIONS[direction]}

{detailed_app}

⏰ Время: {time.strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        try:
            # Получаем готовые КП для данного направления (первые 5)
            ready_offers = self.db.get_ready_offers_by_direction(direction, limit=5, offset=0)
            
            # Создаем кнопки с готовыми КП
            keyboard = []
            for offer in ready_offers:
                button_text = f"{offer['company_name'][:20]} | {offer['payment_purpose'][:15]}"
                callback_data = f"send_kp_{offer['id']}_{user_id}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            # Добавляем кнопки пагинации если КП больше 5
            if len(ready_offers) == 5:
                keyboard.append([
                    InlineKeyboardButton("➡️ Далее", callback_data=f"kp_page_1_{direction}_{user_id}")
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Отправляем сообщение в админский чат
            # Убираем reply_to_message_id, так как он вызывает ошибки
            admin_msg = await context.bot.send_message(
                chat_id=ADMIN_CHATS[direction],
                text=admin_message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
            # Сохраняем информацию об админском сообщении в БД
            if app_id > 0:
                self.db.update_client_application_admin_info(
                    app_id, admin_msg.message_id, str(ADMIN_CHATS[direction])
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
            logger.error(f"Error sending application: {e}")
            await update.message.reply_text(
                "❌ Ошибка отправки заявки. Попробуйте позже."
            )
    
    async def handle_admin_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает ответ администратора"""
        reply_to_message = update.message.reply_to_message
        
        if not reply_to_message or not reply_to_message.from_user.is_bot:
            return
        
        # Извлекаем user_id из текста сообщения бота
        bot_message_text = reply_to_message.text
        if "ID: " not in bot_message_text:
            return
        
        try:
            # Ищем строку с ID пользователя (не ID заявки)
            user_id_line = None
            for line in bot_message_text.split('\n'):
                if '🆔 ID:' in line and 'НОВАЯ ЗАЯВКА' not in line:
                    user_id_line = line
                    break
            
            if not user_id_line:
                logger.error("Could not find user ID in message")
                return
                
            # Извлекаем user_id из строки "🆔 ID: {user_id}"
            id_part = user_id_line.split('🆔 ID:')[1].strip()
            user_id = int(id_part)
            
            # Извлекаем направление из сообщения
            direction_line = [line for line in bot_message_text.split('\n') if 'Направление:' in line]
            direction = None
            if direction_line:
                direction_text = direction_line[0].split('Направление:')[1].strip()
                # Находим ключ направления по названию
                for dir_key, dir_name in DIRECTIONS.items():
                    if dir_name == direction_text:
                        direction = dir_key
                        break
            
            # Если направление не найдено, определяем его по админскому чату
            if not direction:
                admin_chat_id = update.effective_chat.id
                for dir_key, chat_id_config in ADMIN_CHATS.items():
                    if str(admin_chat_id) == str(chat_id_config):
                        direction = dir_key
                        break
            
            # Получаем admin_chat_id текущего чата
            admin_chat_id = update.effective_chat.id
            
            # Генерируем уникальный ID для этого КП (используем message_id)
            kp_message_id = update.message.message_id
            kp_id = f"{admin_chat_id}_{kp_message_id}"
            
            # Получаем информацию о заявке клиента из сообщения
            client_info = "Неизвестная заявка"
            if reply_to_message and reply_to_message.text:
                lines = reply_to_message.text.split('\n')
                
                # Ищем название фирмы клиента в сообщении
                for i, line in enumerate(lines):
                    # Ищем строку с фирмой (обычно это строка после "НОВАЯ ЗАЯВКА")
                    if 'Фирма:' in line:
                        client_info = line.split('Фирма:')[1].strip()
                        break
                    elif 'НОВАЯ ЗАЯВКА' in line and i + 1 < len(lines):
                        # Берем следующую строку после "НОВАЯ ЗАЯВКА" - это обычно название фирмы
                        next_line = lines[i + 1].strip()
                        if next_line and not next_line.startswith('💸') and not next_line.startswith('👤'):
                            client_info = next_line
                            break
                
                # Очищаем от проблемных символов для callback_data
                client_info = client_info.replace(' ', '_').replace('"', '').replace("'", '').replace('\n', '').replace('\r', '').replace('📋', '').replace('🏢', '').strip()[:15]
                logger.info(f"Parsed client info: {client_info}")
            
            # Создаем inline кнопки для обратной связи (краткий формат)
            client_short = client_info[:5] if client_info else "unk"
            direction_for_feedback = direction if direction else "unknown"
            keyboard = [
                [
                    InlineKeyboardButton("✅ Счёт подходит", callback_data=f"fb_yes_{admin_chat_id}_{kp_message_id}_none_{direction_for_feedback}_{client_short}"),
                    InlineKeyboardButton("❌ Счёт не подходит", callback_data=f"fb_no_{admin_chat_id}_{kp_message_id}_none_{direction_for_feedback}_{client_short}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Отправляем ответ пользователю с кнопками
            response_text = update.message.text
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=f"💬 Коммерческое предложение от администратора:\n\n{response_text}",
                    reply_markup=reply_markup
                )
            except Exception as send_error:
                logger.error(f"Error sending message to user {user_id}: {send_error}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Не удалось отправить сообщение пользователю {user_id}. Возможно, пользователь заблокировал бота."
                )
                return
            
            # Подтверждаем админу
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ КП отправлено пользователю {user_id}"
            )
            
        except Exception as e:
            logger.error(f"Error processing admin response: {e}")
    
    async def handle_send_kp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Отправляет выбранное КП клиенту"""
        query = update.callback_query
        
        try:
            # Парсим callback_data: send_kp_{offer_id}_{user_id}
            parts = self._parse_callback_data(query.data, 4)
            if not parts:
                await query.answer("❌ Неверный формат данных", show_alert=True)
                return
            offer_id = int(parts[2])
            client_user_id = int(parts[3])
            
            # Получаем КП из БД
            offer = self.db.get_ready_offer_by_id(offer_id)
            
            if not offer:
                await query.answer("❌ КП не найдено", show_alert=True)
                return
            
            # Формируем текст КП для клиента
            commission_text = f"{offer.get('commission', 0)}%" if offer.get('commission', 0) > 0 else "0%"
            kp_text = f"""
💼 Коммерческое предложение

🏢 Фирма: {offer['company_name']}
🔢 ИНН: {offer['inn']}
🏦 Банк: {offer['bank']}
📝 Назначение платежа: {offer['payment_purpose']}
💰 Минимальная сумма: {offer['min_amount']:,} руб.
💵 Максимальная сумма: {offer['max_amount']:,} руб.
📊 Комиссия: {commission_text}
🏗️ Направление: {DIRECTIONS.get(offer['direction'], 'Не указано')}
            """
            
            # Получаем admin_chat_id и message_id для обратной связи
            admin_chat_id = query.message.chat_id
            
            # Получаем информацию о заявке клиента из БД
            client_info = "Неизвестная заявка"
            direction_from_db = offer['direction']
            
            # Пытаемся найти заявку клиента по admin_message_id
            # Сначала ищем в reply_to_message
            admin_message_id = None
            app_data = None
            
            try:
                # Если есть reply_to_message, используем его ID
                if query.message.reply_to_message:
                    admin_message_id = query.message.reply_to_message.message_id
                    app_data = self.db.get_client_application_by_admin_message(admin_message_id, str(admin_chat_id))
                
                # Если не найдено, пробуем найти по ID заявки в тексте
                if not app_data and query.message.reply_to_message:
                    original_message = query.message.reply_to_message
                    if original_message and original_message.text:
                        lines = original_message.text.split('\n')
                        for line in lines:
                            if 'НОВАЯ ЗАЯВКА (ID:' in line:
                                try:
                                    # Извлекаем ID из строки типа "НОВАЯ ЗАЯВКА (ID: 3)"
                                    id_part = line.split('ID:')[1].strip()
                                    app_id = int(id_part.split(')')[0].strip())
                                    app_data = self.db.get_client_application_by_id(app_id)
                                    if app_data:
                                        admin_message_id = original_message.message_id
                                        break
                                except (ValueError, IndexError):
                                    pass
                
                if app_data:
                    client_info = app_data['company_name']
                    direction_from_db = app_data['direction']
                    logger.info(f"Found application: {app_data['company_name']}, direction: {app_data['direction']}")
                else:
                    # Fallback: ищем в тексте сообщения
                    original_message = query.message.reply_to_message
                    if original_message and original_message.text:
                        lines = original_message.text.split('\n')
                        for line in lines:
                            if 'НОВАЯ ЗАЯВКА (ID:' in line:
                                try:
                                    # Извлекаем ID из строки типа "НОВАЯ ЗАЯВКА (ID: 3)"
                                    id_part = line.split('ID:')[1].strip()
                                    app_id = int(id_part.split(')')[0].strip())
                                    app_data = self.db.get_client_application_by_id(app_id)
                                    if app_data:
                                        client_info = app_data['company_name']
                                        direction_from_db = app_data['direction']
                                        break
                                except (ValueError, IndexError):
                                    pass
                            
                            # Fallback: ищем название фирмы в тексте
                            if '🏢 Фирма:' in line:
                                client_info = line.split('🏢 Фирма:')[1].strip()
                                break
                            elif 'Фирма:' in line and '🏢' in line:
                                client_info = line.split('Фирма:')[1].strip()
                                break
            except Exception as e:
                logger.error(f"Error getting application data: {e}")
            
            # Очищаем от проблемных символов для callback_data и ограничиваем длину
            client_info = client_info.replace(' ', '_').replace('"', '').replace("'", '').replace('\n', '').replace('\r', '')[:8]
            
            # Создаем кнопки обратной связи (краткий формат)
            # Сокращаем client_info до минимума
            client_short = client_info[:5] if client_info else "unk"
            
            # Используем правильный admin_message_id или fallback на текущий message_id
            kp_message_id = admin_message_id if admin_message_id else query.message.message_id
            
            # Логируем callback_data для отладки
            callback_data_yes = f"fb_yes_{admin_chat_id}_{kp_message_id}_{offer_id}_{direction_from_db}_{client_short}"
            callback_data_no = f"fb_no_{admin_chat_id}_{kp_message_id}_{offer_id}_{direction_from_db}_{client_short}"
            logger.info(f"Creating callback_data: yes={callback_data_yes}")
            logger.info(f"Creating callback_data: no={callback_data_no}")
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Счёт подходит", callback_data=callback_data_yes),
                    InlineKeyboardButton("❌ Счёт не подходит", callback_data=callback_data_no)
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Отправляем КП клиенту
            await context.bot.send_message(
                chat_id=client_user_id,
                text=kp_text,
                reply_markup=reply_markup
            )
            
            # Подтверждаем админу
            await query.answer("✅ КП отправлено клиенту")
            
            # Отправляем подтверждение в админ чат (НЕ убираем кнопки, чтобы можно было отправлять повторно)
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text=f"✅ КП '{offer['company_name']}' отправлено пользователю {client_user_id}"
            )
            
        except Exception as e:
            logger.error(f"Error sending offer: {e}")
            await query.answer("❌ Ошибка отправки КП", show_alert=True)
    
    async def handle_kp_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает пагинацию КП"""
        query = update.callback_query
        
        try:
            # Парсим callback_data: kp_page_{page}_{direction}_{user_id}
            parts = self._parse_callback_data(query.data, 5)
            if not parts:
                await query.answer("❌ Неверный формат данных", show_alert=True)
                return
            page = int(parts[2])
            direction = parts[3]
            client_user_id = int(parts[4])
            
            # Получаем КП для данной страницы
            offset = page * 5
            ready_offers = self.db.get_ready_offers_by_direction(direction, limit=5, offset=offset)
            
            if not ready_offers:
                await query.answer("Больше нет КП", show_alert=True)
                return
            
            # Создаем кнопки с КП
            keyboard = []
            for offer in ready_offers:
                button_text = f"{offer['company_name'][:20]} | {offer['payment_purpose'][:15]}"
                callback_data = f"send_kp_{offer['id']}_{client_user_id}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            # Добавляем кнопки навигации
            nav_buttons = []
            if page > 0:
                nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"kp_page_{page-1}_{direction}_{client_user_id}"))
            if len(ready_offers) == 5:
                nav_buttons.append(InlineKeyboardButton("➡️ Далее", callback_data=f"kp_page_{page+1}_{direction}_{client_user_id}"))
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Обновляем кнопки
            await query.edit_message_reply_markup(reply_markup=reply_markup)
            await query.answer(f"Страница {page + 1}")
            
        except Exception as e:
            logger.error(f"Error in offer pagination: {e}")
            await query.answer("❌ Ошибка", show_alert=True)
    
    async def handle_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает обратную связь от клиента по КП"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Отвечаем на callback сразу, чтобы избежать таймаута
        await query.answer()
        
        # Логируем callback_data для отладки
        logger.info(f"Feedback callback_data: {query.data}")
        
        # Парсим callback_data: fb_yes/no_{admin_chat_id}_{message_id}_{offer_id}_{direction}_{client_short}
        try:
            parts = self._parse_callback_data(query.data, 7)
            if not parts:
                await query.answer("❌ Неверный формат данных", show_alert=True)
                return
                
            feedback_type = parts[1]  # yes или no
            admin_chat_id = parts[2]
            kp_message_id = parts[3]
            offer_id = parts[4]
            direction = parts[5]
            client_short = parts[6] if len(parts) > 6 else "unk"
            
            # Логируем распарсенные данные
            logger.info(f"Parsed: admin_chat_id={admin_chat_id}, kp_message_id={kp_message_id}, offer_id={offer_id}, direction={direction}, client_short={client_short}")
            
            # Получаем полную информацию о клиенте из БД
            client_info = "Неизвестная заявка"
            try:
                # Пытаемся найти заявку по admin_message_id
                app_data = self.db.get_client_application_by_admin_message(int(kp_message_id), admin_chat_id)
                if app_data:
                    client_info = app_data['company_name']
                    direction = app_data['direction']  # Используем направление из БД
                    logger.info(f"Found application in DB: {app_data['company_name']}, direction: {app_data['direction']}")
                else:
                    logger.warning(f"Заявка не найдена в БД для admin_message_id={kp_message_id}, admin_chat_id={admin_chat_id}")
                    # Fallback на сокращенное имя
                    client_info = client_short
            except Exception as e:
                logger.error(f"Error searching application in DB: {e}")
                client_info = client_short
            
            # Если направление неизвестно, определяем его по админскому чату
            if direction == "unknown" or not direction:
                for dir_key, chat_id_config in ADMIN_CHATS.items():
                    if chat_id_config and str(admin_chat_id) == str(chat_id_config):
                        direction = dir_key
                        break
                # Если все еще не найдено, логируем для отладки
                if direction == "unknown" or not direction:
                    logger.error(f"Direction not found for admin_chat_id: {admin_chat_id}, ADMIN_CHATS: {ADMIN_CHATS}")
            
            kp_id = f"{admin_chat_id}_{kp_message_id}"
            if offer_id:
                kp_id += f"_{offer_id}"
            
            # Сохраняем feedback в БД
            feedback_data = {
                'user_id': user_id,
                'offer_id': kp_id,
                'feedback_type': feedback_type,
                'direction': direction or 'unknown'
            }
            self.db.add_feedback(feedback_data)
            
            # Обновляем сообщение с КП, показываем выбор пользователя
            if feedback_type == 'yes':
                feedback_emoji = "✅"
                feedback_text = "Вы выбрали: Счёт подходит"
                admin_feedback_text = "✅ КЛИЕНТ ПРИНЯЛ КП"
            else:
                feedback_emoji = "❌"
                feedback_text = "Вы выбрали: Счёт не подходит"
                admin_feedback_text = "❌ КЛИЕНТ ОТКЛОНИЛ КП"
            
            # Редактируем сообщение клиента, убираем кнопки (только если еще не обработано)
            try:
                await query.edit_message_text(
                    text=f"{query.message.text}\n\n{feedback_emoji} {feedback_text}"
                )
            except Exception as edit_error:
                if "Message is not modified" in str(edit_error):
                    logger.info("Message already processed, skipping edit")
                else:
                    raise edit_error
            
            # Отправляем результат в админ чат
            username = query.from_user.username or query.from_user.first_name
            
            # Определяем тип КП
            kp_type = "Ручное КП от админа"
            if offer_id and offer_id != "none" and offer_id.isdigit():
                try:
                    # Получаем данные КП из БД для отображения названия
                    offer_data = self.db.get_ready_offer_by_id(int(offer_id))
                    if offer_data:
                        # Формируем название как "компания | банк"
                        company_name = offer_data.get('company_name', 'Неизвестная компания')
                        bank_name = offer_data.get('bank', 'Неизвестный банк')
                        kp_type = f"Готовое КП: {company_name} | {bank_name}"
                    else:
                        kp_type = f"Готовое КП (ID: {offer_id})"
                except Exception as e:
                    logger.error(f"Error getting offer data: {e}")
                    kp_type = f"Готовое КП (ID: {offer_id})"
            
            # Определяем направление по admin_chat_id
            admin_direction = None
            for dir_key, chat_id in ADMIN_CHATS.items():
                if str(admin_chat_id) == str(chat_id):
                    admin_direction = dir_key
                    break
            
            # Используем направление из БД, если есть, иначе из admin_chat_id
            final_direction = direction if direction and direction != "unknown" else admin_direction
            
            admin_notification = f"""
{admin_feedback_text}

👤 Пользователь: @{username}
🆔 ID: {user_id}
🏢 Фирма клиента: {client_info}
🏗️ Направление: {DIRECTIONS.get(final_direction, 'Неизвестно')}
📋 Тип КП: {kp_type}
📩 ID сообщения с КП: {kp_message_id}

⏰ Время: {time.strftime('%Y-%m-%d %H:%M:%S')}
            """
            
            await context.bot.send_message(
                chat_id=int(admin_chat_id),
                text=admin_notification
            )
            
            # Отправляем уведомление владельцу
            if OWNER_CHAT_ID:
                owner_notification = f"""
📊 УВЕДОМЛЕНИЕ ВЛАДЕЛЬЦУ

{admin_feedback_text}
👤 Пользователь: @{username}
🆔 ID: {user_id}
🏢 Фирма клиента: {client_info}
🏗️ Направление: {DIRECTIONS.get(final_direction, 'Неизвестно')}
📋 Тип КП: {kp_type}
📩 ID сообщения с КП: {kp_message_id}
⏰ Время: {time.strftime('%Y-%m-%d %H:%M:%S')}
                """
                
                try:
                    await context.bot.send_message(
                        chat_id=OWNER_CHAT_ID,
                        text=owner_notification,
                        parse_mode='HTML'
                    )
                    
                    # Сохраняем уведомление в БД
                    notification_data = {
                        'notification_type': 'feedback',
                        'user_id': user_id,
                        'application_id': None,  # TODO: связать с заявкой
                        'offer_id': offer_id,
                        'direction': direction,
                        'company_name': client_info,
                        'admin_chat_id': admin_chat_id,
                        'admin_user_id': None,  # TODO: получить ID админа
                        'feedback_type': feedback_type,
                        'message': owner_notification
                    }
                    self.db.add_owner_notification(notification_data)
                    
                except Exception as e:
                    logger.error(f"Error sending owner notification: {e}")
                    # Если группа мигрировала, попробуем извлечь новый ID из ошибки
                    if "migrated to supergroup" in str(e):
                        try:
                            # Извлекаем новый ID из сообщения об ошибке
                            error_msg = str(e)
                            if "New chat id:" in error_msg:
                                new_chat_id = error_msg.split("New chat id:")[1].strip()
                                await context.bot.send_message(
                                    chat_id=new_chat_id,
                                    text=owner_notification,
                                    parse_mode='HTML'
                                )
                                logger.info(f"Notification sent to new supergroup: {new_chat_id}")
                            else:
                                logger.error(f"Failed to extract new ID from error: {error_msg}")
                        except Exception as e2:
                            logger.error(f"Error sending to new supergroup: {e2}")
            
            # query.answer() уже вызван в начале функции
            
        except Exception as e:
            logger.error(f"Error processing feedback: {e}")
            await query.answer("❌ Ошибка обработки ответа", show_alert=True)
    
    async def handle_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        
        logger.info(f"Processing admin command: {message_text} in chat {chat_id}")
        
        # Обработка команд
        if message_text.startswith('/help_admin'):
            help_text = """
🔧 ДОСТУПНЫЕ КОМАНДЫ АДМИНА:

👥 /users - Список всех пользователей
🆕 /new_users - Новые пользователи (за 7 дней)
🚫 /block <user_id> - Заблокировать пользователя
✅ /unblock <user_id> - Разблокировать пользователя

📋 УПРАВЛЕНИЕ КП:
➕ /add_kp - Добавить новое КП
📝 /list_kp - Список всех КП
✏️ /edit_kp <id> - Редактировать КП
🗑️ /delete_kp <id> - Удалить КП

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
        
        elif message_text.startswith('/add_kp'):
            await self._handle_add_kp_command(update, context, admin_direction)
        
        elif message_text.startswith('/list_kp'):
            await self._handle_list_kp_command(update, context, admin_direction)
        
        elif message_text.startswith('/edit_kp'):
            await self._handle_edit_kp_command(update, context, admin_direction)
        
        elif message_text.startswith('/delete_kp'):
            await self._handle_delete_kp_command(update, context)
        
        elif message_text.startswith('/stats'):
            await self._handle_stats_command(update, context)
        elif message_text.startswith('/db_stats'):
            await self._handle_db_stats_command(update, context)
        elif message_text.startswith('/cleanup_db'):
            await self._handle_cleanup_db_command(update, context)
    
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
    
    async def handle_admin_kp_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обрабатывает состояния добавления/редактирования КП"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        
        if user_id not in self.admin_states:
            return
        
        admin_state = self.admin_states[user_id]
        state = admin_state['state']
        kp_data = admin_state['kp_data']
        
        # Обработка добавления КП
        if state == 'add_kp_company_name':
            kp_data['company_name'] = text
            admin_state['state'] = 'add_kp_inn'
            await context.bot.send_message(chat_id=chat_id, text="2️⃣ Введите ИНН:")
        
        elif state == 'add_kp_inn':
            kp_data['inn'] = text
            admin_state['state'] = 'add_kp_payment_purpose'
            await context.bot.send_message(chat_id=chat_id, text="3️⃣ Введите назначение платежа:")
        
        elif state == 'add_kp_payment_purpose':
            kp_data['payment_purpose'] = text
            admin_state['state'] = 'add_kp_bank'
            await context.bot.send_message(chat_id=chat_id, text="4️⃣ Введите название банка:")
        
        elif state == 'add_kp_bank':
            kp_data['bank'] = text
            admin_state['state'] = 'add_kp_min_amount'
            await context.bot.send_message(chat_id=chat_id, text="5️⃣ Введите минимальную сумму (только число):")
        
        elif state == 'add_kp_min_amount':
            try:
                kp_data['min_amount'] = int(text)
                admin_state['state'] = 'add_kp_max_amount'
                await context.bot.send_message(chat_id=chat_id, text="6️⃣ Введите максимальную сумму (только число):")
            except ValueError:
                await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат. Введите число:")
        
        elif state == 'add_kp_max_amount':
            try:
                kp_data['max_amount'] = int(text)
                admin_state['state'] = 'add_kp_commission'
                await context.bot.send_message(chat_id=chat_id, text="7️⃣ Введите комиссию (только число, например: 2.5):")
            except ValueError:
                await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат. Введите число:")
        
        elif state == 'add_kp_commission':
            try:
                kp_data['commission'] = float(text)
                kp_data['direction'] = admin_state['direction']
                
                # Сохраняем КП в БД
                kp_id = self.db.add_ready_offer(kp_data)
                
                if kp_id:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ КП успешно добавлено! (ID: {kp_id})\n\n"
                             f"🏢 Фирма: {kp_data['company_name']}\n"
                             f"🔢 ИНН: {kp_data['inn']}\n"
                             f"🏦 Банк: {kp_data['bank']}\n"
                             f"📝 Назначение: {kp_data['payment_purpose']}\n"
                             f"💰 Сумма: {kp_data['min_amount']:,} - {kp_data['max_amount']:,} руб.\n"
                             f"📊 Комиссия: {kp_data['commission']}%"
                    )
                else:
                    await context.bot.send_message(chat_id=chat_id, text="❌ Ошибка сохранения КП")
                
                # Очищаем состояние
                del self.admin_states[user_id]
                
            except ValueError:
                await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат. Введите число (например: 2.5):")
        
        # Обработка редактирования КП
        elif state == 'edit_kp_company_name':
            if text != '-':
                kp_data['company_name'] = text
            admin_state['state'] = 'edit_kp_inn'
            
            current_offer = self.db.get_ready_offer_by_id(admin_state['kp_id'])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"2️⃣ Введите новый ИНН (текущий: {current_offer['inn']}) или '-':"
            )
        
        elif state == 'edit_kp_inn':
            if text != '-':
                kp_data['inn'] = text
            admin_state['state'] = 'edit_kp_payment_purpose'
            
            current_offer = self.db.get_ready_offer_by_id(admin_state['kp_id'])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"3️⃣ Введите новое назначение платежа (текущее: {current_offer['payment_purpose']}) или '-':"
            )
        
        elif state == 'edit_kp_payment_purpose':
            if text != '-':
                kp_data['payment_purpose'] = text
            admin_state['state'] = 'edit_kp_bank'
            
            current_offer = self.db.get_ready_offer_by_id(admin_state['kp_id'])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"4️⃣ Введите новое название банка (текущий: {current_offer['bank']}) или '-':"
            )
        
        elif state == 'edit_kp_bank':
            if text != '-':
                kp_data['bank'] = text
            admin_state['state'] = 'edit_kp_min_amount'
            
            current_offer = self.db.get_ready_offer_by_id(admin_state['kp_id'])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"5️⃣ Введите новую минимальную сумму (текущая: {current_offer['min_amount']:,}) или '-':"
            )
        
        elif state == 'edit_kp_min_amount':
            if text != '-':
                try:
                    kp_data['min_amount'] = int(text)
                except ValueError:
                    await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат. Введите число или '-':")
                    return
            
            admin_state['state'] = 'edit_kp_max_amount'
            current_offer = self.db.get_ready_offer_by_id(admin_state['kp_id'])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"6️⃣ Введите новую максимальную сумму (текущая: {current_offer['max_amount']:,}) или '-':"
            )
        
        elif state == 'edit_kp_max_amount':
            if text != '-':
                try:
                    kp_data['max_amount'] = int(text)
                except ValueError:
                    await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат. Введите число или '-':")
                    return
            
            admin_state['state'] = 'edit_kp_commission'
            await context.bot.send_message(chat_id=chat_id, text="7️⃣ Введите комиссию (только число, например: 2.5) или '-' чтобы оставить текущую:")
        
        elif state == 'edit_kp_commission':
            if text != '-':
                try:
                    kp_data['commission'] = float(text)
                except ValueError:
                    await context.bot.send_message(chat_id=chat_id, text="❌ Неверный формат. Введите число (например: 2.5) или '-':")
                    return
            
            # Получаем текущие данные КП
            current_offer = self.db.get_ready_offer_by_id(admin_state['kp_id'])
            
            # Заполняем недостающие поля из текущих данных
            final_data = {
                'company_name': kp_data.get('company_name', current_offer['company_name']),
                'inn': kp_data.get('inn', current_offer['inn']),
                'payment_purpose': kp_data.get('payment_purpose', current_offer['payment_purpose']),
                'bank': kp_data.get('bank', current_offer['bank']),
                'min_amount': kp_data.get('min_amount', current_offer['min_amount']),
                'max_amount': kp_data.get('max_amount', current_offer['max_amount']),
                'commission': kp_data.get('commission', current_offer.get('commission', 0.0)),
                'direction': admin_state['direction']
            }
            
            # Обновляем КП в БД
            if self.db.update_ready_offer(admin_state['kp_id'], final_data):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ КП успешно обновлено! (ID: {admin_state['kp_id']})\n\n"
                         f"🏢 Фирма: {final_data['company_name']}\n"
                         f"🔢 ИНН: {final_data['inn']}\n"
                         f"🏦 Банк: {final_data['bank']}\n"
                         f"📝 Назначение: {final_data['payment_purpose']}\n"
                         f"💰 Сумма: {final_data['min_amount']:,} - {final_data['max_amount']:,} руб.\n"
                         f"💸 Комиссия: {final_data['commission']}%"
                )
            else:
                await context.bot.send_message(chat_id=chat_id, text="❌ Ошибка обновления КП")
            
            # Очищаем состояние
            del self.admin_states[user_id]
    
    async def _handle_add_kp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, admin_direction: str):
        """Начинает процесс добавления КП"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Инициализируем состояние для добавления КП
        self.admin_states[user_id] = {
            'state': 'add_kp_company_name',
            'direction': admin_direction,
            'chat_id': chat_id,
            'kp_data': {}
        }
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="➕ Добавление нового КП\n\n1️⃣ Введите название фирмы:"
        )
    
    async def _handle_list_kp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, admin_direction: str):
        """Показывает список всех КП для данного направления"""
        chat_id = update.effective_chat.id
        
        offers = self.db.get_ready_offers_by_direction(admin_direction, limit=100)
        
        if not offers:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ КП не найдены для данного направления."
            )
            return
        
        message = f"📋 СПИСОК КП ({DIRECTIONS.get(admin_direction, 'Неизвестно')}):\n\n"
        
        for i, offer in enumerate(offers, 1):
            commission_text = f"{offer.get('commission', 0)}%" if offer.get('commission', 0) > 0 else "0%"
            message += f"{i}. ID: {offer['id']}\n"
            message += f"   🏢 {offer['company_name']}\n"
            message += f"   🔢 ИНН: {offer['inn']}\n"
            message += f"   🏦 Банк: {offer['bank']}\n"
            message += f"   📝 Назначение: {offer['payment_purpose']}\n"
            message += f"   💰 Сумма: {offer['min_amount']:,} - {offer['max_amount']:,} руб.\n"
            message += f"   📊 Комиссия: {commission_text}\n\n"
            
            # Telegram имеет лимит на длину сообщения
            if len(message) > 3500:
                await context.bot.send_message(chat_id=chat_id, text=message)
                message = ""
        
        if message:
            await context.bot.send_message(chat_id=chat_id, text=message)
    
    async def _handle_edit_kp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, admin_direction: str):
        """Редактирует КП"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        message_text = update.message.text.strip()
        
        parts = message_text.split()
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Использование: /edit_kp <id>\n\nНапример: /edit_kp 5"
            )
            return
        
        try:
            kp_id = int(parts[1])
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверный формат ID. Используйте число."
            )
            return
        
        # Получаем КП из БД
        offer = self.db.get_ready_offer_by_id(kp_id)
        
        if not offer:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ КП с ID {kp_id} не найдено."
            )
            return
        
        # Проверяем, что КП относится к этому направлению
        if offer['direction'] != admin_direction:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Это КП принадлежит другому направлению ({DIRECTIONS.get(offer['direction'])})."
            )
            return
        
        # Инициализируем состояние для редактирования КП
        self.admin_states[user_id] = {
            'state': 'edit_kp_company_name',
            'direction': admin_direction,
            'chat_id': chat_id,
            'kp_id': kp_id,
            'kp_data': {}
        }
        
        commission_text = f"{offer.get('commission', 0)}%" if offer.get('commission', 0) > 0 else "0%"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"""✏️ Редактирование КП (ID: {kp_id})

Текущие данные:
🏢 Фирма: {offer['company_name']}
🔢 ИНН: {offer['inn']}
🏦 Банк: {offer['bank']}
📝 Назначение: {offer['payment_purpose']}
💰 Мин. сумма: {offer['min_amount']:,} руб.
💵 Макс. сумма: {offer['max_amount']:,} руб.
📊 Комиссия: {commission_text}

1️⃣ Введите новое название фирмы (или отправьте '-' чтобы оставить текущее):"""
        )
    
    async def _handle_delete_kp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Удаляет КП"""
        chat_id = update.effective_chat.id
        message_text = update.message.text.strip()
        
        parts = message_text.split()
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Использование: /delete_kp <id>\n\nНапример: /delete_kp 5"
            )
            return
        
        try:
            kp_id = int(parts[1])
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверный формат ID. Используйте число."
            )
            return
        
        # Получаем КП перед удалением
        offer = self.db.get_ready_offer_by_id(kp_id)
        
        if not offer:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ КП с ID {kp_id} не найдено."
            )
            return
        
        # Показываем информацию о КП с комиссией
        commission_text = f"{offer.get('commission', 0)}%" if offer.get('commission', 0) > 0 else "0%"
        
        # Удаляем КП
        if self.db.delete_ready_offer(kp_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ КП успешно удалено!\n\n"
                     f"🏢 Фирма: {offer['company_name']}\n"
                     f"🔢 ИНН: {offer['inn']}\n"
                     f"🏦 Банк: {offer['bank']}\n"
                     f"📝 Назначение: {offer['payment_purpose']}\n"
                     f"💰 Сумма: {offer['min_amount']:,} - {offer['max_amount']:,} руб.\n"
                     f"📊 Комиссия: {commission_text}\n"
                     f"🆔 ID: {kp_id}"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Ошибка удаления КП {kp_id}."
            )
    
    async def _handle_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает статистику за день"""
        chat_id = update.effective_chat.id
        
        logger.info(f"Stats command called from chat {chat_id} (type: {type(chat_id)})")
        logger.info(f"OWNER_CHAT_ID: {OWNER_CHAT_ID} (type: {type(OWNER_CHAT_ID)})")
        logger.info(f"String comparison: '{chat_id}' != '{OWNER_CHAT_ID}' = {str(chat_id) != str(OWNER_CHAT_ID)}")
        
        # Проверяем, что это чат владельца
        if str(chat_id) != str(OWNER_CHAT_ID):
            logger.info(f"Access denied: chat_id={chat_id}, OWNER_CHAT_ID={OWNER_CHAT_ID}")
            await update.message.reply_text(f"❌ Доступ запрещен. Эта команда доступна только владельцу.\nВаш chat_id: {chat_id}\nОжидаемый: {OWNER_CHAT_ID}")
            return
        
        try:
            stats = self.db.get_daily_statistics()
            
            if not stats:
                await update.message.reply_text("❌ Ошибка получения статистики")
                return
            
            stats_text = f"""
📊 СТАТИСТИКА ЗА ДЕНЬ ({stats['date']})

📋 Заявок: {stats['applications_count']}

📈 Обратная связь:
✅ Принято: {stats['feedback_stats'].get('yes', 0)}
❌ Отклонено: {stats['feedback_stats'].get('no', 0)}

🏗️ По направлениям:"""
            
            for direction, count in stats['direction_stats'].items():
                direction_name = DIRECTIONS.get(direction, direction)
                stats_text += f"\n• {direction_name}: {count}"
            
            await update.message.reply_text(stats_text)
            
        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            await update.message.reply_text("❌ Ошибка получения статистики")
    
    async def _handle_db_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает статистику базы данных"""
        chat_id = update.effective_chat.id
        
        logger.info(f"DB stats command called from chat {chat_id} (type: {type(chat_id)})")
        logger.info(f"OWNER_CHAT_ID: {OWNER_CHAT_ID} (type: {type(OWNER_CHAT_ID)})")
        logger.info(f"String comparison: '{chat_id}' != '{OWNER_CHAT_ID}' = {str(chat_id) != str(OWNER_CHAT_ID)}")
        
        if str(chat_id) != str(OWNER_CHAT_ID):
            logger.info(f"Access denied: chat_id={chat_id}, OWNER_CHAT_ID={OWNER_CHAT_ID}")
            await update.message.reply_text(f"❌ Доступ запрещен. Эта команда доступна только владельцу.\nВаш chat_id: {chat_id}\nОжидаемый: {OWNER_CHAT_ID}")
            return
        
        try:
            stats = self.db.get_database_stats()
            if not stats:
                await update.message.reply_text("❌ Ошибка получения статистики БД")
                return
            
            stats_text = f"""📊 СТАТИСТИКА БАЗЫ ДАННЫХ

📋 Заявки клиентов: {stats['applications']:,}
✅ Положительные отзывы: {stats['feedback_yes']:,}
❌ Отрицательные отзывы: {stats['feedback_no']:,}
📢 Уведомления владельцу: {stats['notifications']:,}
👥 Пользователи: {stats['users']:,}

💾 Размер БД: {stats['db_size_mb']:.2f} MB

🔄 Для очистки старых данных используйте /cleanup_db"""

            await update.message.reply_text(stats_text)
            
        except Exception as e:
            logger.error(f"Error getting database statistics: {e}")
            await update.message.reply_text("❌ Ошибка получения статистики БД")
    
    async def _handle_cleanup_db_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Очищает старые данные из БД"""
        chat_id = update.effective_chat.id
        
        if str(chat_id) != str(OWNER_CHAT_ID):
            await update.message.reply_text(f"❌ Доступ запрещен. Эта команда доступна только владельцу.")
            return
        
        try:
            # Получаем статистику до очистки
            stats_before = self.db.get_database_stats()
            
            # Выполняем очистку
            cleanup_result = self.db.cleanup_old_data()
            
            # Получаем статистику после очистки
            stats_after = self.db.get_database_stats()
            
            cleanup_text = f"""🧹 ОЧИСТКА БД ЗАВЕРШЕНА

📊 ДО ОЧИСТКИ:
• Уведомления: {stats_before.get('notifications', 0):,}
• Отрицательные отзывы: {stats_before.get('feedback_no', 0):,}
• Размер БД: {stats_before.get('db_size_mb', 0):.2f} MB

🗑️ УДАЛЕНО:
• Уведомлений: {cleanup_result['notifications_deleted']:,}
• Отрицательных отзывов: {cleanup_result['feedback_no_deleted']:,}

📊 ПОСЛЕ ОЧИСТКИ:
• Уведомления: {stats_after.get('notifications', 0):,}
• Отрицательные отзывы: {stats_after.get('feedback_no', 0):,}
• Размер БД: {stats_after.get('db_size_mb', 0):.2f} MB

✅ Положительные отзывы сохранены для статистики"""

            await update.message.reply_text(cleanup_text)
            
        except Exception as e:
            logger.error(f"Error cleaning database: {e}")
            await update.message.reply_text("❌ Ошибка очистки БД")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик ошибок"""
        logger.error(f"Error processing update: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка. Попробуйте позже."
            )


def main() -> None:
    """Основная функция запуска бота"""
    # Проверяем конфигурацию
    print("Проверяем конфигурацию...")
    
    if not BOT_TOKEN:
        print("ОШИБКА: BOT_TOKEN не настроен!")
        return
    
    print(f"BOT_TOKEN: {'*' * 10}{BOT_TOKEN[-4:]}")
    
    print(f"OWNER_CHAT_ID: {OWNER_CHAT_ID or 'НЕ НАСТРОЕН'}")
    
    print("\nАдминские чаты:")
    for direction, chat_id in ADMIN_CHATS.items():
        status = "OK" if chat_id else "НЕ НАСТРОЕН"
        print(f"  {status} {direction}: {chat_id or 'Не настроен'}")
    
    if not any(ADMIN_CHATS.values()):
        print("ОШИБКА: Ни один админский чат не настроен!")
        return
    
    print("\nЗапускаем бота...")
    
    bot = ApplicationBot()
    
    # Оптимизируем БД при запуске
    print("Оптимизируем базу данных...")
    if bot.db.optimize_database():
        print("База данных оптимизирована")
    else:
        print("Ошибка оптимизации БД")
    
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
    
    # Запускаем ежедневную очистку после инициализации приложения
    async def post_init(application):
        bot.start_daily_cleanup()
    
    application.post_init = post_init
    
    # Запускаем бота
    print("Бот запущен и готов к работе!")
    application.run_polling()


if __name__ == '__main__':
    main()
