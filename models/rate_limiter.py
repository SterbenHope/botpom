#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
from collections import defaultdict
from typing import Dict, List


class RateLimiter:
    """Система ограничения частоты запросов"""
    
    def __init__(self, max_requests: int = 10, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: Dict[int, List[float]] = defaultdict(list)
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверяет, разрешен ли запрос от пользователя"""
        current_time = time.time()
        user_requests = self.requests[user_id]
        
        # Удаляем старые запросы
        user_requests[:] = [req_time for req_time in user_requests 
                           if current_time - req_time < self.time_window]
        
        # Проверяем лимит
        if len(user_requests) >= self.max_requests:
            return False
        
        # Добавляем текущий запрос
        user_requests.append(current_time)
        return True
    
    def get_remaining_time(self, user_id: int) -> int:
        """Возвращает время до следующего разрешенного запроса"""
        current_time = time.time()
        user_requests = self.requests[user_id]
        
        if len(user_requests) < self.max_requests:
            return 0
        
        oldest_request = min(user_requests)
        return int(self.time_window - (current_time - oldest_request))

