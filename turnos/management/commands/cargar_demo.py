"""Carga datos de prueba. Uso: python manage.py cargar_demo  (borra los turnos existentes)."""
import random
from collections import Counter
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from turnos import services
from turnos.models import Alumno, Clase, HorarioFijo, Inscripcion, ParametrosGimnasio

User = get_user_model()

PASSWORD_DEMO = "demo1234"   # solo para desarrollo local
NOMBRES = ["Lucía", "Mateo", "Sofía", "Benjamín", "Valentina", "Joaquín", "Martina", "Tomás",
           "Catalina", "Santiago", "Emma", "Thiago", "Julieta", "Bautista", "Camila", "Lautaro",
           "Mía", "Felipe", "Olivia", "Ignacio", "Renata", "Agustín", "Paula", "Facundo",
           "Florencia", "Nicolás", "Abril", "Franco", "Delfina", "Gonzalo", "Antonella", "Bruno"]
APELLIDOS = ["González", "Rodríguez", "Gómez", "Fernández", "López", "Díaz", "Martínez", "Pérez",
             "García", "Sánchez", "Romero", "Sosa", "Álvarez", "Torres", "Ruiz", "Ramírez"]
# Horas más pedidas (mañana temprano y después del trabajo)
PESOS_HORA = {7: 7, 8: 9, 9: 3, 10: 2, 11: 1, 12: 3, 13: 2, 14: 1, 15: 1, 16: 2,
              17: 5, 18: 12, 19: 12, 20: 7, 21: 3}


class Command(BaseCommand):
    help = "Crea un profesor, alumnos con semana tipo y genera los turnos del mes actual y el siguiente."

    def add_arguments(self, parser):
        parser.add_argument("--permitir-produccion", action="store_true",
                            help="Permite correrlo con DEBUG=False (BORRA todos los alumnos).")

    @transaction.atomic
    def handle(self, *args, permitir_produccion=False, **opts):
        if not settings.DEBUG and not permitir_produccion:
            raise CommandError(
                "cargar_demo BORRA todos los alumnos, turnos y cuotas, y crea usuarios con "
                "contraseña conocida. No se ejecuta con DEBUG=False (producción). "
                "Si de verdad querés hacerlo, agregá --permitir-produccion.")
        rnd = random.Random(42)
        params = ParametrosGimnasio.load()

        finanzas = apps.is_installed("finanzas")
        if finanzas:      # las cuotas protegen a los alumnos: se limpian primero
            call_command("cargar_demo_finanzas", limpiar=True, permitir_produccion=True)
        Inscripcion.objects.all().delete()
        Clase.objects.all().delete()
        Alumno.objects.all().delete()
        User.objects.filter(is_staff=False).delete()

        profe, _ = User.objects.update_or_create(
            username="profesor",
            defaults={"first_name": "Laura", "last_name": "Profe",
                      "is_staff": True, "is_superuser": True})
        profe.set_password(PASSWORD_DEMO)
        profe.save()

        inicio_mes = timezone.localdate().replace(day=1)
        ocupacion = Counter()                     # (día, hora) → horarios fijos
        horas = list(PESOS_HORA)
        pesos = list(PESOS_HORA.values())

        for i in range(60):
            nombre = NOMBRES[i % len(NOMBRES)]
            username = "alumno" if i == 0 else f"alumno{i}"
            u = User.objects.create_user(username, password=PASSWORD_DEMO, first_name=nombre,
                                         last_name=rnd.choice(APELLIDOS))
            dias_sem = rnd.choice([2, 3, 3, 3, 4, 5])
            alumno = Alumno.objects.create(usuario=u, dias_por_semana=dias_sem)
            hora_pref = rnd.choices(horas, pesos)[0]
            for dia in sorted(rnd.sample(range(5), dias_sem)):
                hora = hora_pref if rnd.random() < .75 else rnd.choices(horas, pesos)[0]
                # Respeta el cupo en la plantilla: si está llena, busca otra hora
                while ocupacion[(dia, hora)] >= params.cupo_maximo:
                    hora = rnd.choices(horas, pesos)[0]
                ocupacion[(dia, hora)] += 1
                HorarioFijo.objects.create(alumno=alumno, dia_semana=dia, hora=hora,
                                           vigente_desde=inicio_mes)

        total = 0
        sig = services.fin_de_mes(inicio_mes) + timedelta(days=1)
        for f in (inicio_mes, sig):
            total += len(services.generar_mes(f.year, f.month).inscripciones)

        # Un ejemplo de sobrecupo asignado por el profesor
        llena = (Clase.objects.con_ocupacion().filter(fecha__gte=timezone.localdate(),
                                                      ocupados__gte=params.cupo_maximo).first())
        if llena:
            extra = Alumno.objects.exclude(inscripciones__fecha=llena.fecha).first()
            if extra:
                services.asignar_por_profesor(profe, extra, llena.fecha, llena.hora)

        if finanzas:
            call_command("cargar_demo_finanzas", permitir_produccion=True, stdout=self.stdout._out)

        self.stdout.write(self.style.SUCCESS(
            f"Listo: {Alumno.objects.count()} alumnos, {total} inscripciones.\n"
            f"Profesor: profesor / {PASSWORD_DEMO}   Alumno: alumno / {PASSWORD_DEMO}"))
