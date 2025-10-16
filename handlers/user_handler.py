#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import DIRECTIONS, APPLICATION_FORM_SEND, APPLICATION_FORM_RECEIVE

logger = logging.getLogger(__name__)

# Константы для состояний
class UserStates:
    WAITING_APPLICATION = 'waiting_application'


class UserHandler:
    """Обработчик пользовательских команд и действий"""
    
    def __init__(self, db, user_states, user_applications):
        self.db = db
        self.user_states = user_states
        self.user_applications = user_applications
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Очищаем предыдущее состояние пользователя
        if user_id in self.user_states:
            del self.user_states[user_id]
        
        welcome_message = """
🤖 Добро пожаловать в бот заявок!

Выберите тип операции:
        """
        
        # Создаем inline клавиатуру с выбором типа операции
        keyboard = [
            [InlineKeyboardButton("💸 Отправляете перевод", callback_data="operation_send")],
            [InlineKeyboardButton("💰 Получаете перевод", callback_data="operation_receive")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Если это callback query (кнопка "начать заново"), редактируем существующее сообщение
        if update.callback_query:
            await update.callback_query.edit_message_text(
                welcome_message,
                reply_markup=reply_markup
            )
        else:
            # Иначе это команда /start, отправляем новое сообщение
            await update.message.reply_text(
                welcome_message,
                reply_markup=reply_markup
            )
    
    async def handle_operation_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает выбор типа операции"""
        query = update.callback_query
        user_id = query.from_user.id
        
        operation = query.data.replace("operation_", "")
        logger.info(f"Выбрана операция: {operation}")
        
        # Сохраняем тип операции в состоянии
        if user_id not in self.user_states:
            self.user_states[user_id] = {}
        
        self.user_states[user_id]['operation'] = operation
        
        # Показываем выбор направления
        await self.show_direction_selection(update, context)
    
    async def show_direction_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает выбор направления"""
        query = update.callback_query
        await query.answer()
        
        # Создаем inline клавиатуру с направлениями
        keyboard = []
        for direction, description in DIRECTIONS.items():
            keyboard.append([InlineKeyboardButton(description, callback_data=f"direction_{direction}")])
        
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🏗️ Выберите направление:",
            reply_markup=reply_markup
        )
    
    async def handle_direction_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает выбор направления пользователем"""
        query = update.callback_query
        user_id = query.from_user.id
        
        direction = query.data.replace("direction_", "")
        logger.info(f"Обрабатываем выбор направления: {direction}")
        logger.info(f"Доступные направления: {list(DIRECTIONS.keys())}")
        logger.info(f"Направление {direction} в DIRECTIONS: {direction in DIRECTIONS}")
        
        if direction not in DIRECTIONS:
            await query.edit_message_text("❌ Неизвестное направление")
            return
        
        # Получаем операцию из существующего состояния
        operation = self.user_states.get(user_id, {}).get('operation', 'send')
        
        # Сохраняем состояние пользователя
        self.user_states[user_id] = {
            'state': UserStates.WAITING_APPLICATION,
            'direction': direction,
            'operation': operation,
            'timestamp': time.time()
        }
        
        # Определяем какая форма нужна
        if operation == 'send':
            form_text = APPLICATION_FORM_SEND
        else:
            form_text = APPLICATION_FORM_RECEIVE
        
        # Показываем форму заявки
        await query.edit_message_text(
            f"✅ Вы выбрали: {DIRECTIONS[direction]}\n\n{form_text}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Начать заново", callback_data="restart")
            ]])
        )
    
