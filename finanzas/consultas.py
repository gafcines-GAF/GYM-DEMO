"""Consultas del dashboard. Cada función hace 1-3 consultas, sin bucles por alumno."""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (Case, Count, Exists, ExpressionWrapper, F, Min, OuterRef, Q, Sum,
                              Value, When)
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from turnos.models import Alumno

from .models import CERO, DINERO, Cuota, Pago, ParametrosFinanzas, primer_dia, ultimo_dia

TOTAL = ExpressionWrapper(F("monto_final") + F("recargo"), output_field=DINERO)
SALDO = ExpressionWrapper(F("monto_final") + F("recargo") - F("monto_pagado"), output_field=DINERO)
IMPAGAS = Cuota.IMPAGAS


def _cero(expr):
    return Coalesce(expr, Value(CERO, output_field=DINERO))


def mes_anterior(periodo: date) -> date:
    return primer_dia(primer_dia(periodo) - timedelta(days=1))


def _variacion(actual: Decimal, anterior: Decimal):
    if not anterior:
        return None
    return round((actual - anterior) * 100 / anterior)


# ── 1. Ingresos (caja): pagos con fecha en el mes ─────────────
def ingresos(periodo: date) -> dict:
    inicio, fin = primer_dia(periodo), ultimo_dia(periodo)
    pagos = Pago.objects.filter(anulado=False, fecha__range=(inicio, fin))
    datos = pagos.aggregate(total=_cero(Sum("monto")), cantidad=Count("id"))

    etiquetas = dict(Pago.Metodo.choices)
    por_metodo = list(pagos.values("metodo").annotate(total=Sum("monto"), cantidad=Count("id"))
                      .order_by("-total"))
    maximo = max((m["total"] for m in por_metodo), default=0) or 1
    for m in por_metodo:
        m["nombre"] = etiquetas.get(m["metodo"], m["metodo"])
        m["pct"] = round(m["total"] * 100 / maximo)

    ant = mes_anterior(periodo)
    anterior = (Pago.objects.filter(anulado=False, fecha__range=(ant, ultimo_dia(ant)))
                .aggregate(t=_cero(Sum("monto")))["t"])
    return {**datos, "por_metodo": por_metodo, "mes_anterior": anterior,
            "variacion": _variacion(datos["total"], anterior)}


# ── 2. Facturación de las cuotas del mes ──────────────────────
def facturacion(periodo: date) -> dict:
    datos = (Cuota.objects.del_periodo(periodo).no_anuladas().aggregate(
        emitido=_cero(Sum(TOTAL)),
        cobrado=_cero(Sum("monto_pagado")),
        pendiente=_cero(Sum(SALDO)),
        cuotas=Count("id"),
        pagadas=Count("id", filter=Q(estado=Cuota.Estado.PAGADA)),
        parciales=Count("id", filter=Q(estado=Cuota.Estado.PARCIAL)),
        pendientes=Count("id", filter=Q(estado=Cuota.Estado.PENDIENTE)),
    ))
    datos["tasa_cobranza"] = (round(datos["cobrado"] * 100 / datos["emitido"])
                              if datos["emitido"] else 0)
    return datos


# ── 3. Estado de los alumnos activos ──────────────────────────
def estado_alumnos(periodo: date, hoy: date | None = None) -> dict:
    hoy = hoy or timezone.localdate()
    impaga = Cuota.objects.filter(alumno=OuterRef("pk"), estado__in=IMPAGAS)
    datos = (Alumno.objects.filter(activo=True).annotate(
        tiene_vencida=Exists(impaga.filter(fecha_vencimiento__lt=hoy)),
        tiene_por_vencer=Exists(impaga.filter(fecha_vencimiento__gte=hoy)),
        tiene_cuota_mes=Exists(Cuota.objects.filter(alumno=OuterRef("pk"),
                                                    periodo=primer_dia(periodo))),
    ).aggregate(
        total=Count("pk"),
        morosos=Count("pk", filter=Q(tiene_vencida=True)),
        por_vencer=Count("pk", filter=Q(tiene_vencida=False, tiene_por_vencer=True)),
        al_dia=Count("pk", filter=Q(tiene_vencida=False, tiene_por_vencer=False)),
        sin_cuota=Count("pk", filter=Q(tiene_cuota_mes=False)),
    ))
    total = datos["total"] or 1
    for clave in ("al_dia", "por_vencer", "morosos"):
        datos[f"{clave}_pct"] = round(datos[clave] * 100 / total, 1)
    return datos


# ── 4. Deudores con acumulado ─────────────────────────────────
def deudores(hoy: date | None = None):
    hoy = hoy or timezone.localdate()
    pct = ParametrosFinanzas.load().recargo_porcentaje
    recargo_estimado = Case(
        When(recargo_porcentaje__isnull=True, fecha_vencimiento__lt=hoy,
             then=ExpressionWrapper(SALDO * Value(pct) / Value(100), output_field=DINERO)),
        default=Value(CERO), output_field=DINERO,
    )
    return (Cuota.objects.filter(estado__in=IMPAGAS)
            .values("alumno_id", "alumno__usuario__first_name", "alumno__usuario__last_name",
                    "alumno__usuario__username", "alumno__telefono", "alumno__activo")
            .annotate(
                deuda_vencida=_cero(Sum(SALDO, filter=Q(fecha_vencimiento__lt=hoy))),
                deuda_total=_cero(Sum(SALDO)),
                recargo_estimado=_cero(Sum(recargo_estimado)),
                cuotas_impagas=Count("id"),
                vencida_desde=Min("fecha_vencimiento", filter=Q(fecha_vencimiento__lt=hoy)),
            )
            .filter(deuda_vencida__gt=0)
            .annotate(a_cobrar=ExpressionWrapper(F("deuda_total") + F("recargo_estimado"),
                                                 output_field=DINERO))
            .order_by("-a_cobrar", "alumno__usuario__last_name"))


def total_deuda_vencida(hoy: date | None = None) -> dict:
    hoy = hoy or timezone.localdate()
    return (Cuota.objects.filter(estado__in=IMPAGAS, fecha_vencimiento__lt=hoy)
            .aggregate(total=_cero(Sum(SALDO)), alumnos=Count("alumno", distinct=True)))


# ── 5. Evolución de ingresos ──────────────────────────────────
def evolucion_ingresos(periodo: date, meses: int = 6) -> list[dict]:
    lista = [primer_dia(periodo)]
    for _ in range(meses - 1):
        lista.insert(0, mes_anterior(lista[0]))
    filas = (Pago.objects.filter(anulado=False, fecha__range=(lista[0], ultimo_dia(lista[-1])))
             .annotate(mes=TruncMonth("fecha")).values("mes").annotate(total=Sum("monto")))
    por_mes = {(f["mes"].year, f["mes"].month): f["total"] for f in filas}
    datos = [{"mes": m, "total": por_mes.get((m.year, m.month), CERO)} for m in lista]
    maximo = max((d["total"] for d in datos), default=0) or 1
    for d in datos:
        d["pct"] = round(d["total"] * 100 / maximo)
    return datos
