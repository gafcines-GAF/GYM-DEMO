"""
Datos de prueba de finanzas sobre los alumnos existentes: cuotas de los últimos
4 meses, pagos (en término, tardíos, parciales e impagos), descuentos y profesores
de distintos niveles. Lo invoca `cargar_demo`; también se puede correr solo.
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from finanzas import services
from finanzas.models import Cuota, Descuento, Pago, ParametrosFinanzas, primer_dia
from turnos.models import Alumno, PerfilProfesor

User = get_user_model()
PASSWORD_DEMO = "demo1234"   # solo para desarrollo local
METODOS = [Pago.Metodo.EFECTIVO] * 5 + [Pago.Metodo.TRANSFERENCIA] * 4 + [Pago.Metodo.BILLETERA] * 2 \
    + [Pago.Metodo.DEBITO]


def mes(delta: int) -> date:
    hoy = timezone.localdate()
    m = hoy.month - 1 + delta
    return date(hoy.year + m // 12, m % 12 + 1, 1)


class Command(BaseCommand):
    help = "Carga cuotas, pagos y descuentos de ejemplo (borra los datos financieros existentes)."

    def add_arguments(self, parser):
        parser.add_argument("--limpiar", action="store_true",
                            help="Solo borra los datos financieros y termina.")

    @transaction.atomic
    def handle(self, *args, limpiar=False, **opts):
        Pago.objects.all().delete()
        Cuota.objects.all().delete()
        Descuento.objects.all().delete()
        if limpiar:
            return

        rnd = random.Random(7)
        hoy = timezone.localdate()
        ParametrosFinanzas.objects.update_or_create(pk=1, defaults={"recargo_porcentaje": Decimal("10")})

        # Profesores de distintos niveles
        jefe = User.objects.get(username="profesor")
        PerfilProfesor.objects.update_or_create(usuario=jefe, defaults={"nivel": 10})
        for username, nombre, nivel in (("profe8", "Martín", 8), ("profe5", "Sol", 5)):
            u, _ = User.objects.update_or_create(
                username=username, defaults={"first_name": nombre, "last_name": "Profe",
                                             "is_staff": True, "is_superuser": False})
            u.set_password(PASSWORD_DEMO)
            u.save()
            PerfilProfesor.objects.update_or_create(usuario=u, defaults={"nivel": nivel})

        # Descuentos
        alumnos = list(Alumno.objects.filter(activo=True).order_by("pk"))
        desde = mes(-6)
        for a in alumnos[3:9]:
            Descuento.objects.create(alumno=a, tipo="PORC", valor=10, motivo="Hermanos", vigente_desde=desde)
        for a in alumnos[12:15]:
            Descuento.objects.create(alumno=a, tipo="FIJO", valor=5, motivo="Promo", vigente_desde=desde)
        Descuento.objects.create(alumno=alumnos[20], tipo="PORC", valor=100, motivo="Beca",
                                 vigente_desde=desde)

        # Cuotas de los últimos 4 meses (incluido el actual)
        for delta in (-3, -2, -1, 0):
            services.generar_cuotas(mes(delta))

        # Pagos
        pagos = 0
        for cuota in Cuota.objects.filter(estado=Cuota.Estado.PENDIENTE).order_by("periodo", "pk"):
            venc = cuota.fecha_vencimiento
            actual = cuota.periodo == primer_dia(hoy)
            r = rnd.random()
            if actual:
                limite = min(hoy, venc)
                if r < .58 and limite >= cuota.periodo:
                    fecha = cuota.periodo + timedelta(days=rnd.randint(0, (limite - cuota.periodo).days))
                    pagos += self._pagar(jefe, cuota, cuota.saldo_pendiente, fecha, rnd)
                continue
            if r < .78:                                   # en término
                fecha = cuota.periodo + timedelta(days=rnd.randint(0, venc.day - 1))
                pagos += self._pagar(jefe, cuota, cuota.saldo_pendiente, fecha, rnd)
            elif r < .88:                                 # tarde, con recargo
                fecha = min(hoy, venc + timedelta(days=rnd.randint(2, 15)))
                pagos += self._pagar(jefe, cuota, cuota.saldo_pendiente * Decimal("1.1"), fecha, rnd)
            elif r < .94:                                 # parcial
                fecha = cuota.periodo + timedelta(days=rnd.randint(0, venc.day - 1))
                pagos += self._pagar(jefe, cuota, (cuota.saldo_pendiente / 2).quantize(Decimal("1")),
                                     fecha, rnd)
            # resto: impagas (morosos)

        self.stdout.write(self.style.SUCCESS(
            f"Finanzas: {Cuota.objects.count()} cuotas, {pagos} pagos, recargo 10 %.\n"
            f"Profesores: profesor (nivel 10), profe8 (nivel 8), profe5 (nivel 5, sin acceso) "
            f"/ {PASSWORD_DEMO}"))

    def _pagar(self, usuario, cuota, monto, fecha, rnd):
        cuota.refresh_from_db()
        # Con recargo, el saldo crece: se limita al saldo real después de aplicarlo
        services._aplicar_recargo(cuota, fecha)
        monto = min(Decimal(monto).quantize(Decimal("0.01")), cuota.saldo_pendiente)
        services.registrar_pago(usuario, cuota.pk, monto, rnd.choice(METODOS), fecha=fecha)
        return 1
