from django import forms

from .models import Alumno


class CambioHorarioForm(forms.Form):
    fecha = forms.DateField()
    hora = forms.IntegerField(min_value=0, max_value=23)
    alcance = forms.ChoiceField(choices=[("PUNTUAL", "Solo este día"),
                                         ("PERMANENTE", "Por el resto del mes")])


class AsignarAlumnoForm(forms.Form):
    alumno = forms.ModelChoiceField(
        queryset=Alumno.objects.filter(activo=True).select_related("usuario"),
        empty_label="Elegí un alumno…",
    )
