from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload

try:  # Flask-Migrate is optional in local setups
    from flask_migrate import Migrate
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal installs
    Migrate = None  # type: ignore[assignment]

try:  # zoneinfo is available in the stdlib but may miss tzdata on some OSes
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - keep compatibility with exotic Python builds
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]


os.environ.setdefault("FLASK_RUN_HOST", "127.0.0.1")
os.environ.setdefault("FLASK_RUN_PORT", "5050")

db = SQLAlchemy()
migrate = Migrate() if Migrate is not None else None

DEFAULT_SUBJECTS: tuple[dict[str, str | int | None], ...] = (
    {
        "name": "Математика",
        "color": "#6366f1",
        "default_duration": 60,
        "description": "Алгебра, геометрия и подготовка к контрольным работам.",
    },
    {
        "name": "Русский язык",
        "color": "#f97316",
        "default_duration": 45,
        "description": "Орфография, пунктуация и развитие речи.",
    },
    {
        "name": "Литература",
        "color": "#ec4899",
        "default_duration": 50,
        "description": "Разбор произведений, подготовка к сочинениям и анализ текстов.",
    },
    {
        "name": "Английский язык",
        "color": "#22d3ee",
        "default_duration": 50,
        "description": "Грамматика, разговорная практика и подготовка к экзаменам.",
    },
    {
        "name": "Физика",
        "color": "#8b5cf6",
        "default_duration": 60,
        "description": "Теория, решение задач и подготовка к лабораторным работам.",
    },
    {
        "name": "Химия",
        "color": "#14b8a6",
        "default_duration": 55,
        "description": "Основы химии, реакции и подготовка к практическим занятиям.",
    },
    {
        "name": "Биология",
        "color": "#22c55e",
        "default_duration": 50,
        "description": "Подготовка к олимпиадам, контрольным и ЕГЭ по биологии.",
    },
    {
        "name": "История",
        "color": "#f59e0b",
        "default_duration": 45,
        "description": "Хронология, работа с источниками и подготовка к итоговым работам.",
    },
    {
        "name": "Обществознание",
        "color": "#fb7185",
        "default_duration": 45,
        "description": "Экономика, право и обществоведческие темы для экзаменов.",
    },
    {
        "name": "География",
        "color": "#0ea5e9",
        "default_duration": 45,
        "description": "Картография, природные зоны и подготовка к контрольным.",
    },
    {
        "name": "Информатика",
        "color": "#4ade80",
        "default_duration": 60,
        "description": "Программирование, алгоритмы и цифровая грамотность.",
    },
)


def _resolve_moscow_timezone():
    """Return a tzinfo for Europe/Moscow with a graceful fallback."""
    if ZoneInfo is None:  # pragma: no cover - happens only on very old Python builds
        return timezone(timedelta(hours=3))

    try:
        return ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:  # Windows & minimal installations without tzdata
        return timezone(timedelta(hours=3))


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory for the tutor diary service."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY="dev",
        SQLALCHEMY_DATABASE_URI="sqlite:///tutor_diary.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    upload_root = Path(app.instance_path) / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    app.config.setdefault("UPLOAD_FOLDER", str(upload_root))

    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    if migrate is not None:
        migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .views import bp as diary_bp

    app.register_blueprint(diary_bp)

    moscow_timezone = _resolve_moscow_timezone()
    app.config.setdefault("MOSCOW_TIMEZONE", moscow_timezone)

    @app.context_processor
    def inject_now():
        def now_moscow():
            tz = app.config.get("MOSCOW_TIMEZONE", moscow_timezone)
            return datetime.now(tz)

        return {"now": now_moscow, "now_moscow": now_moscow}

    def ensure_admin_account() -> None:
        """Create the default admin account if it doesn't exist."""
        from sqlalchemy import text

        from .models import Admin

        admin = Admin.query.filter_by(username="admin").first()
        if not admin:
            admin = Admin(username="admin")
            admin.set_password("admin")
            db.session.add(admin)
            db.session.commit()

        db.session.execute(
            text("UPDATE teachers SET owner_id = :admin_id WHERE owner_id IS NULL"),
            {"admin_id": admin.id},
        )
        db.session.commit()

    def ensure_default_subjects() -> None:
        """Seed the catalog with the core school subjects if missing."""
        from .models import SubjectSetting

        existing_subjects = {
            subject.name for subject in SubjectSetting.query.with_entities(SubjectSetting.name).all()
        }
        created = False
        for subject in DEFAULT_SUBJECTS:
            if subject["name"] not in existing_subjects:
                db.session.add(
                    SubjectSetting(
                        name=subject["name"],
                        color=subject["color"],
                        default_duration=subject["default_duration"],
                        description=subject["description"],
                    )
                )
                created = True
        if created:
            db.session.commit()

    def ensure_teacher_subject_assignments() -> None:
        """Guarantee that each teacher works with one to three predefined subjects."""
        from .models import SubjectSetting, Teacher

        teachers = Teacher.query.options(joinedload(Teacher.subjects)).all()
        if not teachers:
            return

        available_subjects = (
            SubjectSetting.query.order_by(SubjectSetting.name.asc()).all()
        )
        if not available_subjects:
            return

        available_by_id = {subject.id: subject for subject in available_subjects if subject.id}
        if not available_by_id:
            return

        first_subject_id = next(iter(available_by_id))

        for teacher in teachers:
            unique_subject_ids: list[int] = []
            for subject in teacher.subjects:
                if not subject or subject.id not in available_by_id:
                    continue
                if subject.id in unique_subject_ids:
                    continue
                unique_subject_ids.append(subject.id)

            if len(unique_subject_ids) > 3:
                unique_subject_ids = unique_subject_ids[:3]

            if not unique_subject_ids and first_subject_id is not None:
                unique_subject_ids = [first_subject_id]

            teacher.subjects = [available_by_id[sid] for sid in unique_subject_ids]

        db.session.commit()

    def ensure_schema_upgrades() -> None:
        """Apply lightweight schema adjustments for legacy databases."""
        from sqlalchemy import inspect, text

        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())

        if "students" in table_names:
            student_columns = {column["name"] for column in inspector.get_columns("students")}
            if "teacher_id" not in student_columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN teacher_id INTEGER"))
                db.session.commit()
            if "email" not in student_columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN email VARCHAR(120)"))
                db.session.commit()
            if "phone" not in student_columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN phone VARCHAR(50)"))
                db.session.commit()
            if "avatar_path" not in student_columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN avatar_path VARCHAR(255)"))
                db.session.commit()

        if "teachers" in table_names:
            teacher_columns = {column["name"] for column in inspector.get_columns("teachers")}
            altered = False
            if "max_students" not in teacher_columns:
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN max_students INTEGER"))
                altered = True
            if "owner_id" not in teacher_columns:
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN owner_id INTEGER"))
                altered = True
            if "last_name" not in teacher_columns:
                db.session.execute(
                    text("ALTER TABLE teachers ADD COLUMN last_name VARCHAR(120)")
                )
                altered = True
            if "first_name" not in teacher_columns:
                db.session.execute(
                    text("ALTER TABLE teachers ADD COLUMN first_name VARCHAR(80)")
                )
                altered = True
            if "patronymic" not in teacher_columns:
                db.session.execute(
                    text("ALTER TABLE teachers ADD COLUMN patronymic VARCHAR(120)")
                )
                altered = True
            if altered:
                db.session.commit()
            db.session.execute(
                text("UPDATE teachers SET max_students = COALESCE(max_students, 10)")
            )
            db.session.commit()

        if "sessions" in table_names:
            session_columns = {column["name"] for column in inspector.get_columns("sessions")}
            altered = False
            if "fee_amount" not in session_columns:
                db.session.execute(
                    text("ALTER TABLE sessions ADD COLUMN fee_amount NUMERIC(10, 2)")
                )
                altered = True
            if "payment_status" not in session_columns:
                db.session.execute(
                    text(
                        "ALTER TABLE sessions ADD COLUMN payment_status VARCHAR(20)"
                    )
                )
                db.session.execute(
                    text(
                        "UPDATE sessions SET payment_status = 'unpaid' "
                        "WHERE payment_status IS NULL"
                    )
                )
                altered = True
            if "payment_id" not in session_columns:
                db.session.execute(
                    text("ALTER TABLE sessions ADD COLUMN payment_id INTEGER")
                )
                altered = True
            if "join_link" not in session_columns:
                db.session.execute(
                    text("ALTER TABLE sessions ADD COLUMN join_link VARCHAR(255)")
                )
                altered = True
            if altered:
                db.session.commit()

        if "assignment_attachments" not in table_names:
            db.session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS assignment_attachments ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "assignment_id INTEGER NOT NULL REFERENCES assignments(id)"
                    " ON DELETE CASCADE,"
                    "title VARCHAR(255) NOT NULL,"
                    "url VARCHAR(255),"
                    "created_at DATETIME"
                    ")"
                )
            )
            db.session.commit()

    def bootstrap_database() -> None:
        """Ensure tables and the default admin account exist."""
        db.create_all()
        ensure_schema_upgrades()
        ensure_admin_account()
        ensure_default_subjects()
        ensure_teacher_subject_assignments()

    with app.app_context():
        bootstrap_database()

    @app.cli.command("init-db")
    def init_db() -> None:
        """Initialize the database with the required tables."""
        bootstrap_database()
        print("Initialized the database.")

    return app
