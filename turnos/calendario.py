"""Armado de las grillas Día / Semana / Mes con 2 consultas por pantalla."""
from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from .models import Clase, Inscripcion, ParametrosGimnasio, calcular_nivel

ETIQUETAS = {"libre": "Disponible", "medio": "Pocos lugares",
             "lleno": "Completa", "sobrecupo": "Sobrecupo"}


@dataclass
class Franja:
    fecha: date
    hora: int
    cupo: int
    ocupados: int
    nivel: str
    clase_id: int | None = None
    cancelada: bool = False
    es_mia: bool = False
    editable: bool = False

    @property
    def pct(self) -> int:
        return min(round(self.ocupados * 100 / self.cupo), 100) if self.cupo else 100

    @property
    def etiqueta(self) -> str:
        return "Cancelada" if self.cancelada else ETIQUETAS[self.nivel]


def dias_habiles(desde: date, hasta: date) -> list[date]:
    return [desde + timedelta(d) for d in range((hasta - desde).days + 1)
            if (desde + timedelta(d)).weekday() < 5]


def proximo_habil(fecha: date, paso: int = 1) -> date:
    fecha += timedelta(days=paso)
    while fecha.weekday() > 4:
        fecha += timedelta(days=paso)
    return fecha


def hoy_habil() -> date:
    hoy = timezone.localdate()
    return hoy if hoy.weekday() < 5 else proximo_habil(hoy)


def lunes_de(fecha: date) -> date:
    return fecha - timedelta(days=fecha.weekday())


def construir_grilla(fechas: list[date], usuario) -> dict[date, list[Franja]]:
    if not fechas:
        return {}
    params = ParametrosGimnasio.load()
    rango = (fechas[0], fechas[-1])
    staff = usuario.is_staff or usuario.is_superuser

    clases = {(c.fecha, c.hora): c
              for c in Clase.objects.filter(fecha__range=rango).con_ocupacion()}

    mias = {}
    if hasattr(usuario, "alumno"):
        mias = dict(Inscripcion.objects
                    .filter(alumno=usuario.alumno, fecha__range=rango,
                            estado=Inscripcion.Estado.ACTIVA)
                    .values_list("fecha", "clase__hora"))

    dias = params.dias_anticipacion_cambio
    limite = timezone.localdate() + timedelta(days=dias)
    grilla = {}
    for f in fechas:
        fila = []
        for h in params.horas_disponibles:
            c = clases.get((f, h))
            cupo = c.cupo_especial if c and c.cupo_especial else params.cupo_maximo
            ocupados = c.ocupados if c else 0
            cancelada = bool(c and c.cancelada)
            nivel = calcular_nivel(ocupados, cupo, params.umbral_amarillo)
            if nivel == "sobrecupo" and not staff:
                nivel = "lleno"          # el sobrecupo es una alerta solo para profesores
            fila.append(Franja(
                fecha=f, hora=h, cupo=cupo, ocupados=ocupados, nivel=nivel,
                clase_id=c.pk if c else None, cancelada=cancelada,
                es_mia=mias.get(f) == h,
                editable=staff or (f >= limite and not cancelada),
            ))
        grilla[f] = fila
    return grilla
