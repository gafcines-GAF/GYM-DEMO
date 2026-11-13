from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from turnos import services
from turnos.models import Alumno, Clase, HorarioFijo, Inscripcion, ParametrosGimnasio, calcular_nivel

User = get_user_model()
ACTIVA, CANCELADA = Inscripcion.Estado.ACTIVA, Inscripcion.Estado.CANCELADA


def crear_alumno(nombre, dias=3):
    u = User.objects.create_user(nombre, password="x")
    return Alumno.objects.create(usuario=u, dias_por_semana=dias)


def mes_futuro(meses=2):
    hoy = timezone.localdate()
    m = hoy.month - 1 + meses
    return hoy.year + m // 12, m % 12 + 1


def primer(dia_semana, anio, mes):
    return next(services.fechas_del_dia(dia_semana, date(anio, mes, 1), date(anio, mes, 28)))


class Base(TestCase):
    def setUp(self):
        self.params = ParametrosGimnasio.load()          # cupo 7
        self.profe = User.objects.create_user("profe", password="x", is_staff=True)
        self.ana = crear_alumno("ana")
        hoy = timezone.localdate()
        self.lunes = hoy + timedelta(days=14 - hoy.weekday())   # lunes de dentro de 2 semanas

    def llenar(self, fecha, hora, n=None):
        n = self.params.cupo_maximo if n is None else n
        for i in range(n):
            a = crear_alumno(f"relleno_{fecha}_{hora}_{i}")
            services.asignar_por_profesor(self.profe, a, fecha, hora)


class ColoresTests(TestCase):
    def test_niveles(self):
        self.assertEqual(calcular_nivel(4, 7, 70), "libre")
        self.assertEqual(calcular_nivel(5, 7, 70), "medio")
        self.assertEqual(calcular_nivel(7, 7, 70), "lleno")
        self.assertEqual(calcular_nivel(8, 7, 70), "sobrecupo")


class CupoTests(Base):
    def test_alumno_no_entra_en_clase_llena(self):
        self.llenar(self.lunes, 10)
        insc = services.asignar_por_profesor(self.profe, self.ana, self.lunes + timedelta(1), 10)
        with self.assertRaises(services.CupoLlenoError):
            services.cambiar_puntual(self.ana.usuario, insc.inscripciones[0].pk, self.lunes, 10)
        # La transacción se revirtió: el turno original sigue activo
        self.assertEqual(Inscripcion.objects.get(pk=insc.inscripciones[0].pk).estado, ACTIVA)

    def test_profesor_asigna_en_sobrecupo_con_advertencia(self):
        self.llenar(self.lunes, 10)
        res = services.asignar_por_profesor(self.profe, self.ana, self.lunes, 10)
        self.assertTrue(res.inscripciones[0].sobrecupo)
        self.assertIn("SOBRECUPO", res.advertencias[0])
        clase = Clase.objects.get(fecha=self.lunes, hora=10)
        self.assertEqual(clase.nivel_ocupacion, "sobrecupo")

    def test_una_clase_por_dia(self):
        services.asignar_por_profesor(self.profe, self.ana, self.lunes, 10)
        with self.assertRaises(services.ReglaNegocioError):
            services.asignar_por_profesor(self.profe, self.ana, self.lunes, 18)

    def test_fin_de_semana_y_horario_invalidos(self):
        sabado = self.lunes + timedelta(days=5)
        with self.assertRaises(services.ReglaNegocioError):
            services.asignar_por_profesor(self.profe, self.ana, sabado, 10)
        with self.assertRaises(services.ReglaNegocioError):
            services.asignar_por_profesor(self.profe, self.ana, self.lunes, 22)


class CambioPuntualTests(Base):
    def test_cambio_mismo_dia_otra_hora(self):
        original = services.asignar_por_profesor(self.profe, self.ana, self.lunes, 10).inscripciones[0]
        nueva = services.cambiar_puntual(self.ana.usuario, original.pk, self.lunes, 18).inscripciones[0]
        original.refresh_from_db()
        self.assertEqual(original.estado, CANCELADA)
        self.assertEqual(nueva.reemplaza_a, original)
        self.assertEqual(nueva.origen, Inscripcion.Origen.CAMBIO_PUNTUAL)

    def test_anticipacion_minima(self):
        hoy = timezone.localdate()
        clase = Clase.objects.create(fecha=hoy, hora=10)
        insc = Inscripcion.objects.create(alumno=self.ana, clase=clase)
        with self.assertRaisesMessage(services.ReglaNegocioError, "anticipación"):
            services.cambiar_puntual(self.ana.usuario, insc.pk, self.lunes, 10)

    def test_no_puede_mover_turno_ajeno(self):
        beto = crear_alumno("beto")
        insc = services.asignar_por_profesor(self.profe, beto, self.lunes, 10).inscripciones[0]
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            services.cambiar_puntual(self.ana.usuario, insc.pk, self.lunes, 18)


class RecurrenciaTests(Base):
    def setUp(self):
        super().setUp()
        self.anio, self.mes = mes_futuro()
        inicio = date(self.anio, self.mes, 1)
        self.horario = HorarioFijo.objects.create(alumno=self.ana, dia_semana=0, hora=10,
                                                  vigente_desde=inicio)
        services.generar_mes(self.anio, self.mes)

    def activas(self, **kw):
        return Inscripcion.objects.filter(alumno=self.ana, estado=ACTIVA, **kw)

    def test_generar_mes_replica_y_es_idempotente(self):
        lunes_del_mes = list(services.fechas_del_dia(
            0, date(self.anio, self.mes, 1), services.fin_de_mes(date(self.anio, self.mes, 1))))
        self.assertEqual(self.activas().count(), len(lunes_del_mes))
        services.generar_mes(self.anio, self.mes)
        self.assertEqual(self.activas().count(), len(lunes_del_mes))

    def test_generar_mes_respeta_cancelaciones(self):
        primera = self.activas().first()
        services.dar_de_baja(self.ana.usuario, primera.pk)
        antes = self.activas().count()
        services.generar_mes(self.anio, self.mes)
        self.assertEqual(self.activas().count(), antes)

    def test_cambio_permanente(self):
        desde = primer(0, self.anio, self.mes)
        res = services.cambiar_permanente(self.ana.usuario, self.horario.pk, 2, 18, desde)
        self.assertFalse(self.activas(clase__hora=10).exists())
        self.assertTrue(all(i.clase.hora == 18 and i.fecha.weekday() == 2 for i in self.activas()))
        self.assertEqual(len(res.inscripciones), self.activas().count())
        # La plantilla vieja empezaba ese mismo día → se reemplazó por la nueva
        self.assertEqual(list(self.ana.horarios_vigentes(desde).values_list("dia_semana", "hora")),
                         [(2, 18)])

    def test_cambio_permanente_todo_o_nada(self):
        miercoles = list(services.fechas_del_dia(
            2, date(self.anio, self.mes, 1), services.fin_de_mes(date(self.anio, self.mes, 1))))
        self.llenar(miercoles[-1], 18)                  # el último miércoles está lleno
        antes = set(self.activas().values_list("pk", flat=True))
        with self.assertRaises(services.CupoLlenoError):
            services.cambiar_permanente(self.ana.usuario, self.horario.pk, 2, 18,
                                        primer(0, self.anio, self.mes))
        self.assertEqual(set(self.activas().values_list("pk", flat=True)), antes)
        self.horario.refresh_from_db()
        self.assertIsNone(self.horario.vigente_hasta)

    def test_clase_cancelada_se_saltea(self):
        lunes = primer(0, self.anio, self.mes)
        otro = crear_alumno("otro")
        HorarioFijo.objects.create(alumno=otro, dia_semana=0, hora=10,
                                   vigente_desde=date(self.anio, self.mes, 1))
        services.alternar_cancelacion_clase(self.profe, lunes, 10)
        res = services.generar_mes(self.anio, self.mes)
        self.assertFalse(Inscripcion.objects.filter(alumno=otro, fecha=lunes).exists())
        self.assertTrue(any("cancelada" in a for a in res.advertencias))


class VistasTests(Base):
    def test_pantallas_responden(self):
        services.asignar_por_profesor(self.profe, self.ana, self.lunes, 10)
        self.client.force_login(self.ana.usuario)
        self.assertRedirects(self.client.get("/"), reverse("turnos:dia", args=[services_hoy()]),
                             fetch_redirect_response=False)
        for url in [reverse("turnos:dia", args=[self.lunes]),
                    reverse("turnos:semana", args=[self.lunes]),
                    reverse("turnos:mes", args=[self.lunes.year, self.lunes.month])]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        r = self.client.get(reverse("turnos:dia", args=[self.lunes]))
        self.assertContains(r, "Tu turno")
        self.assertContains(r, "franja nivel-libre")

    def test_alumno_cambia_por_la_vista(self):
        insc = services.asignar_por_profesor(self.profe, self.ana, self.lunes, 10).inscripciones[0]
        self.client.force_login(self.ana.usuario)
        r = self.client.post(reverse("turnos:cambiar", args=[insc.pk]),
                             {"fecha": self.lunes.isoformat(), "hora": 18, "alcance": "PUNTUAL"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Inscripcion.objects.filter(alumno=self.ana, clase__hora=18, estado=ACTIVA).exists())

    def test_alumno_no_ve_detalle_de_clase(self):
        self.client.force_login(self.ana.usuario)
        r = self.client.get(reverse("turnos:clase", args=[self.lunes, 10]))
        self.assertEqual(r.status_code, 302)        # redirige al login de admin

    def test_profesor_ve_detalle_y_asigna_en_sobrecupo(self):
        self.llenar(self.lunes, 10)
        self.client.force_login(self.profe)
        r = self.client.post(reverse("turnos:asignar", args=[self.lunes, 10]),
                             {"alumno": self.ana.pk}, follow=True)
        self.assertContains(r, "SOBRECUPO")
        self.assertContains(r, "8/7")


def services_hoy():
    from turnos.calendario import hoy_habil
    return hoy_habil()


class AdminAlumnoTests(Base):
    def datos(self, **extra):
        d = {"username": "nuevo", "password": "clave-segura-1", "first_name": "Nuevo",
             "last_name": "Alumno", "email": "", "dias_por_semana": 2, "telefono": "", "activo": "on",
             "horarios-TOTAL_FORMS": 3, "horarios-INITIAL_FORMS": 0,
             # Inline de descuentos que agrega la app finanzas (visible para superusuarios)
             "descuentos-TOTAL_FORMS": 0, "descuentos-INITIAL_FORMS": 0,
             "horarios-0-dia_semana": 0, "horarios-0-hora": 18, "horarios-0-vigente_desde": "2026-01-01",
             "horarios-1-dia_semana": 2, "horarios-1-hora": 18, "horarios-1-vigente_desde": "2026-01-01"}
        d.update(extra)
        return d

    def setUp(self):
        super().setUp()
        self.profe.is_superuser = True
        self.profe.save()
        self.client.force_login(self.profe)

    def test_crea_alumno_con_usuario_y_semana_tipo(self):
        r = self.client.post(reverse("admin:turnos_alumno_add"), self.datos())
        self.assertEqual(r.status_code, 302)
        alumno = Alumno.objects.get(usuario__username="nuevo")
        self.assertTrue(alumno.usuario.check_password("clave-segura-1"))
        self.assertEqual(alumno.horarios.count(), 2)

    def test_no_permite_mas_dias_que_los_contratados(self):
        r = self.client.post(reverse("admin:turnos_alumno_add"), self.datos(**{
            "horarios-2-dia_semana": 4, "horarios-2-hora": 9, "horarios-2-vigente_desde": "2026-01-01"}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "contratados 2 días")
        self.assertFalse(User.objects.filter(username="nuevo").exists())   # sin usuario huérfano
