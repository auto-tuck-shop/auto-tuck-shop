"""
Development settings for auto-tuck-shop project.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

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
