from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from finanzas.views import _celda

User = get_user_model()


class SeguridadTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)

    def test_alta_de_alumno_rechaza_contrasena_debil(self):
        r = self.client.post(reverse("admin:turnos_alumno_add"), {
            "username": "debil", "password": "123456", "first_name": "", "last_name": "",
            "email": "", "dias_por_semana": 2, "activo": "on",
            "horarios-TOTAL_FORMS": 0, "horarios-INITIAL_FORMS": 0,
            "descuentos-TOTAL_FORMS": 0, "descuentos-INITIAL_FORMS": 0})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="debil").exists())

    def test_fechas_fuera_de_rango_dan_404(self):
        self.assertEqual(self.client.get("/dia/0001-01-01/").status_code, 404)
        self.assertEqual(self.client.get("/mes/9999/12/").status_code, 404)

    def test_redireccion_next_externa_se_ignora(self):
        r = self.client.post(reverse("turnos:baja", args=[999999]),
                             {"next": "https://sitio-malicioso.com/"})
        self.assertNotIn("sitio-malicioso", r.get("Location", ""))

    def test_csv_neutraliza_formulas(self):
        self.assertEqual(_celda("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(_celda("@SUM(1)"), "'@SUM(1)")
        self.assertEqual(_celda("Pérez, Ana"), "Pérez, Ana")

    @override_settings(DEBUG=False)
    def test_datos_demo_bloqueados_en_produccion(self):
        with self.assertRaisesMessage(CommandError, "DEBUG=False"):
            call_command("cargar_demo")
        with self.assertRaisesMessage(CommandError, "DEBUG=False"):
            call_command("cargar_demo_finanzas")
