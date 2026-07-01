from flask import Blueprint

bp = Blueprint("trails", __name__)

from . import routes  # noqa: E402,F401
