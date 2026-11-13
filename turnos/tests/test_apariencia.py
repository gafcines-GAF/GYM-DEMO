import io
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from turnos.models import IMAGENES_BASE, ParametrosGimnasio

User = get_user_model()
MEDIA_TEMP = tempfile.mkdtemp(prefix="gym_media_test_")


def png(nombre="nuevo.png", color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(buf, "PNG")
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA_TEMP)
class AparienciaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Copia las imágenes originales al MEDIA temporal
        shutil.copytree(Path(settings.BASE_DIR) / "media" / "sitio" / "default",
                        Path(MEDIA_TEMP) / "sitio" / "default", dirs_exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_TEMP, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.profe = User.objects.create_user("profe", password="x", is_staff=True, is_superuser=True)

    def datos_admin(self, **extra):
        p = ParametrosGimnasio.load()
        d = {"cupo_maximo": p.cupo_maximo, "hora_apertura": p.hora_apertura,
             "hora_cierre": p.hora_cierre, "umbral_amarillo": p.umbral_amarillo,
             "dias_anticipacion_cambio": p.dias_anticipacion_cambio,
             "nombre_gimnasio": "Fuerza Total", "texto_pie": "Av. Siempreviva 742"}
        d.update(extra)
        return d

    def test_logueado_ve_encabezado_y_pie(self):
        self.client.force_login(self.profe)
        r = self.client.get(reverse("turnos:inicio"), follow=True)
        self.assertContains(r, 'class="encabezado"')
        self.assertContains(r, 'class="pie"')
        for ruta in IMAGENES_BASE.values():
            self.assertContains(r, ruta)

    def test_login_no_muestra_encabezado_ni_pie(self):
        r = self.client.get(reverse("login"))
        self.assertNotContains(r, 'class="encabezado"')
        self.assertNotContains(r, 'class="pie"')

    def test_profesor_cambia_imagen_y_se_borra_la_anterior(self):
        self.client.force_login(self.profe)
        url = reverse("admin:turnos_parametrosgimnasio_change", args=[1])
        ParametrosGimnasio.load()

        self.client.post(url, self.datos_admin(logo=png("uno.png")))
        p = ParametrosGimnasio.load()
        primera = p.logo.name
        self.assertTrue(primera.startswith("sitio/uno"))
        self.assertEqual(p.nombre_gimnasio, "Fuerza Total")

        self.client.post(url, self.datos_admin(logo=png("dos.png", (0, 0, 200))))
        p = ParametrosGimnasio.load()
        self.assertTrue(p.logo.name.startswith("sitio/dos"))
        self.assertFalse(p.logo.storage.exists(primera))           # la vieja se borró

        r = self.client.get(reverse("turnos:inicio"), follow=True)
        self.assertContains(r, p.logo.url)

    def test_restaurar_originales_no_borra_las_base(self):
        self.client.force_login(self.profe)
        url = reverse("admin:turnos_parametrosgimnasio_change", args=[1])
        ParametrosGimnasio.load()
        self.client.post(url, self.datos_admin(imagen_pie=png("pie_nuevo.png")))
        subida = ParametrosGimnasio.load().imagen_pie.name

        self.client.post(url, self.datos_admin(restaurar_imagenes="on"))
        p = ParametrosGimnasio.load()
        self.assertEqual(p.imagen_pie.name, IMAGENES_BASE["imagen_pie"])
        self.assertFalse(p.imagen_pie.storage.exists(subida))
        self.assertTrue(p.imagen_pie.storage.exists(IMAGENES_BASE["imagen_pie"]))

    def test_rechaza_archivo_que_no_es_imagen(self):
        self.client.force_login(self.profe)
        url = reverse("admin:turnos_parametrosgimnasio_change", args=[1])
        ParametrosGimnasio.load()
        falso = SimpleUploadedFile("logo.png", b"no soy una imagen", content_type="image/png")
        r = self.client.post(url, self.datos_admin(logo=falso))
        self.assertEqual(r.status_code, 200)                        # vuelve el form con error
        self.assertEqual(ParametrosGimnasio.load().logo.name, IMAGENES_BASE["logo"])
