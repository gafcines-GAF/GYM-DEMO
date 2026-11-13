"""Reglas de negocio de cuotas y pagos. Vistas, Admin, comandos y señales pasan por acá."""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from turnos.models import Alumno

from .formato import pesos
from .models import CERO, Cuota, Descuento, Pago, ParametrosFinanzas, Tarifa, primer_dia
from .permisos import exigir_permiso

CENTAVO = Decimal("0.01")


class FinanzasError(Exception):
    """Error de negocio que se muestra al usuario tal cual."""


def redondear(x: Decimal) -> Decimal:
    return Decimal(x).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ── Cálculo puro ──────────────────────────────────────────────
@dataclass
class Calculo:
    base: Decimal
    descuento: Decimal
    detalle: str
    final: Decimal


def calcular_monto(base: Decimal, descuentos) -> Calculo:
    """Primero los porcentajes (tope 100 %), después los montos fijos. Nunca baja de $0."""
    descuentos = list(descuentos)
    porc = min(sum((d.valor for d in descuentos if d.tipo == Descuento.Tipo.PORCENTAJE), CERO),
               Decimal(100))
    fijo = sum((d.valor for d in descuentos if d.tipo == Descuento.Tipo.MONTO_FIJO), CERO)
    descuento = min(base, redondear(base * porc / 100) + fijo)
    return Calculo(base=base, descuento=descuento,
                   detalle=", ".join(str(d) for d in descuentos)[:255],
                   final=base - descuento)


# ── Generación masiva ─────────────────────────────────────────
@dataclass
class ResultadoGeneracion:
    creadas: int = 0
    ya_existian: int = 0
    omitidos: list[str] = field(default_factory=list)


@transaction.atomic
def generar_cuotas(periodo: date, alumnos=None) -> ResultadoGeneracion:
    """
    Crea la cuota del mes para cada alumno activo que no la tenga. Idempotente:
    se puede correr varias veces; la restricción (alumno, período) impide duplicados.
    """
    periodo = primer_dia(periodo)
    params = ParametrosFinanzas.load()
    vencimiento = periodo.replace(day=params.dia_vencimiento)
    tarifas = {d: Tarifa.objects.vigente(d, periodo) for d in range(1, 6)}

    res = ResultadoGeneracion()
    existentes = set(Cuota.objects.filter(periodo=periodo).values_list("alumno_id", flat=True))
    res.ya_existian = len(existentes)

    qs = alumnos if alumnos is not None else Alumno.objects.all()
    qs = (qs.filter(activo=True).exclude(pk__in=existentes)
            .select_related("usuario")
            .prefetch_related(Prefetch("descuentos",
                                       queryset=Descuento.objects.vigentes_en(periodo),
                                       to_attr="descuentos_del_mes")))

    nuevas = []
    for alumno in qs:
        tarifa = tarifas.get(alumno.dias_por_semana)
        if tarifa is None:
            res.omitidos.append(
                f"{alumno}: no hay tarifa para {alumno.dias_por_semana} día(s) por semana.")
            continue
        c = calcular_monto(tarifa.monto, alumno.descuentos_del_mes)
        nuevas.append(Cuota(
            alumno=alumno, periodo=periodo, dias_por_semana=alumno.dias_por_semana,
            monto_base=c.base, descuento_total=c.descuento, detalle_descuentos=c.detalle,
            monto_final=c.final, fecha_vencimiento=vencimiento,
            estado=Cuota.Estado.PAGADA if c.final == CERO else Cuota.Estado.PENDIENTE,
        ))

    Cuota.objects.bulk_create(nuevas)
    res.creadas = len(nuevas)
    return res


@transaction.atomic
def recalcular_cuota(usuario, cuota_id) -> Cuota:
    """Si cambió el plan o un descuento y la cuota no tiene pagos, se recalcula."""
    exigir_permiso(usuario)
    cuota = Cuota.objects.select_for_update().select_related("alumno").get(pk=cuota_id)
    if cuota.estado == Cuota.Estado.ANULADA:
        raise FinanzasError("La cuota está anulada.")
    if cuota.pagos.filter(anulado=False).exists():
        raise FinanzasError("La cuota ya tiene pagos: anulalos antes de recalcular.")
    tarifa = Tarifa.objects.vigente(cuota.alumno.dias_por_semana, cuota.periodo)
    if tarifa is None:
        raise FinanzasError("No hay tarifa vigente para ese plan.")
    c = calcular_monto(tarifa.monto, cuota.alumno.descuentos.vigentes_en(cuota.periodo))
    cuota.dias_por_semana = cuota.alumno.dias_por_semana
    cuota.monto_base, cuota.descuento_total = c.base, c.descuento
    cuota.detalle_descuentos, cuota.monto_final = c.detalle, c.final
    cuota.save()
    cuota.recalcular_estado()
    return cuota


# ── Pagos ─────────────────────────────────────────────────────
def _aplicar_recargo(cuota: Cuota, fecha_pago: date):
    """Una sola vez por cuota y solo si el pago es posterior al vencimiento."""
    if cuota.recargo_porcentaje is not None or fecha_pago <= cuota.fecha_vencimiento:
        return
    pct = ParametrosFinanzas.load().recargo_porcentaje
    cuota.recargo_porcentaje = pct
    cuota.recargo = redondear(cuota.saldo_pendiente * pct / 100)
    cuota.save(update_fields=["recargo", "recargo_porcentaje", "modificada"])


@transaction.atomic
def registrar_pago(usuario, cuota_id, monto, metodo, fecha=None,
                   referencia="", observaciones="") -> Pago:
    exigir_permiso(usuario)
    hoy = timezone.localdate()
    fecha = fecha or hoy
    monto = redondear(monto)

    cuota = Cuota.objects.select_for_update().get(pk=cuota_id)
    if cuota.estado == Cuota.Estado.ANULADA:
        raise FinanzasError("La cuota está anulada.")
    if cuota.estado == Cuota.Estado.PAGADA:
        raise FinanzasError("La cuota ya está paga.")
    if fecha > hoy:
        raise FinanzasError("La fecha de pago no puede ser futura.")
    if monto <= CERO:
        raise FinanzasError("El monto debe ser mayor a cero.")
    if metodo not in Pago.Metodo.values:
        raise FinanzasError("Método de pago inválido.")

    _aplicar_recargo(cuota, fecha)
    saldo = cuota.saldo_pendiente
    if monto > saldo:
        raise FinanzasError(f"El monto supera el saldo pendiente ({pesos(saldo)}).")

    pago = Pago.objects.create(cuota=cuota, monto=monto, metodo=metodo, fecha=fecha,
                               referencia=referencia, observaciones=observaciones,
                               registrado_por=usuario)
    cuota.recalcular_estado()
    return pago


@transaction.atomic
def anular_pago(usuario, pago_id, motivo: str) -> Pago:
    exigir_permiso(usuario)
    if not (motivo or "").strip():
        raise FinanzasError("Indicá el motivo de la anulación.")
    pago = Pago.objects.select_for_update().get(pk=pago_id)
    if pago.anulado:
        raise FinanzasError("El pago ya estaba anulado.")
    pago.anulado, pago.motivo_anulacion = True, motivo.strip()[:200]
    pago.save(update_fields=["anulado", "motivo_anulacion"])

    cuota = Cuota.objects.select_for_update().get(pk=pago.cuota_id)
    # Sin pagos válidos, el recargo se revierte (fue un error de carga)
    if not cuota.pagos.filter(anulado=False).exists() and cuota.recargo_porcentaje is not None:
        cuota.recargo, cuota.recargo_porcentaje = CERO, None
        cuota.save(update_fields=["recargo", "recargo_porcentaje", "modificada"])
    cuota.recalcular_estado()
    return pago


@transaction.atomic
def anular_cuota(usuario, cuota_id, motivo: str) -> Cuota:
    """Para bajas o errores. Solo si no tiene pagos válidos."""
    exigir_permiso(usuario)
    if not (motivo or "").strip():
        raise FinanzasError("Indicá el motivo de la anulación.")
    cuota = Cuota.objects.select_for_update().get(pk=cuota_id)
    if cuota.pagos.filter(anulado=False).exists():
        raise FinanzasError("La cuota tiene pagos: anulalos primero.")
    cuota.estado = Cuota.Estado.ANULADA
    cuota.observaciones = f"{cuota.observaciones}\nAnulada: {motivo.strip()}".strip()
    cuota.save(update_fields=["estado", "observaciones", "modificada"])
    return cuota
