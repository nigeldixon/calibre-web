"""Fixtures shared by the application unit tests.

The cps package imports the optional Google Drive helpers through several
modules at import time. Those dependencies are not installed in the test
environment and none of the unit tests exercise Drive functionality, so a
small stub is installed before any cps module is imported.
"""
import sys
import types
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_babel import Babel


if "cps.gdriveutils" not in sys.modules:
    gdriveutils = types.ModuleType("cps.gdriveutils")
    gdriveutils.getFileFromEbooksFolder = lambda *args, **kwargs: None
    gdriveutils.do_gdrive_download = lambda *args, **kwargs: None
    gdriveutils.updateGdriveCalibreFromLocal = lambda *args, **kwargs: None
    sys.modules["cps.gdriveutils"] = gdriveutils


@pytest.fixture
def app():
    """A minimal Flask app with a controllable reverse-proxy flag."""
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.secret_key = "unit-test-secret"
    application.wsgi_app = SimpleNamespace(is_proxied=False)
    Babel(application)  # Flask-Babel's gettext expects its extension to be registered
    return application
