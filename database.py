#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль для работы с базой данных КП
"""

import sqlite3
import json
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class KPDatabase:
    """Класс для работы с базой данных КП"""
    
    def __init__(self, db_path: str = 'kp_database.db') -> None:
        self.db_path = db_path
        self.init_database()
    
    def init_database(self) -> None:
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
                        company_name TEXT NOT NULL,
                        inn TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        payment_purpose TEXT NOT NULL,
                        bank TEXT NOT NULL,
                        min_amount INTEGER DEFAULT 0,
                        max_amount INTEGER DEFAULT 0,
                        commission REAL DEFAULT 0.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Таблица заявок клиентов
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS client_applications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        direction TEXT NOT NULL,
                        company_name TEXT NOT NULL,
                        inn TEXT NOT NULL,
                        bank TEXT NOT NULL,
                        nds_rate INTEGER NOT NULL,
                        category TEXT NOT NULL,
                        payment_purpose TEXT NOT NULL,
                        amount INTEGER NOT NULL,
                        equipment_type TEXT NOT NULL,
                        description TEXT,
                        operation_type TEXT NOT NULL,
                        admin_message_id INTEGER,
                        admin_chat_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Миграция: проверяем и добавляем недостающие колонки
                cursor.execute("PRAGMA table_info(ready_offers)")
                columns = [column[1] for column in cursor.fetchall()]
                
                if 'company_name' not in columns:
                    # Старая структура, нужно пересоздать таблицу
                    cursor.execute('DROP TABLE IF EXISTS ready_offers')
                    cursor.execute('''
                        CREATE TABLE ready_offers (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            company_name TEXT NOT NULL,
                            inn TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            payment_purpose TEXT NOT NULL,
                            bank TEXT NOT NULL,
                            min_amount INTEGER DEFAULT 0,
                            max_amount INTEGER DEFAULT 0,
                            commission REAL DEFAULT 0.0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    logger.info("Table ready_offers recreated with new structure")
                else:
                    # Проверяем наличие колонки commission
                    if 'commission' not in columns:
                        cursor.execute('ALTER TABLE ready_offers ADD COLUMN commission REAL DEFAULT 0.0')
                        logger.info("Added commission column to ready_offers")
                    logger.info("Table ready_offers already has correct structure")
                
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
                
                # Таблица уведомлений для владельца
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS owner_notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        notification_type TEXT NOT NULL,
                        user_id INTEGER,
                        application_id INTEGER,
                        offer_id INTEGER,
                        direction TEXT,
                        company_name TEXT,
                        admin_chat_id TEXT,
                        admin_user_id INTEGER,
                        feedback_type TEXT,
                        message TEXT,
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
                
                # Добавляем индексы для оптимизации
                self._create_indexes(cursor)
                
                conn.commit()
                logger.info("Database initialized successfully")
                
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
    
    def _create_indexes(self, cursor) -> None:
        """Создает индексы для оптимизации производительности"""
        indexes = [
            # Индексы для client_applications
            "CREATE INDEX IF NOT EXISTS idx_applications_user_id ON client_applications(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_applications_direction ON client_applications(direction)",
            "CREATE INDEX IF NOT EXISTS idx_applications_created_at ON client_applications(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_applications_admin_message ON client_applications(admin_message_id, admin_chat_id)",
            
            # Индексы для feedback
            "CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON feedback(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback(feedback_type)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_direction ON feedback(direction)",
            
            # Индексы для owner_notifications
            "CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON owner_notifications(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_type ON owner_notifications(notification_type)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_direction ON owner_notifications(direction)",
            
            # Индексы для ready_offers
            "CREATE INDEX IF NOT EXISTS idx_offers_direction ON ready_offers(direction)",
            "CREATE INDEX IF NOT EXISTS idx_offers_created_at ON ready_offers(created_at)",
            
            # Индексы для users
            "CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(first_seen)",
            "CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(is_blocked)",
        ]
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
                logger.info(f"Created index: {index_sql.split('idx_')[1].split(' ')[0]}")
            except Exception as e:
                logger.error(f"Error creating index: {e}")
    
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
                logger.info(f"Direction {key}: {name} added/updated")
                return True
        except Exception as e:
            logger.error(f"Error adding direction: {e}")
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
                logger.info(f"Purpose {purpose} added for direction {direction_key}")
                return True
        except Exception as e:
            logger.error(f"Error adding purpose: {e}")
            return False
    
    def add_ready_offer(self, offer_data: Dict[str, Any]) -> int:
        """Добавляет готовое КП"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO ready_offers 
                    (company_name, inn, direction, payment_purpose, bank, min_amount, max_amount, commission)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    offer_data['company_name'],
                    offer_data['inn'],
                    offer_data['direction'],
                    offer_data['payment_purpose'],
                    offer_data['bank'],
                    offer_data.get('min_amount', 0),
                    offer_data.get('max_amount', 0),
                    offer_data.get('commission', 0.0)
                ))
                
                conn.commit()
                # Получаем ID созданного КП
                cursor.execute('SELECT last_insert_rowid()')
                new_id = cursor.fetchone()[0]
                logger.info(f"Offer {offer_data['company_name']} added with ID: {new_id}")
                return new_id
                
        except Exception as e:
            logger.error(f"Error adding offer: {e}")
            return 0
    
    def get_ready_offers_by_direction(self, direction: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Получает готовые КП по направлению с пагинацией"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, company_name, inn, direction, payment_purpose, 
                           bank, min_amount, max_amount, commission, created_at
                    FROM ready_offers 
                    WHERE direction = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                ''', (direction, limit, offset))
                
                offers = []
                for row in cursor.fetchall():
                    offers.append({
                        'id': row[0],
                        'company_name': row[1],
                        'inn': row[2],
                        'direction': row[3],
                        'payment_purpose': row[4],
                        'bank': row[5],
                        'min_amount': row[6],
                        'max_amount': row[7],
                        'commission': row[8],
                        'created_at': row[9]
                    })
                
                return offers
                
        except Exception as e:
            logger.error(f"Error getting offer: {e}")
            return []
    
    def get_ready_offer_by_id(self, offer_id: int) -> Optional[Dict[str, Any]]:
        """Получает КП по ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, company_name, inn, direction, payment_purpose, 
                           bank, min_amount, max_amount, commission, created_at
                    FROM ready_offers 
                    WHERE id = ?
                ''', (offer_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'company_name': row[1],
                        'inn': row[2],
                        'direction': row[3],
                        'payment_purpose': row[4],
                        'bank': row[5],
                        'min_amount': row[6],
                        'max_amount': row[7],
                        'commission': row[8],
                        'created_at': row[9]
                    }
                return None
                
        except Exception as e:
            logger.error(f"Error getting offer: {e}")
            return None
    
    def update_ready_offer(self, offer_id: int, offer_data: Dict[str, Any]) -> bool:
        """Обновляет готовое КП"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE ready_offers 
                    SET company_name = ?, inn = ?, direction = ?, payment_purpose = ?, 
                        bank = ?, min_amount = ?, max_amount = ?, commission = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (
                    offer_data['company_name'],
                    offer_data['inn'],
                    offer_data['direction'],
                    offer_data['payment_purpose'],
                    offer_data['bank'],
                    offer_data.get('min_amount', 0),
                    offer_data.get('max_amount', 0),
                    offer_data.get('commission', 0.0),
                    offer_id
                ))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Error updating offer: {e}")
            return False
    
    def delete_ready_offer(self, offer_id: int) -> bool:
        """Удаляет готовое КП"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM ready_offers WHERE id = ?', (offer_id,))
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Error deleting offer: {e}")
            return False
    
    def add_client_application(self, application_data: Dict[str, Any]) -> int:
        """Добавляет заявку клиента и возвращает её ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO client_applications 
                    (user_id, direction, company_name, inn, bank, nds_rate, 
                     category, payment_purpose, amount, equipment_type, 
                     description, operation_type, admin_message_id, admin_chat_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    application_data['user_id'],
                    application_data['direction'],
                    application_data['company_name'],
                    application_data['inn'],
                    application_data['bank'],
                    application_data['nds_rate'],
                    application_data['category'],
                    application_data['payment_purpose'],
                    application_data['amount'],
                    application_data['equipment_type'],
                    application_data.get('description', ''),
                    application_data['operation_type'],
                    application_data.get('admin_message_id'),
                    application_data.get('admin_chat_id')
                ))
                
                conn.commit()
                app_id = cursor.lastrowid
                logger.info(f"Client application added with ID: {app_id}")
                return app_id
                
        except Exception as e:
            logger.error(f"Error adding client application: {e}")
            return 0
    
    def get_client_application_by_id(self, app_id: int) -> Optional[Dict[str, Any]]:
        """Получает заявку клиента по ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, user_id, direction, company_name, inn, bank, 
                           nds_rate, category, payment_purpose, amount, 
                           equipment_type, description, operation_type, 
                           admin_message_id, admin_chat_id, created_at
                    FROM client_applications WHERE id = ?
                ''', (app_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'user_id': row[1],
                        'direction': row[2],
                        'company_name': row[3],
                        'inn': row[4],
                        'bank': row[5],
                        'nds_rate': row[6],
                        'category': row[7],
                        'payment_purpose': row[8],
                        'amount': row[9],
                        'equipment_type': row[10],
                        'description': row[11],
                        'operation_type': row[12],
                        'admin_message_id': row[13],
                        'admin_chat_id': row[14],
                        'created_at': row[15]
                    }
                return None
                
        except Exception as e:
            logger.error(f"Error getting client application: {e}")
            return None
    
    def update_client_application_admin_info(self, app_id: int, admin_message_id: int, admin_chat_id: str) -> bool:
        """Обновляет информацию об админском сообщении для заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE client_applications 
                    SET admin_message_id = ?, admin_chat_id = ?
                    WHERE id = ?
                ''', (admin_message_id, admin_chat_id, app_id))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Error updating client application: {e}")
            return False
    
    def get_client_application_by_admin_message(self, admin_message_id: int, admin_chat_id: str) -> Optional[Dict[str, Any]]:
        """Получает заявку клиента по admin_message_id и admin_chat_id"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, user_id, direction, company_name, inn, bank, 
                           nds_rate, category, payment_purpose, amount, 
                           equipment_type, description, operation_type, 
                           admin_message_id, admin_chat_id, created_at
                    FROM client_applications 
                    WHERE admin_message_id = ? AND admin_chat_id = ?
                ''', (admin_message_id, admin_chat_id))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'user_id': row[1],
                        'direction': row[2],
                        'company_name': row[3],
                        'inn': row[4],
                        'bank': row[5],
                        'nds_rate': row[6],
                        'category': row[7],
                        'payment_purpose': row[8],
                        'amount': row[9],
                        'equipment_type': row[10],
                        'description': row[11],
                        'operation_type': row[12],
                        'admin_message_id': row[13],
                        'admin_chat_id': row[14],
                        'created_at': row[15]
                    }
                return None
                
        except Exception as e:
            logger.error(f"Error getting application by admin_message: {e}")
            return None
    
    def add_owner_notification(self, notification_data: Dict[str, Any]) -> bool:
        """Добавляет уведомление для владельца"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO owner_notifications 
                    (notification_type, user_id, application_id, offer_id, direction, 
                     company_name, admin_chat_id, admin_user_id, feedback_type, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    notification_data['notification_type'],
                    notification_data.get('user_id'),
                    notification_data.get('application_id'),
                    notification_data.get('offer_id'),
                    notification_data.get('direction'),
                    notification_data.get('company_name'),
                    notification_data.get('admin_chat_id'),
                    notification_data.get('admin_user_id'),
                    notification_data.get('feedback_type'),
                    notification_data.get('message')
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error adding owner notification: {e}")
            return False
    
    def get_daily_statistics(self, date: str = None) -> Dict[str, Any]:
        """Получает статистику за день"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if date is None:
                    date = datetime.now().strftime('%Y-%m-%d')
                
                # Статистика заявок
                cursor.execute('''
                    SELECT COUNT(*) FROM client_applications 
                    WHERE DATE(created_at) = ?
                ''', (date,))
                applications_count = cursor.fetchone()[0]
                
                # Статистика обратной связи
                cursor.execute('''
                    SELECT feedback_type, COUNT(*) FROM feedback 
                    WHERE DATE(created_at) = ?
                    GROUP BY feedback_type
                ''', (date,))
                feedback_stats = dict(cursor.fetchall())
                
                # Статистика по направлениям
                cursor.execute('''
                    SELECT direction, COUNT(*) FROM client_applications 
                    WHERE DATE(created_at) = ?
                    GROUP BY direction
                ''', (date,))
                direction_stats = dict(cursor.fetchall())
                
                return {
                    'date': date,
                    'applications_count': applications_count,
                    'feedback_stats': feedback_stats,
                    'direction_stats': direction_stats
                }
                
        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}
    
    def get_directions(self) -> Dict[str, str]:
        """Получает все направления"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT key, name FROM directions WHERE key != "другое"')
                return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting directions: {e}")
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
                logger.info(f"Feedback added for user {feedback_data['user_id']}")
                return True
                
        except Exception as e:
            logger.error(f"Error adding feedback: {e}")
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
            logger.error(f"Error adding user: {e}")
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
            logger.error(f"Error getting users: {e}")
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
            logger.error(f"Error getting new users: {e}")
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
            logger.error(f"Error blocking user: {e}")
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
            logger.error(f"Error unblocking user: {e}")
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
            logger.error(f"Error checking user block status: {e}")
            return False
    
    def cleanup_old_data(self, days_to_keep_notifications: int = 30, days_to_keep_feedback_no: int = 7) -> Dict[str, int]:
        """Очищает старые данные из БД"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Дата для очистки уведомлений
                notifications_cutoff = datetime.now() - timedelta(days=days_to_keep_notifications)
                
                # Дата для очистки отрицательных фидбеков
                feedback_no_cutoff = datetime.now() - timedelta(days=days_to_keep_feedback_no)
                
                # 1. Удаляем старые уведомления владельцу
                cursor.execute("""
                    DELETE FROM owner_notifications 
                    WHERE created_at < ?
                """, (notifications_cutoff,))
                notifications_deleted = cursor.rowcount
                
                # 2. Удаляем старые отрицательные фидбеки (только 'no')
                cursor.execute("""
                    DELETE FROM feedback 
                    WHERE feedback_type = 'no' AND created_at < ?
                """, (feedback_no_cutoff,))
                feedback_no_deleted = cursor.rowcount
                
                # 3. Оставляем только положительные фидбеки (yes) - они не удаляются
                # Это важно для статистики и аналитики
                
                conn.commit()
                
                logger.info(f"Cleanup completed:")
                logger.info(f"  - Notifications deleted: {notifications_deleted}")
                logger.info(f"  - Negative feedback deleted: {feedback_no_deleted}")
                
                return {
                    'notifications_deleted': notifications_deleted,
                    'feedback_no_deleted': feedback_no_deleted
                }
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return {'notifications_deleted': 0, 'feedback_no_deleted': 0}
    
    def optimize_database(self) -> bool:
        """Оптимизирует базу данных SQLite"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 1. Включаем WAL режим для лучшей производительности
                cursor.execute("PRAGMA journal_mode=WAL")
                
                # 2. Увеличиваем кэш страниц
                cursor.execute("PRAGMA cache_size=10000")
                
                # 3. Включаем синхронизацию для надежности
                cursor.execute("PRAGMA synchronous=NORMAL")
                
                # 4. Анализируем БД для оптимизации запросов
                cursor.execute("ANALYZE")
                
                # 5. Выполняем очистку старых данных
                cleanup_result = self.cleanup_old_data()
                
                conn.commit()
                logger.info(f"Database optimization completed: {cleanup_result}")
                return True
                
        except Exception as e:
            logger.error(f"Error optimizing database: {e}")
            return False
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Получает статистику БД"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Подсчитываем записи
                cursor.execute("SELECT COUNT(*) FROM client_applications")
                applications = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM feedback WHERE feedback_type = 'yes'")
                feedback_yes = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM feedback WHERE feedback_type = 'no'")
                feedback_no = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM owner_notifications")
                notifications = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM users")
                users = cursor.fetchone()[0]
                
                # Размер БД
                cursor.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
                db_size = cursor.fetchone()[0]
                
                return {
                    'applications': applications,
                    'feedback_yes': feedback_yes,
                    'feedback_no': feedback_no,
                    'notifications': notifications,
                    'users': users,
                    'db_size_kb': db_size / 1024,
                    'db_size_mb': db_size / (1024 * 1024)
                }
                
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}
    
    
