from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone


def calcular_nivel(ocupados: int, cupo: int, umbral: int) -> str:
    """Única fuente de verdad de la regla de colores."""
    if ocupados > cupo:
        return "sobrecupo"
    if ocupados >= cupo:
        return "lleno"
    if ocupados * 100 >= umbral * cupo:
        return "medio"
    return "libre"


IMAGENES_BASE = {
    "logo": "sitio/default/logo.png",
    "imagen_encabezado": "sitio/default/encabezado.jpg",
    "imagen_pie": "sitio/default/pie.jpg",
}
MAX_IMAGEN_MB = 5


def validar_imagen(archivo):
    try:
        tamano = archivo.size
    except (OSError, ValueError):     # archivo base ausente: no bloquear el guardado
        return
    if tamano > MAX_IMAGEN_MB * 1024 * 1024:
        raise ValidationError(f"La imagen no puede superar {MAX_IMAGEN_MB} MB.")


# ──────────────────────────────────────────────────────────────
# 1. Parámetros globales (fila única)
# ──────────────────────────────────────────────────────────────
class ParametrosGimnasio(models.Model):
    cupo_maximo = models.PositiveSmallIntegerField(
        "cupo máximo", default=7, validators=[MinValueValidator(1)],
        help_text="Alumnos máximos por clase.",
    )
    hora_apertura = models.PositiveSmallIntegerField(
        "hora de apertura", default=7, validators=[MaxValueValidator(23)],
    )
    hora_cierre = models.PositiveSmallIntegerField(
        "hora de cierre", default=22, validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text="La última clase empieza una hora antes.",
    )
    umbral_amarillo = models.PositiveSmallIntegerField(
        "umbral amarillo (%)", default=70, validators=[MaxValueValidator(100)],
        help_text="% de ocupación a partir del cual la clase se pinta amarilla.",
    )
    dias_anticipacion_cambio = models.PositiveSmallIntegerField(
        "días de anticipación para cambios", default=1,
        help_text="Días mínimos de anticipación para que un alumno cambie un turno.",
    )

    # ── Apariencia del sitio ──
    nombre_gimnasio = models.CharField("nombre del gimnasio", max_length=80, default="Gimnasio")
    logo = models.ImageField(
        upload_to="sitio/", default=IMAGENES_BASE["logo"], validators=[validar_imagen],
        help_text="Cuadrada, idealmente PNG con fondo transparente (mín. 256×256).",
    )
    imagen_encabezado = models.ImageField(
        "imagen del encabezado", upload_to="sitio/", default=IMAGENES_BASE["imagen_encabezado"],
        validators=[validar_imagen],
        help_text="Apaisada, recomendado 1920×360. Se recorta para ocupar todo el ancho.",
    )
    imagen_pie = models.ImageField(
        "imagen del pie de página", upload_to="sitio/", default=IMAGENES_BASE["imagen_pie"],
        validators=[validar_imagen], help_text="Apaisada, recomendado 1920×220.",
    )
    texto_pie = models.CharField(
        "texto del pie de página", max_length=200, blank=True,
        default="Lunes a viernes de 07:00 a 22:00",
    )

    class Meta:
        verbose_name = verbose_name_plural = "Parámetros del gimnasio"

    def __str__(self):
        return "Parámetros del gimnasio"

    def save(self, *args, **kwargs):
        self.pk = 1
        anterior = ParametrosGimnasio.objects.filter(pk=1).first()
        super().save(*args, **kwargs)
        if anterior:
            # Borra del disco la imagen reemplazada (nunca las originales)
            for campo in IMAGENES_BASE:
                vieja, nueva = getattr(anterior, campo), getattr(self, campo)
                if vieja.name and vieja.name != nueva.name and not vieja.name.startswith("sitio/default/"):
                    vieja.storage.delete(vieja.name)

    def restaurar_imagenes(self):
        for campo, ruta in IMAGENES_BASE.items():
            setattr(self, campo, ruta)
        self.save()

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def horas_disponibles(self):
        return list(range(self.hora_apertura, self.hora_cierre))

    def clean(self):
        if self.hora_apertura >= self.hora_cierre:
            raise ValidationError("La apertura debe ser anterior al cierre.")


class DiaSemana(models.IntegerChoices):
    LUNES = 0, "Lunes"
    MARTES = 1, "Martes"
    MIERCOLES = 2, "Miércoles"
    JUEVES = 3, "Jueves"
    VIERNES = 4, "Viernes"


# ──────────────────────────────────────────────────────────────
# 2. Alumno
# ──────────────────────────────────────────────────────────────
class Alumno(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alumno"
    )
    dias_por_semana = models.PositiveSmallIntegerField(
        "días por semana", validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    telefono = models.CharField("teléfono", max_length=30, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["usuario__last_name", "usuario__first_name"]

    def __str__(self):
        return self.usuario.get_full_name() or self.usuario.username

    def horarios_vigentes(self, en_fecha=None):
        en_fecha = en_fecha or timezone.localdate()
        return self.horarios.filter(
            Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=en_fecha),
            vigente_desde__lte=en_fecha,
        )


# ──────────────────────────────────────────────────────────────
# 3. Semana tipo (plantilla de recurrencia)
# ──────────────────────────────────────────────────────────────
class HorarioFijo(models.Model):
    alumno = models.ForeignKey(Alumno, on_delete=models.CASCADE, related_name="horarios")
    dia_semana = models.PositiveSmallIntegerField("día", choices=DiaSemana.choices)
    hora = models.PositiveSmallIntegerField(help_text="Hora de inicio (7 a 21).")
    vigente_desde = models.DateField(default=timezone.localdate)
    vigente_hasta = models.DateField(
        null=True, blank=True, help_text="Vacío = vigente indefinidamente."
    )

    class Meta:
        verbose_name = "horario fijo"
        verbose_name_plural = "semana tipo"
        ordering = ["dia_semana", "hora"]
        constraints = [
            models.UniqueConstraint(
                fields=["alumno", "dia_semana"],
                condition=Q(vigente_hasta__isnull=True),
                name="un_horario_abierto_por_dia",
                violation_error_message="El alumno ya tiene un horario fijo ese día.",
            ),
        ]

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.hora:02d}:00"

    def clean(self):
        if self.hora is not None and self.hora not in ParametrosGimnasio.load().horas_disponibles:
            raise ValidationError({"hora": "Fuera del horario del gimnasio."})
        if self.vigente_hasta and self.vigente_desde and self.vigente_hasta < self.vigente_desde:
            raise ValidationError("La vigencia termina antes de empezar.")


# ──────────────────────────────────────────────────────────────
# 4. Clase concreta
# ──────────────────────────────────────────────────────────────
class ClaseQuerySet(models.QuerySet):
    def con_ocupacion(self):
        return self.annotate(
            ocupados=Count("inscripciones", filter=Q(inscripciones__estado="ACTIVA"))
        )


class Clase(models.Model):
    fecha = models.DateField(db_index=True)
    hora = models.PositiveSmallIntegerField()
    cupo_especial = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text="Opcional: reemplaza el cupo global solo para esta clase.",
    )
    cancelada = models.BooleanField(default=False, help_text="Feriado, mantenimiento, etc.")

    objects = ClaseQuerySet.as_manager()

    class Meta:
        ordering = ["fecha", "hora"]
        constraints = [
            models.UniqueConstraint(fields=["fecha", "hora"], name="clase_unica_por_franja"),
        ]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y} {self.hora:02d}:00"

    def clean(self):
        if self.fecha and self.fecha.weekday() > 4:
            raise ValidationError("El gimnasio abre de lunes a viernes.")

    @property
    def hora_inicio(self) -> time:
        return time(self.hora)

    @property
    def cupo(self) -> int:
        return self.cupo_especial or ParametrosGimnasio.load().cupo_maximo

    @property
    def cantidad_inscriptos(self) -> int:
        if hasattr(self, "ocupados"):
            return self.ocupados
        return self.inscripciones.filter(estado=Inscripcion.Estado.ACTIVA).count()

    @property
    def lugares_libres(self) -> int:
        return self.cupo - self.cantidad_inscriptos

    @property
    def porcentaje_ocupacion(self) -> int:
        return round(self.cantidad_inscriptos * 100 / self.cupo) if self.cupo else 100

    @property
    def nivel_ocupacion(self) -> str:
        return calcular_nivel(
            self.cantidad_inscriptos, self.cupo, ParametrosGimnasio.load().umbral_amarillo
        )


# ──────────────────────────────────────────────────────────────
# 5. Inscripción + asistencia
# ──────────────────────────────────────────────────────────────
class Inscripcion(models.Model):
    class Origen(models.TextChoices):
        FIJO = "FIJO", "Semana tipo"
        CAMBIO_PUNTUAL = "PUNTUAL", "Cambio de un día"
        CAMBIO_PERMANENTE = "PERMANENTE", "Cambio permanente"
        ADMIN = "ADMIN", "Asignado por profesor"

    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        CANCELADA = "CANCELADA", "Cancelada"

    alumno = models.ForeignKey(Alumno, on_delete=models.CASCADE, related_name="inscripciones")
    clase = models.ForeignKey(Clase, on_delete=models.PROTECT, related_name="inscripciones")
    fecha = models.DateField(editable=False, db_index=True)
    horario_origen = models.ForeignKey(
        HorarioFijo, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="inscripciones",
    )
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.FIJO)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ACTIVA)
    sobrecupo = models.BooleanField(default=False)
    reemplaza_a = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reemplazada_por",
    )
    asistio = models.BooleanField("asistió", null=True, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "inscripción"
        verbose_name_plural = "inscripciones"
        ordering = ["clase__fecha", "clase__hora"]
        constraints = [
            models.UniqueConstraint(
                fields=["alumno", "fecha"],
                condition=Q(estado="ACTIVA"),
                name="una_clase_por_dia",
                violation_error_message="El alumno ya tiene una clase ese día.",
            ),
        ]
        indexes = [models.Index(fields=["clase", "estado"])]

    def __str__(self):
        return f"{self.alumno} → {self.clase} ({self.get_estado_display()})"

    def save(self, *args, **kwargs):
        self.fecha = self.clase.fecha
        super().save(*args, **kwargs)
