"""Периодическое обновление сводки. Бизнес-данные всегда остаются в PostgreSQL."""
import json
import logging
import os
import time
import redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from server import database_url, engine_for, summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
engine = engine_for(database_url())
cache = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_timeout=2)
while True:
    try:
        with Session(engine) as db:
            # Блокировка Redis предотвращает одновременный запуск нескольких обработчиков.
            with cache.lock("production:summary-lock", timeout=10, blocking_timeout=1):
                cache.setex("production:summary", 10, json.dumps(summary(db)))
                cache.setex("production:worker", 20, str(time.time()))
        logging.info("Сводка обновлена")
    except (redis.RedisError, SQLAlchemyError, ValueError):
        logging.exception("Временная ошибка обновления сводки")
    time.sleep(5)
