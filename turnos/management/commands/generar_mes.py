from django.core.management.base import BaseCommand
from django.utils import timezone

from turnos.services import generar_mes


class Command(BaseCommand):
    help = "Replica la semana tipo de cada alumno en el mes indicado (por defecto, el siguiente)."

    def add_arguments(self, parser):
        parser.add_argument("--anio", type=int)
        parser.add_argument("--mes", type=int)

    def handle(self, *args, anio=None, mes=None, **opts):
        hoy = timezone.localdate()
        if not (anio and mes):
            anio, mes = (hoy.year + 1, 1) if hoy.month == 12 else (hoy.year, hoy.month + 1)
        res = generar_mes(anio, mes)
        self.stdout.write(self.style.SUCCESS(
            f"{mes:02d}/{anio}: {len(res.inscripciones)} inscripciones creadas."))
        for adv in res.advertencias:
            self.stdout.write(self.style.WARNING(adv))
