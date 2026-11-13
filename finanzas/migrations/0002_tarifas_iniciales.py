from datetime import date
from decimal import Decimal

from django.db import migrations

VIGENTE_DESDE = date(2026, 1, 1)
TARIFAS = {2: "50", 3: "60", 4: "70", 5: "80"}   # el plan de 1 día se carga desde el Admin


def cargar(apps, schema_editor):
    Tarifa = apps.get_model("finanzas", "Tarifa")
    for dias, monto in TARIFAS.items():
        Tarifa.objects.get_or_create(dias_por_semana=dias, vigente_desde=VIGENTE_DESDE,
                                     defaults={"monto": Decimal(monto)})


def quitar(apps, schema_editor):
    apps.get_model("finanzas", "Tarifa").objects.filter(vigente_desde=VIGENTE_DESDE).delete()


class Migration(migrations.Migration):
    dependencies = [("finanzas", "0001_initial")]
    operations = [migrations.RunPython(cargar, quitar)]
