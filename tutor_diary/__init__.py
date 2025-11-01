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

        return {"now": datetime.utcnow}

    def ensure_admin_account() -> None:
        """Create the default teacher account if it doesn't exist."""
        from .models import Teacher

        if not Teacher.query.filter_by(username="admin").first():
            teacher = Teacher(username="admin")
            teacher.set_password("admin")
            db.session.add(teacher)
            db.session.commit()

    def bootstrap_database() -> None:
        """Ensure tables and the default admin account exist."""
        db.create_all()
        ensure_admin_account()

    with app.app_context():
        bootstrap_database()

    @app.cli.command("init-db")
    def init_db() -> None:
        """Initialize the database with the required tables."""
        bootstrap_database()
        print("Initialized the database.")

    return app
