from datetime import datetime, timedelta, timezone

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

try:  # Flask-Migrate is optional in local setups
    from flask_migrate import Migrate
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal installs
    Migrate = None  # type: ignore[assignment]

try:  # zoneinfo is available in the stdlib but may miss tzdata on some OSes
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - keep compatibility with exotic Python builds
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]


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

    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    if migrate is not None:
        migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .views import bp as diary_bp

    app.register_blueprint(diary_bp)

    moscow_timezone = _resolve_moscow_timezone()

    @app.context_processor
    def inject_now():
        def now_moscow():
            return datetime.now(moscow_timezone)

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

        if "teachers" in table_names:
            teacher_columns = {column["name"] for column in inspector.get_columns("teachers")}
            altered = False
            if "max_students" not in teacher_columns:
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN max_students INTEGER"))
                altered = True
            if "owner_id" not in teacher_columns:
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN owner_id INTEGER"))
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
            if altered:
                db.session.commit()

    def bootstrap_database() -> None:
        """Ensure tables and the default admin account exist."""
        db.create_all()
        ensure_schema_upgrades()
        ensure_admin_account()
        ensure_default_subjects()

    with app.app_context():
        bootstrap_database()

    @app.cli.command("init-db")
    def init_db() -> None:
        """Initialize the database with the required tables."""
        bootstrap_database()
        print("Initialized the database.")

    return app
