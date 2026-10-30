from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AccessLog


def client_ip(request):
    """IP del cliente; respeta X-Forwarded-For si la plataforma corre detrás de un proxy."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def _log(request, event, user=None, username=""):
    AccessLog.objects.create(
        user=user,
        username=(user.get_username() if user else username)[:150],
        event=event,
        ip_address=client_ip(request) if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255] if request else "",
        session_key=(request.session.session_key or "") if request else "",
    )


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    _log(request, AccessLog.EVENT_LOGIN, user=user)


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user is not None:
        _log(request, AccessLog.EVENT_LOGOUT, user=user)


@receiver(user_login_failed)
def log_login_failed(sender, credentials, request=None, **kwargs):
    _log(request, AccessLog.EVENT_FAILED, username=credentials.get("username", ""))
