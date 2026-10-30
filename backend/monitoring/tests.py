from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse

from .models import AccessLog, Profile


class ControlUsuariosTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("jefe", "jefe@x.cl", "clave-segura-1")
        Profile.objects.update_or_create(user=self.admin, defaults={"role": Profile.ROLE_GLOBAL})
        self.operador = User.objects.create_user("operador", "op@x.cl", "clave-segura-2")

    def login(self, username, password):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def test_registra_login_fallido_y_logout(self):
        self.login("jefe", "mala")
        self.login("jefe", "clave-segura-1")
        self.client.post(reverse("logout"))
        events = list(AccessLog.objects.order_by("timestamp").values_list("event", flat=True))
        self.assertEqual(events, ["failed", "login", "logout"])
        self.assertEqual(AccessLog.objects.get(event="login").user, self.admin)

    def test_lista_conectados_y_cierra_sesion_ajena(self):
        otro = self.client_class()
        otro.post(reverse("login"), {"username": "operador", "password": "clave-segura-2"})
        self.login("jefe", "clave-segura-1")
        resp = self.client.get(reverse("monitoring:user-sessions"))
        self.assertContains(resp, "Usuarios conectados (2)")
        key = AccessLog.objects.get(username="operador", event="login").session_key
        self.client.post(reverse("monitoring:session-terminate", args=[key]))
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertEqual(otro.get(reverse("monitoring:dashboard")).status_code, 302)

    def test_operador_no_ve_accesos(self):
        self.login("operador", "clave-segura-2")
        self.assertEqual(self.client.get(reverse("monitoring:user-sessions")).status_code, 403)

    def test_editar_rol_y_contrasena(self):
        self.login("jefe", "clave-segura-1")
        url = reverse("monitoring:user-edit", args=[self.operador.pk])
        self.client.post(url, {"action": "profile", "first_name": "Op", "last_name": "", "email": "op@x.cl", "role": "admin"})
        self.assertEqual(Profile.objects.get(user=self.operador).role, "admin")
        self.client.post(url, {"action": "password", "new_password1": "Nueva-Clave-99", "new_password2": "Nueva-Clave-99"})
        self.operador.refresh_from_db()
        self.assertTrue(self.operador.check_password("Nueva-Clave-99"))

    def test_no_quita_rol_al_ultimo_global(self):
        self.login("jefe", "clave-segura-1")
        url = reverse("monitoring:user-edit", args=[self.admin.pk])
        resp = self.client.post(url, {"action": "profile", "first_name": "", "last_name": "", "email": "jefe@x.cl", "role": "owner"})
        self.assertContains(resp, "último usuario Global")
        self.assertEqual(Profile.objects.get(user=self.admin).role, "global")
