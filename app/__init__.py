import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# ─── shared extensions ───────────────────────────────────────────
db      = SQLAlchemy()
migrate = Migrate()

# ─── factory ─────────────────────────────────────────────────────
def create_app(config_path: str | None = None):
    """
    Factory pattern – returns a configured Flask app.
    """
    app = Flask(__name__)

    # 1) CORS: allow any origin on /api/*
    CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

    # 2) load configuration
    config_obj = config_path or os.getenv("FLASK_CONFIG", "config.DevelopmentConfig")
    app.config.from_object(config_obj)

    # fallback DB URI
    app.config.setdefault(
        "SQLALCHEMY_DATABASE_URI",
        os.getenv("DATABASE_URL", "sqlite:///ticket.db")  # ← changed from ticket.db
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # 3) init extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # 4) blueprints
    from app.ticket_routes import ticket_bp          # Ticket CRUD + Files + Tags
    from app.category_routes import category_bp      # Ticket Categories
    from app.notification_route import notification_bp

    app.register_blueprint(ticket_bp, url_prefix="/api")
    app.register_blueprint(category_bp, url_prefix="/api")
    app.register_blueprint(notification_bp, url_prefix="/api")

    # 5) health route
    @app.route("/")
    def health():
        return jsonify(status="ok"), 200

    return app
