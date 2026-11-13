from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Pago


class PagoForm(forms.Form):
    monto = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "0.01"}))
    metodo = forms.ChoiceField(label="Método", choices=Pago.Metodo.choices)
    fecha = forms.DateField(label="Fecha de pago", initial=timezone.localdate,
                            widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    referencia = forms.CharField(required=False, max_length=80,
                                 widget=forms.TextInput(attrs={"placeholder": "Nº de operación"}))
    observaciones = forms.CharField(required=False,
                                    widget=forms.Textarea(attrs={"rows": 2}))


class MotivoForm(forms.Form):
    motivo = forms.CharField(max_length=200)
