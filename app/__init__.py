import click
from flask import Flask, redirect, url_for

from .config import Config
from .extensions import db, login_manager, migrate


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from . import models  # noqa: F401
    from .storage import build_storage

    app.extensions["storage"] = build_storage(app)

    from .auth import bp as auth_bp
    from .trails import bp as trails_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(trails_bp)

    @app.route("/health")
    def health():
        return {"status": "ok"}

    @app.cli.command("init-user")
    def init_user():
        """Creeaza userul admin din ADMIN_USERNAME / ADMIN_PASSWORD daca nu exista."""
        from .models import User

        username = app.config["ADMIN_USERNAME"]
        password = app.config["ADMIN_PASSWORD"]
        if not password:
            click.echo("ADMIN_PASSWORD nesetat — sar peste crearea userului.")
            return
        if User.query.filter_by(username=username).first():
            click.echo(f"Userul '{username}' exista deja.")
            return
        u = User(username=username)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"User creat: {username}")

    return app
