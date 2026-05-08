"""
Production-grade Python Flask Application
Demonstrates: Health checks, metrics, structured logging, config from env
"""

import os
import time
import logging
import json
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, g
from flask_sqlalchemy import SQLAlchemy
from prometheus_flask_exporter import PrometheusMetrics
import structlog

# ── Structured logging setup ──────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger()

# ── App factory ───────────────────────────────────────────────────────────────
def create_app(config=None):
    app = Flask(__name__)

    # Config from environment variables (12-factor app)
    app.config.update(
        ENV=os.getenv("APP_ENV", "development"),
        VERSION=os.getenv("APP_VERSION", "0.0.1"),
        DEBUG=os.getenv("DEBUG", "false").lower() == "true",
        SQLALCHEMY_DATABASE_URI=os.getenv(
            "DATABASE_URL", "sqlite:///app.db"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-in-prod"),
    )

    if config:
        app.config.update(config)

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)
    metrics = PrometheusMetrics(app)
    metrics.info("app_info", "Application info",
                 version=app.config["VERSION"],
                 env=app.config["ENV"])

    # ── Request logging middleware ─────────────────────────────────────────────
    @app.before_request
    def start_timer():
        g.start = time.time()

    @app.after_request
    def log_request(response):
        duration = round((time.time() - g.start) * 1000, 2)
        logger.info(
            "request",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_ms=duration,
            ip=request.remote_addr,
        )
        return response

    # ── Register blueprints ────────────────────────────────────────────────────
    app.register_blueprint(health_bp)
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    # ── Create DB tables ───────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()

    return app


# ── Models ────────────────────────────────────────────────────────────────────
db = SQLAlchemy()


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# ── Blueprints ────────────────────────────────────────────────────────────────
from flask import Blueprint

health_bp = Blueprint("health", __name__)
api_bp = Blueprint("api", __name__)


# ── Health endpoints (used by K8s liveness/readiness probes) ──────────────────
@health_bp.route("/health/live")
def liveness():
    """Kubernetes liveness probe — is the app running?"""
    return jsonify({"status": "UP", "timestamp": datetime.utcnow().isoformat()}), 200


@health_bp.route("/health/ready")
def readiness():
    """Kubernetes readiness probe — is the app ready to serve traffic?"""
    try:
        db.session.execute(db.text("SELECT 1"))
        db_status = "UP"
    except Exception as e:
        logger.error("database_check_failed", error=str(e))
        return jsonify({"status": "DOWN", "db": "DOWN"}), 503

    return jsonify({
        "status": "UP",
        "db": db_status,
        "version": os.getenv("APP_VERSION", "0.0.1"),
        "env": os.getenv("APP_ENV", "development"),
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@health_bp.route("/health/startup")
def startup():
    """Kubernetes startup probe — has the app finished initializing?"""
    return jsonify({"status": "UP"}), 200


# ── API endpoints ─────────────────────────────────────────────────────────────
@api_bp.route("/version")
def version():
    return jsonify({
        "app": "python-cicd-app",
        "version": os.getenv("APP_VERSION", "0.0.1"),
        "env": os.getenv("APP_ENV", "development"),
        "build_date": os.getenv("BUILD_DATE", "unknown"),
        "git_commit": os.getenv("GIT_COMMIT", "unknown"),
    })


@api_bp.route("/items", methods=["GET"])
def get_items():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    items = Item.query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "items": [item.to_dict() for item in items.items],
        "total": items.total,
        "page": items.page,
        "pages": items.pages,
    })


@api_bp.route("/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    item = Item.query.get_or_404(item_id)
    return jsonify(item.to_dict())


@api_bp.route("/items", methods=["POST"])
def create_item():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name is required"}), 400

    item = Item(name=data["name"], description=data.get("description", ""))
    db.session.add(item)
    db.session.commit()
    logger.info("item_created", item_id=item.id, name=item.name)
    return jsonify(item.to_dict()), 201


@api_bp.route("/items/<int:item_id>", methods=["PUT"])
def update_item(item_id):
    item = Item.query.get_or_404(item_id)
    data = request.get_json()
    if data.get("name"):
        item.name = data["name"]
    if "description" in data:
        item.description = data["description"]
    db.session.commit()
    return jsonify(item.to_dict())


@api_bp.route("/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    item = Item.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"message": f"Item {item_id} deleted"}), 200


# ── Error handlers ────────────────────────────────────────────────────────────
def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.error("internal_error", error=str(e))
        return jsonify({"error": "Internal server error"}), 500


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()
    register_error_handlers(app)
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=app.config["DEBUG"])
