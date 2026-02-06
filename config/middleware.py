from django.db import connection
from django.http import HttpResponse


class HealthCheckMiddleware:
    """Respond to /healthz before Django's ALLOWED_HOSTS validation.

    Fly.io health checks hit the machine via internal IP, which isn't in
    ALLOWED_HOSTS. This middleware runs first and short-circuits the request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                return HttpResponse("ok")
            except Exception as e:
                return HttpResponse(f"unhealthy: {e}", status=500)
        return self.get_response(request)
