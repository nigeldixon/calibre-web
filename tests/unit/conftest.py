"""Fixtures shared by the application unit tests."""
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_babel import Babel


@pytest.fixture
def app():
    """A minimal Flask app with a controllable reverse-proxy flag."""
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.secret_key = "unit-test-secret"
    application.wsgi_app = SimpleNamespace(is_proxied=False)
    Babel(application)  # Flask-Babel's gettext expects its extension to be registered
    return application
