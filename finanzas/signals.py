from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from turnos.models import Alumno


@receiver(post_save, sender=Alumno, dispatch_uid="finanzas_cuota_al_alta")
def cuota_al_dar_de_alta(sender, instance, created, raw=False, **kwargs):
    """Alta a mitad de mes: cuota completa del mes en curso."""
    if not created or raw or not instance.activo:
        return
    from .services import generar_cuotas

    pk = instance.pk
    transaction.on_commit(lambda: generar_cuotas(
        timezone.localdate(), alumnos=Alumno.objects.filter(pk=pk)))
