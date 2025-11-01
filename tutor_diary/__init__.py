from flask import Flask
from flask_sqlalchemy import SQLAlchemy

try:  # Flask-Migrate is optional in local setups
    from flask_migrate import Migrate
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal installs
    Migrate = None  # type: ignore[assignment]


db = SQLAlchemy()
migrate = Migrate() if Migrate is not None else None


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

    @app.context_processor
    def inject_now():
        from datetime import datetime
        from zoneinfo import ZoneInfo

        def now_moscow():
            return datetime.now(ZoneInfo("Europe/Moscow"))

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

    def bootstrap_database() -> None:
        """Ensure tables and the default admin account exist."""
        db.create_all()
        ensure_schema_upgrades()
        ensure_admin_account()

    with app.app_context():
        bootstrap_database()

    @app.cli.command("init-db")
    def init_db() -> None:
        """Initialize the database with the required tables."""
        bootstrap_database()
        print("Initialized the database.")

    return app
