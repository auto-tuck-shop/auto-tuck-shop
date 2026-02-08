from .production import *  # noqa

# Override for staging
DEBUG = True  # Easier troubleshooting
USE_MOCK_WHATSAPP = True  # Mock WhatsApp only

# Test API for staging integration tests
ENABLE_TEST_API = True
TEST_API_KEY = env("TEST_API_KEY", default="")
INSTALLED_APPS += ["apps.testing"]  # noqa: F405

# R2 Configuration for staging
# - Uploads go to staging bucket: auto-tuck-shop-staging
# - Tests can read from production bucket for test data
R2_BUCKET_NAME = env("R2_BUCKET_NAME", default="auto-tuck-shop-staging")

# Sentry environment tracking
SENTRY_ENVIRONMENT = "staging"

# Relax security for staging (optional - makes testing easier)
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
