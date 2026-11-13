from calendar import monthrange
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from turnos.models import Alumno

CERO = Decimal("0.00")
DINERO = DecimalField(max_digits=12, decimal_places=2)


def Dinero(verbose_name=None, **kw):
    """Campo de dinero uniforme: hasta 99.999.999,99."""
    return models.DecimalField(verbose_name, max_digits=10, decimal_places=2, **kw)


def primer_dia(fecha):
    return fecha.replace(day=1)


def ultimo_dia(fecha):
    return fecha.replace(day=monthrange(fecha.year, fecha.month)[1])


# ──────────────────────────────────────────────────────────────
# 1. Parámetros financieros (fila única)
# ──────────────────────────────────────────────────────────────
class ParametrosFinanzas(models.Model):
    dia_vencimiento = models.PositiveSmallIntegerField(
        "día de vencimiento", default=10, validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text="Día del mes en que vence la cuota.",
    )
    recargo_porcentaje = models.DecimalField(
        "recargo por pago fuera de término (%)", max_digits=5, decimal_places=2, default=CERO,
        validators=[MinValueValidator(CERO), MaxValueValidator(Decimal(100))],
        help_text="Se aplica una sola vez sobre el saldo, si el pago es posterior al vencimiento. "
                  "0 = sin recargo.",
    )
    nivel_minimo = models.PositiveSmallIntegerField(
        "nivel mínimo de profesor", default=6,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Nivel desde el cual un profesor gestiona cuotas y pagos.",
    )

    class Meta:
        verbose_name = verbose_name_plural = "Parámetros de finanzas"

    def __str__(self):
        return "Parámetros de finanzas"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ──────────────────────────────────────────────────────────────
# 2. Tarifa por días por semana, con vigencia
# ──────────────────────────────────────────────────────────────
class TarifaQuerySet(models.QuerySet):
    def vigente(self, dias_por_semana, periodo):
        return (self.filter(dias_por_semana=dias_por_semana, vigente_desde__lte=primer_dia(periodo))
                .order_by("-vigente_desde").first())


class Tarifa(models.Model):
    dias_por_semana = models.PositiveSmallIntegerField(
        "días por semana", validators=[MinValueValidator(1), MaxValueValidator(5)])
    monto = Dinero(validators=[MinValueValidator(CERO)])
    vigente_desde = models.DateField(help_text="Se toma el primer día del mes indicado.")

    objects = TarifaQuerySet.as_manager()

    class Meta:
        ordering = ["dias_por_semana", "-vigente_desde"]
        constraints = [
            models.UniqueConstraint(fields=["dias_por_semana", "vigente_desde"],
                                    name="tarifa_unica_por_vigencia",
                                    violation_error_message="Ya hay una tarifa para ese plan y mes."),
        ]

    def __str__(self):
        return f"{self.dias_por_semana} día(s)/sem · ${self.monto} desde {self.vigente_desde:%m/%Y}"

    def save(self, *args, **kwargs):
        self.vigente_desde = primer_dia(self.vigente_desde)
        super().save(*args, **kwargs)


# ──────────────────────────────────────────────────────────────
# 3. Descuentos personalizados
# ──────────────────────────────────────────────────────────────
class DescuentoQuerySet(models.QuerySet):
    def vigentes_en(self, periodo):
        """Descuentos activos en algún momento del mes."""
        return self.filter(vigente_desde__lte=ultimo_dia(periodo)).filter(
            Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=primer_dia(periodo)))


class Descuento(models.Model):
    class Tipo(models.TextChoices):
        PORCENTAJE = "PORC", "Porcentaje"
        MONTO_FIJO = "FIJO", "Monto fijo"

    alumno = models.ForeignKey(Alumno, on_delete=models.CASCADE, related_name="descuentos")
    tipo = models.CharField(max_length=4, choices=Tipo.choices, default=Tipo.PORCENTAJE)
    valor = Dinero(validators=[MinValueValidator(CERO)],
                   help_text="Porcentaje (ej. 15) o monto en pesos.")
    motivo = models.CharField(max_length=100, help_text="Ej.: hermanos, jubilado, promo.")
    vigente_desde = models.DateField(default=timezone.localdate)
    vigente_hasta = models.DateField(null=True, blank=True, help_text="Vacío = sin vencimiento.")

    objects = DescuentoQuerySet.as_manager()

    class Meta:
        ordering = ["alumno", "-vigente_desde"]

    def __str__(self):
        valor = (f"{self.valor.normalize():f}%" if self.tipo == self.Tipo.PORCENTAJE
                 else f"${self.valor}")
        return f"{self.motivo} ({valor})"

    def clean(self):
        if self.tipo == self.Tipo.PORCENTAJE and self.valor is not None and self.valor > 100:
            raise ValidationError({"valor": "Un porcentaje no puede superar 100."})
        if self.vigente_hasta and self.vigente_desde and self.vigente_hasta < self.vigente_desde:
            raise ValidationError("La vigencia termina antes de empezar.")


# ──────────────────────────────────────────────────────────────
# 4. Cuota mensual
# ──────────────────────────────────────────────────────────────
class CuotaQuerySet(models.QuerySet):
    def del_periodo(self, periodo):
        return self.filter(periodo=primer_dia(periodo))

    def no_anuladas(self):
        return self.exclude(estado=Cuota.Estado.ANULADA)

    def impagas(self):
        return self.filter(estado__in=Cuota.IMPAGAS)

    def vencidas(self, hoy=None):
        return self.impagas().filter(fecha_vencimiento__lt=hoy or timezone.localdate())


class Cuota(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        PARCIAL = "PARCIAL", "Pago parcial"
        PAGADA = "PAGADA", "Pagada"
        ANULADA = "ANULADA", "Anulada"

    IMPAGAS = [Estado.PENDIENTE, Estado.PARCIAL]

    alumno = models.ForeignKey(Alumno, on_delete=models.PROTECT, related_name="cuotas")
    periodo = models.DateField(help_text="Primer día del mes que se cobra.")

    # Foto del cálculo al generar la cuota
    dias_por_semana = models.PositiveSmallIntegerField("días por semana")
    monto_base = Dinero("monto base")
    descuento_total = Dinero("descuento", default=CERO)
    detalle_descuentos = models.CharField(max_length=255, blank=True)
    monto_final = Dinero("monto")

    recargo = Dinero(default=CERO)
    recargo_porcentaje = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="% aplicado. Vacío = todavía no se aplicó.")
    monto_pagado = Dinero("pagado", default=CERO, editable=False)

    fecha_vencimiento = models.DateField("vencimiento")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    observaciones = models.TextField(blank=True)
    creada = models.DateTimeField(auto_now_add=True)
    modificada = models.DateTimeField(auto_now=True)

    objects = CuotaQuerySet.as_manager()

    class Meta:
        ordering = ["-periodo", "alumno__usuario__last_name"]
        constraints = [
            models.UniqueConstraint(fields=["alumno", "periodo"], name="una_cuota_por_mes",
                                    violation_error_message="El alumno ya tiene cuota ese mes."),
            models.CheckConstraint(condition=Q(monto_final__gte=0), name="cuota_monto_no_negativo"),
        ]
        indexes = [models.Index(fields=["periodo", "estado"]),
                   models.Index(fields=["estado", "fecha_vencimiento"])]

    def __str__(self):
        return f"{self.alumno} · {self.periodo:%m/%Y}"

    def save(self, *args, **kwargs):
        self.periodo = primer_dia(self.periodo)
        super().save(*args, **kwargs)

    @property
    def total_a_pagar(self) -> Decimal:
        return self.monto_final + self.recargo

    @property
    def saldo_pendiente(self) -> Decimal:
        return self.total_a_pagar - self.monto_pagado

    @property
    def vencida(self) -> bool:
        return self.estado in self.IMPAGAS and self.fecha_vencimiento < timezone.localdate()

    @property
    def estado_visible(self) -> str:
        return "Vencida" if self.vencida else self.get_estado_display()

    @property
    def estado_css(self) -> str:
        """Reutiliza los colores de los turnos."""
        if self.vencida:
            return "lleno"
        return {"PAGADA": "libre", "PARCIAL": "medio", "PENDIENTE": "medio"}.get(self.estado, "")

    def recargo_estimado(self, porcentaje=None) -> Decimal:
        """Recargo que se cobraría hoy si todavía no se aplicó."""
        if self.recargo_porcentaje is not None or not self.vencida:
            return CERO
        if porcentaje is None:
            porcentaje = ParametrosFinanzas.load().recargo_porcentaje
        return (self.saldo_pendiente * porcentaje / 100).quantize(Decimal("0.01"))

    def recalcular_estado(self):
        """Actualiza monto_pagado y estado. Lo llaman los servicios con la cuota bloqueada."""
        self.monto_pagado = self.pagos.filter(anulado=False).aggregate(
            t=Coalesce(Sum("monto"), Value(CERO, output_field=DINERO)))["t"]
        if self.estado != self.Estado.ANULADA:
            if self.monto_pagado >= self.total_a_pagar:
                self.estado = self.Estado.PAGADA
            elif self.monto_pagado > CERO:
                self.estado = self.Estado.PARCIAL
            else:
                self.estado = self.Estado.PENDIENTE
        self.save(update_fields=["monto_pagado", "estado", "modificada"])


# ──────────────────────────────────────────────────────────────
# 5. Pago
# ──────────────────────────────────────────────────────────────
class Pago(models.Model):
    class Metodo(models.TextChoices):
        EFECTIVO = "EFECTIVO", "Efectivo"
        TRANSFERENCIA = "TRANSF", "Transferencia"
        DEBITO = "DEBITO", "Tarjeta de débito"
        CREDITO = "CREDITO", "Tarjeta de crédito"
        BILLETERA = "BILLETERA", "Billetera virtual"
        OTRO = "OTRO", "Otro"

    cuota = models.ForeignKey(Cuota, on_delete=models.PROTECT, related_name="pagos")
    fecha = models.DateField("fecha de pago", default=timezone.localdate)
    monto = Dinero(validators=[MinValueValidator(Decimal("0.01"))])
    metodo = models.CharField("método", max_length=10, choices=Metodo.choices,
                              default=Metodo.EFECTIVO)
    referencia = models.CharField(max_length=80, blank=True,
                                  help_text="Nº de operación o comprobante (opcional).")
    observaciones = models.TextField(blank=True)
    anulado = models.BooleanField(default=False)
    motivo_anulacion = models.CharField("motivo de anulación", max_length=200, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-creado"]
        indexes = [models.Index(fields=["fecha", "anulado"])]

    def __str__(self):
        return f"${self.monto} · {self.cuota} ({self.get_metodo_display()})"
