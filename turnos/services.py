"""Reglas de negocio. Las vistas, el Admin y los comandos pasan siempre por acá."""
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Clase, HorarioFijo, Inscripcion, ParametrosGimnasio

ACTIVA = Inscripcion.Estado.ACTIVA
CANCELADA = Inscripcion.Estado.CANCELADA


class ReglaNegocioError(Exception):
    """Error que se muestra al usuario tal cual."""


class CupoLlenoError(ReglaNegocioError):
    pass


@dataclass
class Resultado:
    inscripciones: list = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    def sumar(self, otro: "Resultado"):
        self.inscripciones += otro.inscripciones
        self.advertencias += otro.advertencias


# ── Helpers ───────────────────────────────────────────────────
def es_staff(usuario) -> bool:
    return bool(usuario and (usuario.is_staff or usuario.is_superuser))


def fechas_del_dia(dia_semana: int, desde: date, hasta: date):
    actual = desde + timedelta(days=(dia_semana - desde.weekday()) % 7)
    while actual <= hasta:
        yield actual
        actual += timedelta(days=7)


def fin_de_mes(fecha: date) -> date:
    return fecha.replace(day=monthrange(fecha.year, fecha.month)[1])


def fecha_limite_cambios() -> date:
    """Primera fecha que un alumno puede tocar."""
    dias = ParametrosGimnasio.load().dias_anticipacion_cambio
    return timezone.localdate() + timedelta(days=dias)


def validar_anticipacion(fecha: date, usuario):
    if es_staff(usuario):
        return
    if fecha < fecha_limite_cambios():
        dias = ParametrosGimnasio.load().dias_anticipacion_cambio
        raise ReglaNegocioError(
            f"Los cambios deben hacerse con al menos {dias} día(s) de anticipación."
        )


def validar_dueno(obj, usuario):
    if not es_staff(usuario) and obj.alumno.usuario_id != usuario.id:
        raise PermissionDenied


def obtener_clase(fecha: date, hora: int, bloquear: bool = False) -> Clase:
    if fecha.weekday() > 4:
        raise ReglaNegocioError("El gimnasio abre de lunes a viernes.")
    if hora not in ParametrosGimnasio.load().horas_disponibles:
        raise ReglaNegocioError("Horario fuera del rango de apertura.")
    clase, _ = Clase.objects.get_or_create(fecha=fecha, hora=hora)
    if bloquear:
        clase = Clase.objects.select_for_update().get(pk=clase.pk)
    if clase.cancelada:
        raise ReglaNegocioError(f"La clase del {clase} está cancelada.")
    return clase


# ── Núcleo: todas las altas pasan por aquí ────────────────────
def _inscribir(alumno, clase, usuario, origen, *, horario=None, reemplaza=None,
               permitir_sobrecupo=False) -> Resultado:
    """
    Debe llamarse dentro de transaction.atomic() con `clase` bloqueada
    (select_for_update): así dos pedidos simultáneos por el último lugar
    se procesan de a uno.
    """
    res = Resultado()

    if Inscripcion.objects.filter(alumno=alumno, fecha=clase.fecha, estado=ACTIVA).exists():
        raise ReglaNegocioError(f"{alumno} ya tiene una clase el {clase.fecha:%d/%m}.")

    ocupados = clase.inscripciones.filter(estado=ACTIVA).count()
    sobrecupo = ocupados >= clase.cupo
    if sobrecupo:
        if not (es_staff(usuario) or permitir_sobrecupo):
            raise CupoLlenoError(f"La clase del {clase} no tiene lugares disponibles.")
        res.advertencias.append(
            f"⚠ Clase {clase} en SOBRECUPO: {ocupados + 1}/{clase.cupo} alumnos."
        )

    res.inscripciones.append(Inscripcion.objects.create(
        alumno=alumno, clase=clase, origen=origen, horario_origen=horario,
        reemplaza_a=reemplaza, sobrecupo=sobrecupo, creado_por=usuario,
    ))
    return res


# ── Profesor: asignación manual ───────────────────────────────
@transaction.atomic
def asignar_por_profesor(profesor, alumno, fecha, hora) -> Resultado:
    if not es_staff(profesor):
        raise PermissionDenied
    clase = obtener_clase(fecha, hora, bloquear=True)
    return _inscribir(alumno, clase, profesor, Inscripcion.Origen.ADMIN)


@transaction.atomic
def dar_de_baja(usuario, inscripcion_id) -> Inscripcion:
    insc = Inscripcion.objects.select_for_update().select_related("alumno").get(
        pk=inscripcion_id, estado=ACTIVA)
    validar_dueno(insc, usuario)
    validar_anticipacion(insc.fecha, usuario)
    insc.estado = CANCELADA
    insc.save(update_fields=["estado", "modificado"])
    return insc


@transaction.atomic
def alternar_cancelacion_clase(profesor, fecha, hora) -> Clase:
    """Marca/desmarca una clase como cancelada (feriado, mantenimiento)."""
    if not es_staff(profesor):
        raise PermissionDenied
    clase, _ = Clase.objects.select_for_update().get_or_create(fecha=fecha, hora=hora)
    clase.cancelada = not clase.cancelada
    clase.save(update_fields=["cancelada"])
    return clase


# ── Alumno: cambio de un solo día ─────────────────────────────
@transaction.atomic
def cambiar_puntual(usuario, inscripcion_id, fecha_destino, hora_destino) -> Resultado:
    insc = (Inscripcion.objects.select_for_update()
            .select_related("alumno", "clase").get(pk=inscripcion_id, estado=ACTIVA))
    validar_dueno(insc, usuario)
    validar_anticipacion(insc.fecha, usuario)
    validar_anticipacion(fecha_destino, usuario)

    destino = obtener_clase(fecha_destino, hora_destino, bloquear=True)
    if destino.pk == insc.clase_id:
        raise ReglaNegocioError("Elegiste el mismo turno que ya tenés.")

    # Se libera primero el original (permite cambiar de hora el mismo día).
    # Si algo falla, la transacción se revierte y el original sigue activo.
    insc.estado = CANCELADA
    insc.save(update_fields=["estado", "modificado"])
    return _inscribir(insc.alumno, destino, usuario,
                      Inscripcion.Origen.CAMBIO_PUNTUAL, reemplaza=insc)


# ── Alumno: cambio permanente (resto del mes en adelante) ─────
@transaction.atomic
def cambiar_permanente(usuario, horario_id, nuevo_dia, nueva_hora, desde: date) -> Resultado:
    """
    Cierra la plantilla actual el día anterior a `desde` y abre una nueva.
    Todo o nada: si alguna fecha nueva no tiene cupo, no se aplica ningún cambio.
    La nueva plantilla queda vigente para los meses siguientes.
    """
    horario = (HorarioFijo.objects.select_for_update()
               .select_related("alumno").get(pk=horario_id))
    validar_dueno(horario, usuario)
    validar_anticipacion(desde, usuario)
    alumno = horario.alumno

    if horario.vigente_hasta and horario.vigente_hasta < desde:
        raise ReglaNegocioError("Ese horario fijo ya no está vigente.")
    if nuevo_dia == horario.dia_semana and nueva_hora == horario.hora:
        raise ReglaNegocioError("Elegiste el mismo horario que ya tenés.")
    if (nuevo_dia != horario.dia_semana and alumno.horarios_vigentes(desde)
            .filter(dia_semana=nuevo_dia).exclude(pk=horario.pk).exists()):
        raise ReglaNegocioError("Ya tenés un horario fijo ese día de la semana.")

    # 1. Cancelar las inscripciones futuras de la plantilla vieja
    futuras = Inscripcion.objects.filter(horario_origen=horario, fecha__gte=desde, estado=ACTIVA)
    ultima = futuras.aggregate(m=Max("fecha"))["m"]
    futuras.update(estado=CANCELADA, modificado=timezone.now())

    # 2. Cerrar la plantilla vieja (o borrarla si todavía no había empezado)
    if horario.vigente_desde >= desde:
        horario.delete()
    else:
        horario.vigente_hasta = desde - timedelta(days=1)
        horario.save(update_fields=["vigente_hasta"])

    nuevo = HorarioFijo(alumno=alumno, dia_semana=nuevo_dia, hora=nueva_hora, vigente_desde=desde)
    nuevo.full_clean()
    nuevo.save()

    # 3. Generar hasta fin de mes (o más, si el mes siguiente ya estaba generado)
    hasta = max(fin_de_mes(desde), ultima or desde)
    res = Resultado()
    for fecha in fechas_del_dia(nuevo_dia, desde, hasta):
        if Inscripcion.objects.filter(alumno=alumno, fecha=fecha, estado=ACTIVA).exists():
            res.advertencias.append(f"El {fecha:%d/%m} ya tenías otro turno, se mantuvo ese.")
            continue
        try:
            clase = obtener_clase(fecha, nueva_hora, bloquear=True)
        except ReglaNegocioError as e:          # clase cancelada (feriado)
            res.advertencias.append(str(e))
            continue
        res.sumar(_inscribir(alumno, clase, usuario,
                             Inscripcion.Origen.CAMBIO_PERMANENTE, horario=nuevo))
    return res


# ── Replicar la semana tipo en un mes ─────────────────────────
def generar_mes(anio: int, mes: int) -> Resultado:
    """
    Idempotente: si el alumno ya tiene una inscripción ese día (activa o
    cancelada) no se toca, así se respetan los cambios ya hechos.
    """
    inicio = date(anio, mes, 1)
    fin = fin_de_mes(inicio)
    res = Resultado()

    horarios = (HorarioFijo.objects
                .filter(alumno__activo=True, vigente_desde__lte=fin)
                .exclude(vigente_hasta__lt=inicio)
                .select_related("alumno__usuario"))

    for h in horarios:
        desde = max(inicio, h.vigente_desde)
        hasta = min(fin, h.vigente_hasta or fin)
        for fecha in fechas_del_dia(h.dia_semana, desde, hasta):
            if Inscripcion.objects.filter(alumno=h.alumno, fecha=fecha).exists():
                continue
            try:
                with transaction.atomic():
                    clase = obtener_clase(fecha, h.hora, bloquear=True)
                    res.sumar(_inscribir(h.alumno, clase, None, Inscripcion.Origen.FIJO,
                                         horario=h, permitir_sobrecupo=True))
            except ReglaNegocioError as e:
                res.advertencias.append(f"{h.alumno} {fecha:%d/%m}: {e}")
    return res
