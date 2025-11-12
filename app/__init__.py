# app/__init__.py
import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from openai import OpenAI
# ─── shared extensions ───────────────────────────────────────────
db = SQLAlchemy()
migrate = Migrate()

llm_client = OpenAI(
    base_url="http://69.30.85.208:22148/v1/",
    api_key="389e5f28-62d0-46c6-9cbc-0099da90ff30"  # dummy key, Ollama doesn't validate it
)

# ─── factory ─────────────────────────────────────────────────────
def create_app(config_path: str | None = None):
    """
    Factory pattern – returns a configured Flask app.
    Only ProductionConfig or DevelopmentConfig via FLASK_CONFIG.
    """
    app = Flask(__name__)

    # 1) CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

    # 2) load configuration (default = ProductionConfig)
    config_obj = config_path or os.getenv("FLASK_CONFIG", "config.ProductionConfig")
    app.config.from_object(config_obj)


    # 3) init extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # 4) blueprints
    from app.ticket_routes import ticket_bp
    from app.category_routes import category_bp
    from app.notification_route import notification_bp
    from app.dashboard_routes import dashboard_bp
    from app.ticket_stats import stats_bp
    from app.form_entries import form_entries_blueprint
    from app.google_review_routes import google_review_routes
    from app.mailgun_routes import mailgun_bp

    app.register_blueprint(ticket_bp, url_prefix="/api")
    app.register_blueprint(category_bp, url_prefix="/api")
    app.register_blueprint(notification_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(stats_bp, url_prefix="/api")
    app.register_blueprint(form_entries_blueprint, url_prefix="/api")
    app.register_blueprint(google_review_routes, url_prefix="/api")
    app.register_blueprint(mailgun_bp, url_prefix="/api")

    # 5) health route
    @app.route("/")
    def health():
        return jsonify(status="ok"), 200

    return app
