from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from finanzas.services import generar_cuotas


class Command(BaseCommand):
    help = "Genera las cuotas del mes (por defecto, el actual) para todos los alumnos activos."

    def add_arguments(self, parser):
        parser.add_argument("--periodo", help="Mes a generar, formato AAAA-MM.")

    def handle(self, *args, periodo=None, **opts):
        try:
            fecha = date.fromisoformat(f"{periodo}-01") if periodo else timezone.localdate()
        except ValueError:
            raise CommandError("El período debe tener formato AAAA-MM (ej. 2026-12).")
        res = generar_cuotas(fecha)
        self.stdout.write(self.style.SUCCESS(
            f"{fecha:%m/%Y}: {res.creadas} cuotas creadas, {res.ya_existian} ya existían."))
        for aviso in res.omitidos:
            self.stdout.write(self.style.WARNING(aviso))
