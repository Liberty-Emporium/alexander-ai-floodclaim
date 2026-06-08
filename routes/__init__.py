"""Route blueprints for FloodClaims Pro."""
from routes.api import bp as api_bp
from routes.auth import bp as auth_bp
from routes.billing import bp as billing_bp
from routes.pipeline import bp as pipeline_bp
from routes.rooms import bp as rooms_bp
from routes.feedback import bp as feedback_bp
from routes.customer import bp as customer_bp
from routes.claims import bp as claims_bp
from routes.photos import bp as photos_bp
from routes.reports import bp as reports_bp
from routes.admin import bp as admin_bp
from routes.willie import bp as willie_bp
from routes.schedule import bp as schedule_bp
from routes.aquila import bp as aquila_bp
from routes.analytics import bp as analytics_bp
from routes.portal import bp as portal_bp
from routes.enhanced import bp as enhanced_bp
from routes.password_reset import bp as password_reset_bp


def register_blueprints(app):
    """Register all blueprints with the Flask app."""
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(pipeline_bp)
    app.register_blueprint(rooms_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(claims_bp)
    app.register_blueprint(photos_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(willie_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(aquila_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(enhanced_bp)
    app.register_blueprint(password_reset_bp)
