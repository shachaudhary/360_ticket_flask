# app/__init__.py  (minor whitespace touch for deployment)
import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
# ─── shared extensions ───────────────────────────────────────────
db = SQLAlchemy()
migrate = Migrate()
scheduler = BackgroundScheduler()

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
    from app.project_routes import project_bp

    app.register_blueprint(ticket_bp, url_prefix="/api")
    app.register_blueprint(category_bp, url_prefix="/api")
    app.register_blueprint(notification_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(stats_bp, url_prefix="/api")
    app.register_blueprint(form_entries_blueprint, url_prefix="/api")
    app.register_blueprint(google_review_routes, url_prefix="/api")
    app.register_blueprint(mailgun_bp, url_prefix="/api")
    app.register_blueprint(project_bp, url_prefix="/api")

    # 5) health route
    @app.route("/")
    def health():
        return jsonify(status="ok"), 200

    # 6) Setup scheduled tasks (cron jobs)
    def setup_scheduler(app):
        """Setup background scheduler for periodic tasks"""
        from app.ticket_routes import _process_emails_internal
        
        def run_email_processing():
            """Wrapper to run email processing in app context"""
            with app.app_context():
                try:
                    result = _process_emails_internal()
                    if result.get("status") == "success":
                        print(f"✅ Scheduled email processing: {result.get('message')}")
                    else:
                        print(f"⚠️ Scheduled email processing: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    print(f"❌ Scheduled email processing error: {e}")
        
        # Schedule email processing to run every 10 minutes
        scheduler.add_job(
            func=run_email_processing,
            trigger=CronTrigger(minute='*/10'),  # Every 10 minutes
            id='process_emails_job',
            name='Process emails from last 10 minutes',
            replace_existing=True
        )
        
        # Start scheduler
        if not scheduler.running:
            scheduler.start()
            print("✅ Scheduler started - Email processing will run every 10 minutes")
    
    Initialize scheduler when app is created
    with app.app_context():
        setup_scheduler(app)
    
    # Shutdown scheduler when app closes
    import atexit
    atexit.register(lambda: scheduler.shutdown() if scheduler.running else None)

    return app
