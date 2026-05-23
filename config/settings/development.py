"""
Development settings for auto-tuck-shop project.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

INSTALLED_APPS += ["apps.testing", "apps.mock_whatsapp"]  # noqa: F405
ENABLE_TEST_API = True
TEST_API_KEY = env("TEST_API_KEY", default="")
USE_MOCK_WHATSAPP = True

# Use SQLite for local development if DATABASE_URL not set
DATABASES = {
    "default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3"),  # noqa: F405
}

# Disable WhiteNoise compression in development
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
