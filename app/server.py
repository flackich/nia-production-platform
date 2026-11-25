"""Учебная платформа управления производственными заказами. Автор: Соседов Р. В."""
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote

import redis
from flask import Flask, g, jsonify, request, session
from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, Integer, String,
                        Text, create_engine, delete, func, select, text)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger(__name__)
STATES = {"planned", "in_progress", "completed", "cancelled"}
TRANSITIONS = {"planned": {"in_progress", "cancelled"},
               "in_progress": {"completed", "cancelled"}, "completed": set(), "cancelled": set()}


def setting(name, default=None):
    filename = os.getenv(name + "_FILE")
    return Path(filename).read_text().strip() if filename else os.getenv(name, default)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    workshop: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="ready")
    __table_args__ = (CheckConstraint("status IN ('ready','maintenance')"),)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    product: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer)
    completed_quantity: Mapped[int] = mapped_column(Integer, default=0)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    status: Mapped[str] = mapped_column(String(20), default="planned")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    due_date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (CheckConstraint("quantity > 0"),
                     CheckConstraint("completed_quantity >= 0 AND completed_quantity <= quantity"),
                     CheckConstraint("status IN ('planned','in_progress','completed','cancelled')"),
                     CheckConstraint("priority IN ('normal','high','urgent')"))


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    details: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def serialize(obj):
    return {c.name: (v.isoformat() if isinstance(v := getattr(obj, c.name), (date, datetime)) else v)
            for c in obj.__table__.columns if c.name != "password_hash"}


def database_url():
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    password = quote(setting("DB_PASSWORD", ""), safe="")
    return f"postgresql+psycopg://production:{password}@db:5432/production"


def engine_for(url):
    engine = create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        from sqlalchemy import event
        @event.listens_for(engine, "connect")
        def foreign_keys(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    return engine


def bootstrap(engine):
    """Схема и начальные данные создаются под общей блокировкой PostgreSQL."""
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(15152026)"))
        Base.metadata.create_all(conn)
        with sessionmaker(bind=conn)() as db:
            if not db.scalar(select(User.id).limit(1)):
                users = [("admin", "admin", "Администратор", "ADMIN_PASSWORD", "DemoAdmin-2026!"),
                         ("master", "master", "Мастер участка", "MASTER_PASSWORD", "DemoMaster-2026!"),
                         ("viewer", "viewer", "Наблюдатель", "VIEWER_PASSWORD", "DemoView-2026!")]
                db.add_all([User(username=u, role=r, name=n, password_hash=generate_password_hash(setting(k, d)))
                            for u, r, n, k, d in users])
            if os.getenv("SEED_DEMO", "1") == "1" and not db.scalar(select(Equipment.id).limit(1)):
                machines = [Equipment(name="Токарный станок ТС-01", workshop="Механический цех", status="ready"),
                            Equipment(name="Фрезерный станок ФС-02", workshop="Механический цех", status="ready"),
                            Equipment(name="Линия сборки ЛС-01", workshop="Сборочный цех", status="ready"),
                            Equipment(name="Пресс ПР-03", workshop="Заготовительный цех", status="maintenance")]
                db.add_all(machines); db.flush()
                specs = [("Корпус редуктора", 120, 45, 0, "in_progress", "high", 3),
                         ("Вал приводной", 80, 0, 1, "planned", "normal", 5),
                         ("Узел крепления", 200, 200, 2, "completed", "normal", -2),
                         ("Втулка опорная", 150, 70, 0, "in_progress", "urgent", -1),
                         ("Кронштейн монтажный", 90, 0, 1, "planned", "high", 7),
                         ("Сборочный комплект", 60, 0, 2, "planned", "normal", 8)]
                for i, (product, qty, done, mid, state, priority, days) in enumerate(specs, 1):
                    db.add(Order(code=f"ПЗ-2026-{i:03}", product=product, quantity=qty, completed_quantity=done,
                                 equipment_id=machines[mid].id, status=state, priority=priority,
                                 due_date=date.today() + timedelta(days=days)))
                db.add(Audit(actor="system", action="seed", details="Загружены учебные данные: 4 единицы оборудования, 6 заказов"))
            db.commit()


def summary(db):
    orders = db.scalars(select(Order)).all()
    return {"total": len(orders), "active": sum(o.status == "in_progress" for o in orders),
            "completed": sum(o.status == "completed" for o in orders),
            "overdue": sum(o.due_date < date.today() and o.status in {"planned", "in_progress"} for o in orders),
            "planned_units": sum(o.quantity for o in orders if o.status != "cancelled"),
            "produced_units": sum(o.completed_quantity for o in orders if o.status != "cancelled"),
            "updated_at": datetime.now(timezone.utc).isoformat()}


def create_app(config=None):
    app = Flask(__name__, static_folder="../frontend", static_url_path="/static")
    secret_key = setting("SECRET_KEY")
    if not secret_key and not (config or {}).get("TESTING"):
        raise RuntimeError("SECRET_KEY или SECRET_KEY_FILE обязателен")
    app.config.update(SECRET_KEY=secret_key or "test-key", MAX_CONTENT_LENGTH=64 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    app.config.update(config or {})
    engine = engine_for(app.config.get("DATABASE_URL", database_url()))
    for attempt in range(30):
        try:
            bootstrap(engine); break
        except SQLAlchemyError:
            if attempt == 29: raise
            time.sleep(2)
    Session = sessionmaker(engine, expire_on_commit=False)
    cache = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True,
                                 socket_connect_timeout=0.25, socket_timeout=0.25)
    app.extensions.update(db_engine=engine, db_session=Session, cache=cache)

    @app.before_request
    def context():
        g.db = Session()
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = session.get("csrf", "")
            if not token or not hmac.compare_digest(token, request.headers.get("X-CSRF-Token", "")):
                return jsonify(error="Некорректный CSRF-токен"), 403
            if request.is_json and request.content_length and not isinstance(request.get_json(silent=True), dict):
                return jsonify(error="Тело запроса должно быть объектом JSON"), 400

    @app.after_request
    def headers(response):
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "same-origin"})
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.teardown_request
    def close(_):
        if "db" in g:
            g.db.rollback(); g.db.close()

    @app.errorhandler(IntegrityError)
    def conflict(_):
        g.db.rollback()
        return jsonify(error="Нарушена уникальность или связь данных"), 409

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="Слишком большой запрос"), 413

    def access(*roles):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                user = g.db.get(User, session.get("uid", -1))
                if not user: return jsonify(error="Необходим вход"), 401
                if roles and user.role not in roles: return jsonify(error="Недостаточно прав"), 403
                g.user = user
                return fn(*args, **kwargs)
            return wrapped
        return decorate

    def audit(action, details):
        g.db.add(Audit(actor=g.user.username, action=action, details=details))

    def invalidate():
        try: cache.delete("production:summary")
        except redis.RedisError: log.warning("Redis недоступен, сводка будет рассчитана из БД")

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/health/live")
    def live():
        return jsonify(status="ok")

    @app.get("/health/ready")
    def ready():
        try:
            g.db.execute(text("SELECT 1"))
            return jsonify(status="ready")
        except SQLAlchemyError:
            return jsonify(status="unavailable"), 503

    @app.get("/api/session")
    def current_session():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        user = g.db.get(User, session.get("uid", -1))
        return jsonify(csrf=session["csrf"], user=serialize(user) if user else None)

    @app.post("/api/login")
    def login():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", ""))[:60]
        key = "login:" + hashlib.sha256((request.remote_addr + ":" + username).encode()).hexdigest()
        try:
            if int(cache.get(key) or 0) >= 8: return jsonify(error="Слишком много попыток, повторите через минуту"), 429
        except redis.RedisError: pass
        user = g.db.scalar(select(User).where(User.username == username))
        password = str(data.get("password", ""))[:256]
        if not user or not check_password_hash(user.password_hash, password):
            try:
                cache.incr(key); cache.expire(key, 60)
            except redis.RedisError: pass
            return jsonify(error="Неверное имя пользователя или пароль"), 401
        session.clear(); session["uid"] = user.id; session["csrf"] = secrets.token_urlsafe(32);session.permanent = True
        g.user = user; audit("login", "Вход в систему");g.db.commit()
        return jsonify(user=serialize(user), csrf=session["csrf"])

    @app.post("/api/logout")
    @access()
    def logout():
        audit("logout", "Выход из системы");g.db.commit();session.clear()
        return jsonify(ok=True)

    @app.get("/api/orders")
    @access()
    def orders():
        query = select(Order).order_by(Order.id.desc())
        if state := request.args.get("status"):
            if state not in STATES: return jsonify(error="Неизвестный статус"), 400
            query = query.where(Order.status == state)
        if search := request.args.get("q", "").strip():
            query = query.where(Order.product.ilike("%" + search[:120] + "%") | Order.code.ilike("%" + search[:120] + "%"))
        return jsonify([serialize(o) for o in g.db.scalars(query).all()])

    def order_values(data, existing=None):
        values = {}
        for key in ["code", "product"]:
            value = str(data.get(key, getattr(existing, key, ""))).strip()
            if not value or len(value) > (40 if key == "code" else 120): raise ValueError("Заполните корректно номер и изделие")
            values[key] = value
        for key in ["quantity", "completed_quantity", "equipment_id"]:
            value = data.get(key, getattr(existing, key, 0))
            if isinstance(value, bool) or not str(value).isdigit(): raise ValueError("Количество и оборудование должны быть целыми числами")
            values[key] = int(value)
        if not 1 <= values["quantity"] <= 1_000_000 or not 0 <= values["completed_quantity"] <= values["quantity"]:
            raise ValueError("Выпуск должен быть в пределах объёма заказа")
        machine = g.db.get(Equipment, values["equipment_id"])
        if not machine: raise ValueError("Оборудование не найдено")
        values["status"] = str(data.get("status", getattr(existing, "status", "planned")))
        values["priority"] = str(data.get("priority", getattr(existing, "priority", "normal")))
        if values["status"] not in STATES or values["priority"] not in {"normal", "high", "urgent"}:
            raise ValueError("Некорректный статус или приоритет")
        if not existing and (values["status"] != "planned" or values["completed_quantity"] != 0):
            raise ValueError("Новый заказ создаётся запланированным, без выпуска")
        if existing:
            if values["status"] != existing.status and values["status"] not in TRANSITIONS[existing.status]:
                raise ValueError("Недопустимый переход статуса")
            if existing.status in {"completed", "cancelled"}: raise ValueError("Закрытый заказ нельзя изменять")
        if values["status"] == "planned" and values["completed_quantity"]: raise ValueError("Плановый заказ не содержит выпуска")
        if values["status"] == "completed" and values["completed_quantity"] != values["quantity"]:
            raise ValueError("Для завершения нужен полный выпуск")
        if values["status"] == "in_progress" and machine.status != "ready": raise ValueError("Оборудование на обслуживании")
        try: values["due_date"] = date.fromisoformat(str(data.get("due_date", getattr(existing, "due_date", ""))))
        except ValueError: raise ValueError("Укажите срок в формате ГГГГ-ММ-ДД")
        return values

    @app.post("/api/orders")
    @access("admin", "master")
    def add_order():
        try: values = order_values(request.get_json(silent=True) or {})
        except ValueError as error: return jsonify(error=str(error)), 400
        order = Order(**values);g.db.add(order);g.db.flush()
        audit("order.create", f"Создан заказ {order.code}");g.db.commit();invalidate()
        return jsonify(serialize(order)), 201

    @app.put("/api/orders/<int:oid>")
    @access("admin", "master")
    def update_order(oid):
        order = g.db.scalar(select(Order).where(Order.id == oid).with_for_update())
        if not order: return jsonify(error="Заказ не найден"), 404
        data = request.get_json(silent=True) or {}
        if data.get("version") != order.version: return jsonify(error="Заказ изменён другим пользователем. Обновите список"), 409
        try: values = order_values(data, order)
        except ValueError as error: return jsonify(error=str(error)), 400
        for k, v in values.items(): setattr(order, k, v)
        order.version += 1;audit("order.update", f"Изменён заказ {order.code}, статус {order.status}")
        g.db.commit();invalidate()
        return jsonify(serialize(order))

    @app.delete("/api/orders/<int:oid>")
    @access("admin")
    def remove_order(oid):
        order = g.db.scalar(select(Order).where(Order.id == oid).with_for_update())
        if not order: return jsonify(error="Заказ не найден"), 404
        if order.status != "planned": return jsonify(error="Удалять можно только плановые заказы"), 409
        audit("order.delete", f"Удалён заказ {order.code}");g.db.delete(order);g.db.commit();invalidate()
        return "", 204

    @app.get("/api/equipment")
    @access()
    def equipment():
        return jsonify([serialize(m) for m in g.db.scalars(select(Equipment).order_by(Equipment.id)).all()])

    @app.put("/api/equipment/<int:mid>")
    @access("admin")
    def update_machine(mid):
        machine = g.db.get(Equipment, mid)
        if not machine: return jsonify(error="Оборудование не найдено"), 404
        state = (request.get_json(silent=True) or {}).get("status")
        if state not in {"ready", "maintenance"}: return jsonify(error="Некорректный статус"), 400
        machine.status = state;audit("equipment.update", f"{machine.name}: {state}");g.db.commit()
        return jsonify(serialize(machine))

    @app.get("/api/summary")
    @access()
    def get_summary():
        try:
            if cached := cache.get("production:summary"): return jsonify(json.loads(cached))
        except redis.RedisError: pass
        return jsonify(summary(g.db))

    @app.get("/api/audit")
    @access("admin", "master")
    def history():
        return jsonify([serialize(a) for a in g.db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100)).all()])

    @app.get("/api/system")
    @access("admin")
    def system():
        try: cache_ok = cache.ping(); heartbeat = cache.get("production:worker")
        except redis.RedisError: cache_ok = False;heartbeat = None
        return jsonify(database=engine.dialect.name, redis=bool(cache_ok), worker_heartbeat=heartbeat,
                       worker_alive=bool(heartbeat and time.time() - float(heartbeat) < 20))

    @app.get("/api/export")
    @access()
    def export():
        out = io.StringIO();writer = csv.writer(out)
        writer.writerow(["Номер", "Изделие", "План", "Выпуск", "Статус", "Срок"])
        for o in g.db.scalars(select(Order).order_by(Order.id)):
            cells = [o.code, o.product, o.quantity, o.completed_quantity, o.status, o.due_date.isoformat()]
            writer.writerow(["'" + c if isinstance(c, str) and c.startswith(("=", "+", "-", "@", "\t", "\r")) else c for c in cells])
        return app.response_class("\ufeff" + out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="orders.csv"'})

    @app.get("/metrics")
    def metrics():
        data = summary(g.db)
        return app.response_class("\n".join(["# HELP production_orders_current Number of current orders", "# TYPE production_orders_current gauge",
                                             f'production_orders_current {data["total"]}', '# TYPE production_orders_overdue gauge',
                                             f'production_orders_overdue {data["overdue"]}', '# TYPE production_units_completed gauge',
                                             f'production_units_completed {data["produced_units"]}', ""]), mimetype="text/plain")
    return app
