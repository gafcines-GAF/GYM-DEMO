from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finanzas import consultas, services
from finanzas.models import Cuota, Descuento, Pago, ParametrosFinanzas, Tarifa, primer_dia
from turnos.models import Alumno, PerfilProfesor

User = get_user_model()


def mes(delta: int) -> date:
    """Primer día del mes actual + delta meses."""
    hoy = timezone.localdate()
    m = hoy.month - 1 + delta
    return date(hoy.year + m // 12, m % 12 + 1, 1)


def crear_alumno(nombre, dias=3, activo=True):
    u = User.objects.create_user(nombre, password="x", first_name=nombre.title(), last_name="Test")
    return Alumno.objects.create(usuario=u, dias_por_semana=dias, activo=activo)


def crear_profe(nombre, nivel):
    u = User.objects.create_user(nombre, password="x", is_staff=True)
    PerfilProfesor.objects.create(usuario=u, nivel=nivel)
    return u


class Base(TestCase):
    def setUp(self):
        # Las tarifas iniciales vienen de la migración (2→50 … 5→80, desde 01/2026)
        self.jefe = crear_profe("jefe", 8)
        self.hoy = timezone.localdate()


class CalculoTests(TestCase):
    def d(self, tipo, valor):
        return Descuento(tipo=tipo, valor=D(valor), motivo="x")

    def test_porcentaje_y_fijo(self):
        c = services.calcular_monto(D("60"), [self.d("PORC", "10"), self.d("FIJO", "5")])
        self.assertEqual((c.descuento, c.final), (D("11.00"), D("49.00")))

    def test_tope_cien_por_ciento_y_nunca_negativo(self):
        c = services.calcular_monto(D("60"), [self.d("PORC", "80"), self.d("PORC", "50")])
        self.assertEqual(c.final, D("0"))
        c = services.calcular_monto(D("60"), [self.d("FIJO", "100")])
        self.assertEqual(c.final, D("0"))


class GeneracionTests(Base):
    def test_genera_segun_plan_y_es_idempotente(self):
        a2, a5 = crear_alumno("dos", 2), crear_alumno("cinco", 5)
        crear_alumno("inactivo", 3, activo=False)
        res = services.generar_cuotas(self.hoy)
        self.assertEqual(res.creadas, 2)
        self.assertEqual(Cuota.objects.get(alumno=a2).monto_final, D("50"))
        self.assertEqual(Cuota.objects.get(alumno=a5).monto_final, D("80"))
        res = services.generar_cuotas(self.hoy)
        self.assertEqual((res.creadas, res.ya_existian), (0, 2))

    def test_sin_tarifa_se_omite_con_aviso(self):
        crear_alumno("uno", 1)
        res = services.generar_cuotas(self.hoy)
        self.assertEqual(res.creadas, 0)
        self.assertIn("1 día", res.omitidos[0])

    def test_vencimiento_y_descuento(self):
        a = crear_alumno("ana", 3)
        Descuento.objects.create(alumno=a, tipo="PORC", valor=10, motivo="Hermanos",
                                 vigente_desde=mes(-1))
        services.generar_cuotas(self.hoy)
        c = Cuota.objects.get(alumno=a)
        self.assertEqual(c.fecha_vencimiento, primer_dia(self.hoy).replace(day=10))
        self.assertEqual((c.descuento_total, c.monto_final), (D("6.00"), D("54.00")))
        self.assertIn("Hermanos (10%)", c.detalle_descuentos)

    def test_becado_nace_pagado(self):
        a = crear_alumno("beca", 3)
        Descuento.objects.create(alumno=a, tipo="PORC", valor=100, motivo="Beca",
                                 vigente_desde=mes(-1))
        services.generar_cuotas(self.hoy)
        self.assertEqual(Cuota.objects.get(alumno=a).estado, Cuota.Estado.PAGADA)

    def test_usa_tarifa_vigente_del_mes(self):
        a = crear_alumno("ana", 3)
        Tarifa.objects.create(dias_por_semana=3, monto=D("75"), vigente_desde=mes(1))
        services.generar_cuotas(self.hoy)
        services.generar_cuotas(mes(1))
        montos = list(Cuota.objects.filter(alumno=a).order_by("periodo").values_list("monto_final", flat=True))
        self.assertEqual(montos, [D("60"), D("75")])

    def test_alta_genera_cuota_completa(self):
        with self.captureOnCommitCallbacks(execute=True):
            a = crear_alumno("nuevo", 4)
        c = Cuota.objects.get(alumno=a)
        self.assertEqual((c.periodo, c.monto_final), (primer_dia(self.hoy), D("70")))


class PagoTests(Base):
    def setUp(self):
        super().setUp()
        self.alumno = crear_alumno("ana", 3)                  # $60
        services.generar_cuotas(self.hoy)
        self.cuota = Cuota.objects.get(alumno=self.alumno)

    def pagar(self, monto, cuota=None, fecha=None):
        return services.registrar_pago(self.jefe, (cuota or self.cuota).pk, D(monto),
                                       Pago.Metodo.EFECTIVO, fecha=fecha)

    def test_parcial_y_luego_pagada(self):
        self.pagar("20")
        self.cuota.refresh_from_db()
        self.assertEqual((self.cuota.estado, self.cuota.monto_pagado), ("PARCIAL", D("20")))
        self.pagar("40")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, "PAGADA")
        with self.assertRaisesMessage(services.FinanzasError, "ya está paga"):
            self.pagar("1")

    def test_no_permite_pagar_de_mas_ni_fecha_futura(self):
        with self.assertRaisesMessage(services.FinanzasError, "supera el saldo"):
            self.pagar("60.01")
        with self.assertRaisesMessage(services.FinanzasError, "futura"):
            self.pagar("10", fecha=self.hoy + timedelta(days=1))

    def test_recargo_solo_despues_del_vencimiento_y_una_vez(self):
        ParametrosFinanzas.objects.update(recargo_porcentaje=D("10"))
        services.generar_cuotas(mes(-2))
        vieja = Cuota.objects.get(alumno=self.alumno, periodo=mes(-2))
        # Pago con fecha anterior al vencimiento (cargado tarde): sin recargo
        self.pagar("10", cuota=vieja, fecha=vieja.fecha_vencimiento)
        vieja.refresh_from_db()
        self.assertIsNone(vieja.recargo_porcentaje)
        # Pago fuera de término: 10 % sobre el saldo de $50
        self.pagar("20", cuota=vieja, fecha=vieja.fecha_vencimiento + timedelta(days=3))
        vieja.refresh_from_db()
        self.assertEqual((vieja.recargo, vieja.saldo_pendiente), (D("5.00"), D("35.00")))
        # Un segundo pago tardío no vuelve a recargar
        self.pagar("35", cuota=vieja, fecha=self.hoy)
        vieja.refresh_from_db()
        self.assertEqual((vieja.recargo, vieja.estado), (D("5.00"), "PAGADA"))

    def test_anular_pago_revierte_estado_y_recargo(self):
        ParametrosFinanzas.objects.update(recargo_porcentaje=D("10"))
        services.generar_cuotas(mes(-2))
        vieja = Cuota.objects.get(alumno=self.alumno, periodo=mes(-2))
        pago = self.pagar("66", cuota=vieja, fecha=self.hoy)
        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "PAGADA")
        with self.assertRaisesMessage(services.FinanzasError, "motivo"):
            services.anular_pago(self.jefe, pago.pk, " ")
        services.anular_pago(self.jefe, pago.pk, "Error de carga")
        vieja.refresh_from_db()
        self.assertEqual((vieja.estado, vieja.recargo, vieja.monto_pagado),
                         ("PENDIENTE", D("0"), D("0")))

    def test_anular_cuota_solo_sin_pagos(self):
        self.pagar("10")
        with self.assertRaisesMessage(services.FinanzasError, "tiene pagos"):
            services.anular_cuota(self.jefe, self.cuota.pk, "baja")

    def test_monto_pagado_coincide_con_pagos(self):
        p = self.pagar("15")
        self.pagar("5")
        services.anular_pago(self.jefe, p.pk, "x")
        self.cuota.refresh_from_db()
        real = self.cuota.pagos.filter(anulado=False).aggregate(t=Sum("monto"))["t"]
        self.assertEqual(self.cuota.monto_pagado, real)


class PermisosTests(Base):
    def test_servicios_exigen_nivel(self):
        bajo = crear_profe("bajo", 5)
        a = crear_alumno("ana")
        services.generar_cuotas(self.hoy)
        with self.assertRaises(PermissionDenied):
            services.registrar_pago(bajo, Cuota.objects.get(alumno=a).pk, D("1"), "EFECTIVO")

    def test_vistas_y_admin(self):
        url = reverse("finanzas:dashboard")
        casos = [(crear_profe("cinco", 5), 403), (crear_profe("seis", 6), 200),
                 (User.objects.create_superuser("root", password="x"), 200),
                 (crear_alumno("alu").usuario, 403)]
        for usuario, esperado in casos:
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, esperado, usuario)

        self.client.force_login(User.objects.get(username="cinco"))
        r = self.client.get(reverse("admin:index"))
        self.assertNotContains(r, "/admin/finanzas/")
        r = self.client.get(reverse("turnos:inicio"), follow=True)
        self.assertNotContains(r, ">Finanzas<")

        self.client.force_login(User.objects.get(username="seis"))
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, "/admin/finanzas/")
        r = self.client.get(reverse("turnos:inicio"), follow=True)
        self.assertContains(r, ">Finanzas<")


class DashboardTests(Base):
    def setUp(self):
        super().setUp()
        self.ana, self.beto, self.caro = (crear_alumno("ana", 3), crear_alumno("beto", 2),
                                          crear_alumno("caro", 5))
        services.generar_cuotas(mes(-1))   # vencidas
        services.generar_cuotas(self.hoy)

    def cuota(self, alumno, periodo):
        return Cuota.objects.get(alumno=alumno, periodo=primer_dia(periodo))

    def test_ingresos_por_fecha_de_pago_y_sin_anulados(self):
        services.registrar_pago(self.jefe, self.cuota(self.ana, mes(-1)).pk, D("60"), "EFECTIVO")
        p = services.registrar_pago(self.jefe, self.cuota(self.beto, self.hoy).pk, D("50"), "TRANSF")
        services.registrar_pago(self.jefe, self.cuota(self.caro, self.hoy).pk, D("30"), "TRANSF")
        services.anular_pago(self.jefe, p.pk, "error")
        ing = consultas.ingresos(self.hoy)
        self.assertEqual((ing["total"], ing["cantidad"]), (D("90"), 2))   # deuda vieja incluida
        self.assertEqual({m["metodo"]: m["total"] for m in ing["por_metodo"]},
                         {"EFECTIVO": D("60"), "TRANSF": D("30")})
        fac = consultas.facturacion(self.hoy)
        self.assertEqual((fac["emitido"], fac["cobrado"]), (D("190"), D("30")))

    def test_estado_y_deudores(self):
        # ana paga lo vencido; beto debe todo; caro paga parte de lo vencido
        services.registrar_pago(self.jefe, self.cuota(self.ana, mes(-1)).pk, D("60"), "EFECTIVO")
        services.registrar_pago(self.jefe, self.cuota(self.caro, mes(-1)).pk, D("30"), "EFECTIVO")
        estado = consultas.estado_alumnos(self.hoy)
        # ¿La cuota de este mes ya venció? (depende del día en que corre el test)
        mes_vencido = self.hoy > primer_dia(self.hoy).replace(day=10)
        self.assertEqual(estado["morosos"], 3 if mes_vencido else 2)
        self.assertEqual(estado["por_vencer"], 0 if mes_vencido else 1)
        self.assertEqual(estado["al_dia"], 0)
        self.assertEqual(estado["sin_cuota"], 0)

        deudores = {d["alumno_id"]: d for d in consultas.deudores()}
        self.assertEqual(self.ana.pk in deudores, mes_vencido)
        # beto: $50 del mes pasado (+ $50 de este mes si ya venció)
        self.assertEqual(deudores[self.beto.pk]["deuda_vencida"], D("100") if mes_vencido else D("50"))
        self.assertEqual(deudores[self.beto.pk]["deuda_total"], D("100"))
        # caro: debía $80, pagó $30
        self.assertEqual(deudores[self.caro.pk]["deuda_total"], D("130"))
        self.assertEqual(deudores[self.beto.pk]["cuotas_impagas"], 2)

    def test_recargo_estimado_en_deudores(self):
        ParametrosFinanzas.objects.update(recargo_porcentaje=D("10"))
        d = {x["alumno_id"]: x for x in consultas.deudores()}[self.beto.pk]
        self.assertGreaterEqual(d["recargo_estimado"], D("5"))
        self.assertEqual(d["a_cobrar"], d["deuda_total"] + d["recargo_estimado"])

    def test_pantallas_y_csv(self):
        self.client.force_login(self.jefe)
        for url in [reverse("finanzas:dashboard"), reverse("finanzas:dashboard") + "?periodo=2026-01",
                    reverse("finanzas:buscar") + "?q=ana", reverse("finanzas:alumno", args=[self.ana.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.get(reverse("finanzas:deudores_csv"))
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Test, Beto", r.content.decode("utf-8-sig"))

    def test_cobro_desde_la_vista(self):
        self.client.force_login(self.jefe)
        c = self.cuota(self.beto, self.hoy)
        r = self.client.post(reverse("finanzas:cobrar", args=[c.pk]),
                             {"monto": "50", "metodo": "EFECTIVO", "fecha": self.hoy.isoformat()},
                             follow=True)
        self.assertContains(r, "La cuota quedó pagada")
        c.refresh_from_db()
        self.assertEqual(c.estado, "PAGADA")
