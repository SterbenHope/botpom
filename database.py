#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль для работы с базой данных КП
"""

import sqlite3
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class KPDatabase:
    """Класс для работы с базой данных КП"""
    
    def __init__(self, db_path: str = 'kp_database.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализирует базу данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Таблица направлений
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS directions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Таблица назначений платежей
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payment_purposes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        direction_key TEXT NOT NULL,
                        purpose TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (direction_key) REFERENCES directions (key)
                    )
                ''')
                
                # Таблица готовых КП
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS ready_offers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        direction TEXT NOT NULL,
                        payment_purpose TEXT NOT NULL,
                        kp_name TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT,
                        price TEXT,
                        delivery TEXT,
                        warranty TEXT,
                        details TEXT,  -- JSON массив
                        nds_rates TEXT,  -- JSON массив ставок НДС
                        min_amount INTEGER DEFAULT 0,
                        max_amount INTEGER DEFAULT 0,
                        equipment_types TEXT,  -- JSON массив типов техники
                        payment_purposes TEXT,  -- JSON массив назначений
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Таблица обратной связи
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        offer_id TEXT NOT NULL,
                        feedback_type TEXT NOT NULL,  -- 'yes' или 'no'
                        direction TEXT NOT NULL,
                        payment_purpose TEXT,
                        amount INTEGER,
                        nds_rate INTEGER,
                        equipment_type TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Таблица пользователей
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        is_blocked INTEGER DEFAULT 0,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info("База данных инициализирована успешно")
                
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
    
    def add_direction(self, key: str, name: str) -> bool:
        """Добавляет новое направление"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT OR REPLACE INTO directions (key, name) VALUES (?, ?)',
                    (key, name)
                )
                conn.commit()
                logger.info(f"Направление {key}: {name} добавлено/обновлено")
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления направления: {e}")
            return False
    
    def add_payment_purpose(self, direction_key: str, purpose: str) -> bool:
        """Добавляет назначение платежа для направления"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT OR IGNORE INTO payment_purposes (direction_key, purpose) VALUES (?, ?)',
                    (direction_key, purpose)
                )
                conn.commit()
                logger.info(f"Назначение {purpose} добавлено для направления {direction_key}")
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления назначения: {e}")
            return False
    
    def add_ready_offer(self, offer_data: Dict[str, Any]) -> bool:
        """Добавляет готовое КП"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Преобразуем списки и словари в JSON
                details_json = json.dumps(offer_data.get('details', []), ensure_ascii=False)
                nds_rates_json = json.dumps(offer_data.get('nds_rates', []), ensure_ascii=False)
                equipment_types_json = json.dumps(offer_data.get('equipment_types', []), ensure_ascii=False)
                payment_purposes_json = json.dumps(offer_data.get('payment_purposes', []), ensure_ascii=False)
                
                cursor.execute('''
                    INSERT INTO ready_offers 
                    (direction, payment_purpose, kp_name, title, description, 
                     price, delivery, warranty, details, nds_rates, min_amount, max_amount,
                     equipment_types, payment_purposes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    offer_data['direction'],
                    offer_data['payment_purpose'],
                    offer_data['kp_name'],
                    offer_data['title'],
                    offer_data.get('description', ''),
                    offer_data.get('price', ''),
                    offer_data.get('delivery', ''),
                    offer_data.get('warranty', ''),
                    details_json,
                    nds_rates_json,
                    offer_data.get('min_amount', 0),
                    offer_data.get('max_amount', 0),
                    equipment_types_json,
                    payment_purposes_json
                ))
                
                conn.commit()
                # Получаем ID созданного КП
                cursor.execute('SELECT last_insert_rowid()')
                new_id = cursor.fetchone()[0]
                logger.info(f"КП {offer_data['kp_name']} добавлено с ID: {new_id}")
                return new_id
                
        except Exception as e:
            logger.error(f"Ошибка добавления КП: {e}")
            return False
    
    def get_directions(self) -> Dict[str, str]:
        """Получает все направления"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT key, name FROM directions WHERE key != "другое"')
                return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Ошибка получения направлений: {e}")
            return {}
    
    
    
    def add_feedback(self, feedback_data: Dict[str, Any]) -> bool:
        """Добавляет обратную связь"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO feedback 
                    (user_id, offer_id, feedback_type, direction, payment_purpose, 
                     amount, nds_rate, equipment_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    feedback_data['user_id'],
                    feedback_data['offer_id'],
                    feedback_data['feedback_type'],
                    feedback_data['direction'],
                    feedback_data.get('payment_purpose'),
                    feedback_data.get('amount'),
                    feedback_data.get('nds_rate'),
                    feedback_data.get('equipment_type')
                ))
                
                conn.commit()
                logger.info(f"Обратная связь добавлена для пользователя {feedback_data['user_id']}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка добавления обратной связи: {e}")
            return False
    
    def add_or_update_user(self, user_data: Dict[str, Any]) -> bool:
        """Добавляет или обновляет пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO users 
                    (user_id, username, first_name, last_name, last_activity)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    user_data['user_id'],
                    user_data.get('username'),
                    user_data.get('first_name'),
                    user_data.get('last_name')
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Ошибка добавления пользователя: {e}")
            return False
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Получает всех пользователей"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id, username, first_name, last_name, is_blocked, 
                           first_seen, last_activity
                    FROM users 
                    ORDER BY first_seen DESC
                ''')
                
                users = []
                for row in cursor.fetchall():
                    users.append({
                        'user_id': row[0],
                        'username': row[1],
                        'first_name': row[2],
                        'last_name': row[3],
                        'is_blocked': bool(row[4]),
                        'first_seen': row[5],
                        'last_activity': row[6]
                    })
                
                return users
                
        except Exception as e:
            logger.error(f"Ошибка получения пользователей: {e}")
            return []
    
    def get_new_users(self, days: int = 7) -> List[Dict[str, Any]]:
        """Получает новых пользователей за последние N дней"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id, username, first_name, last_name, is_blocked, 
                           first_seen, last_activity
                    FROM users 
                    WHERE first_seen >= datetime('now', '-{} days')
                    ORDER BY first_seen DESC
                '''.format(days))
                
                users = []
                for row in cursor.fetchall():
                    users.append({
                        'user_id': row[0],
                        'username': row[1],
                        'first_name': row[2],
                        'last_name': row[3],
                        'is_blocked': bool(row[4]),
                        'first_seen': row[5],
                        'last_activity': row[6]
                    })
                
                return users
                
        except Exception as e:
            logger.error(f"Ошибка получения новых пользователей: {e}")
            return []
    
    def block_user(self, user_id: int) -> bool:
        """Блокирует пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users SET is_blocked = 1 WHERE user_id = ?
                ''', (user_id,))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Ошибка блокировки пользователя: {e}")
            return False
    
    def unblock_user(self, user_id: int) -> bool:
        """Разблокирует пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users SET is_blocked = 0 WHERE user_id = ?
                ''', (user_id,))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Ошибка разблокировки пользователя: {e}")
            return False
    
    def is_user_blocked(self, user_id: int) -> bool:
        """Проверяет заблокирован ли пользователь"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT is_blocked FROM users WHERE user_id = ?
                ''', (user_id,))
                
                row = cursor.fetchone()
                return bool(row[0]) if row else False
                
        except Exception as e:
            logger.error(f"Ошибка проверки блокировки пользователя: {e}")
            return False
    
    
    
