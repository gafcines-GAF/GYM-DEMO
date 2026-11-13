from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from turnos.admin import AlumnoAdmin

from . import services
from .models import Cuota, Descuento, Pago, ParametrosFinanzas, Tarifa
from .permisos import SoloFinanzasAdminMixin, puede_gestionar_finanzas


@admin.register(ParametrosFinanzas)
class ParametrosFinanzasAdmin(SoloFinanzasAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request, *args):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = ParametrosFinanzas.load()
        return redirect(reverse("admin:finanzas_parametrosfinanzas_change", args=[obj.pk]))


@admin.register(Tarifa)
class TarifaAdmin(SoloFinanzasAdminMixin, admin.ModelAdmin):
    list_display = ["dias_por_semana", "monto", "vigente_desde"]
    list_filter = ["dias_por_semana"]


@admin.register(Descuento)
class DescuentoAdmin(SoloFinanzasAdminMixin, admin.ModelAdmin):
    list_display = ["alumno", "motivo", "tipo", "valor", "vigente_desde", "vigente_hasta"]
    list_filter = ["tipo"]
    search_fields = ["alumno__usuario__first_name", "alumno__usuario__last_name", "motivo"]
    autocomplete_fields = ["alumno"]

    def has_delete_permission(self, request, obj=None):     # un descuento sí se puede borrar
        return puede_gestionar_finanzas(request.user)


class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    can_delete = False
    fields = ["fecha", "monto", "metodo", "referencia", "anulado", "motivo_anulacion",
              "registrado_por"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):        # los pagos se cargan con el servicio
        return False


@admin.register(Cuota)
class CuotaAdmin(SoloFinanzasAdminMixin, admin.ModelAdmin):
    list_display = ["alumno", "periodo_txt", "monto_final", "recargo", "monto_pagado",
                    "estado_coloreado", "fecha_vencimiento"]
    list_filter = ["estado", "periodo"]
    search_fields = ["alumno__usuario__first_name", "alumno__usuario__last_name",
                     "alumno__usuario__username"]
    date_hierarchy = "periodo"
    inlines = [PagoInline]
    readonly_fields = ["alumno", "periodo", "dias_por_semana", "monto_base", "descuento_total",
                       "detalle_descuentos", "monto_final", "recargo", "recargo_porcentaje",
                       "monto_pagado", "estado", "creada", "modificada"]
    fields = readonly_fields[:-2] + ["fecha_vencimiento", "observaciones"]
    actions = ["recalcular"]

    def has_add_permission(self, request, *args):           # se crean con el generador
        return False

    @admin.display(description="Período", ordering="periodo")
    def periodo_txt(self, obj):
        return f"{obj.periodo:%m/%Y}"

    @admin.display(description="Estado", ordering="estado")
    def estado_coloreado(self, obj):
        colores = {"lleno": "#c62828", "medio": "#9a7400", "libre": "#2d6a4f"}
        return format_html('<b style="color:{}">{}</b>', colores.get(obj.estado_css, "#666"),
                           obj.estado_visible)

    @admin.action(description="Recalcular monto (solo cuotas sin pagos)")
    def recalcular(self, request, queryset):
        ok = 0
        for cuota in queryset:
            try:
                services.recalcular_cuota(request.user, cuota.pk)
                ok += 1
            except services.FinanzasError as e:
                self.message_user(request, f"{cuota}: {e}", messages.WARNING)
        self.message_user(request, f"{ok} cuota(s) recalculada(s).")


@admin.register(Pago)
class PagoAdmin(SoloFinanzasAdminMixin, admin.ModelAdmin):
    list_display = ["fecha", "cuota", "monto", "metodo", "anulado", "registrado_por"]
    list_filter = ["metodo", "anulado", "fecha"]
    date_hierarchy = "fecha"
    search_fields = ["cuota__alumno__usuario__first_name", "cuota__alumno__usuario__last_name",
                     "referencia"]
    readonly_fields = [f.name for f in Pago._meta.fields]

    def has_add_permission(self, request, *args):           # alta y anulación por la pantalla de cobro
        return False


# ── Descuentos dentro de la ficha del alumno (solo para quien gestiona finanzas) ──
class DescuentoInline(admin.TabularInline):
    model = Descuento
    extra = 0
    fields = ["motivo", "tipo", "valor", "vigente_desde", "vigente_hasta"]

    def _puede(self, request):
        return puede_gestionar_finanzas(request.user)

    def has_view_permission(self, request, obj=None):
        return self._puede(request)

    has_add_permission = has_change_permission = has_delete_permission = (
        lambda self, request, obj=None: self._puede(request))


AlumnoAdmin.inlines = [*AlumnoAdmin.inlines, DescuentoInline]
