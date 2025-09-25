# app/__init__.py
import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# ─── shared extensions ───────────────────────────────────────────
db = SQLAlchemy()
migrate = Migrate()


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

    app.register_blueprint(ticket_bp, url_prefix="/api")
    app.register_blueprint(category_bp, url_prefix="/api")
    app.register_blueprint(notification_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp, url_prefix="/api")

    # 5) health route
    @app.route("/")
    def health():
        return jsonify(status="ok"), 200

    return app
