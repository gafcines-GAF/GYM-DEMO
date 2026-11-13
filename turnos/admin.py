from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .models import Alumno, Clase, HorarioFijo, Inscripcion, ParametrosGimnasio

User = get_user_model()


# ── Parámetros (fila única) ───────────────────────────────────
class ParametrosForm(forms.ModelForm):
    restaurar_imagenes = forms.BooleanField(
        label="Restaurar imágenes originales", required=False,
        help_text="Vuelve al logo, encabezado y pie que trae el sistema.",
    )

    class Meta:
        model = ParametrosGimnasio
        fields = "__all__"


def _vista_previa(campo, alto):
    @admin.display(description="Vista previa")
    def mostrar(self, obj):
        imagen = getattr(obj, campo)
        if not imagen:
            return "—"
        return format_html('<img src="{}" style="max-height:{}px;max-width:100%;border-radius:6px;'
                           'border:1px solid #ccc">', imagen.url, alto)
    return mostrar


@admin.register(ParametrosGimnasio)
class ParametrosAdmin(admin.ModelAdmin):
    form = ParametrosForm
    list_display = ["__str__", "cupo_maximo", "hora_apertura", "hora_cierre",
                    "umbral_amarillo", "dias_anticipacion_cambio"]
    readonly_fields = ["previa_logo", "previa_encabezado", "previa_pie"]
    fieldsets = [
        ("Turnos", {"fields": ["cupo_maximo", "hora_apertura", "hora_cierre",
                               "umbral_amarillo", "dias_anticipacion_cambio"]}),
        ("Apariencia", {"fields": ["nombre_gimnasio",
                                   "logo", "previa_logo",
                                   "imagen_encabezado", "previa_encabezado",
                                   "imagen_pie", "previa_pie",
                                   "texto_pie", "restaurar_imagenes"]}),
    ]

    previa_logo = _vista_previa("logo", 96)
    previa_encabezado = _vista_previa("imagen_encabezado", 90)
    previa_pie = _vista_previa("imagen_pie", 60)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("restaurar_imagenes"):
            obj.restaurar_imagenes()

    def has_add_permission(self, request):
        return not ParametrosGimnasio.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = ParametrosGimnasio.load()
        return redirect(reverse("admin:turnos_parametrosgimnasio_change", args=[obj.pk]))


# ── Alumnos con su semana tipo ────────────────────────────────
class HorarioFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        abiertos, dias = 0, set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE") or form.cleaned_data.get("vigente_hasta"):
                continue
            abiertos += 1
            dia = form.cleaned_data.get("dia_semana")
            if dia in dias:
                raise forms.ValidationError("Hay dos horarios abiertos el mismo día.")
            dias.add(dia)
        contratados = self.instance.dias_por_semana or 0
        if abiertos > contratados:
            raise forms.ValidationError(
                f"El alumno tiene contratados {contratados} días por semana "
                f"y cargaste {abiertos} horarios."
            )


class HorarioInline(admin.TabularInline):
    model = HorarioFijo
    formset = HorarioFormSet
    extra = 0
    fields = ["dia_semana", "hora", "vigente_desde", "vigente_hasta"]


class AlumnoForm(forms.ModelForm):
    """Al crear un alumno también crea su usuario de acceso."""
    username = forms.CharField(label="Usuario", required=False)
    first_name = forms.CharField(label="Nombre", required=False)
    last_name = forms.CharField(label="Apellido", required=False)
    email = forms.EmailField(label="Email", required=False)
    password = forms.CharField(label="Contraseña", required=False, widget=forms.PasswordInput,
                               help_text="Solo al crear. Para cambiarla, editá el usuario.")

    class Meta:
        model = Alumno
        fields = ["dias_por_semana", "telefono", "activo"]

    def clean(self):
        data = super().clean()
        if not self.instance.pk:
            if not data.get("username") or not data.get("password"):
                raise forms.ValidationError("Usuario y contraseña son obligatorios.")
            if User.objects.filter(username=data["username"]).exists():
                self.add_error("username", "Ese usuario ya existe.")
        return data

    def preparar_usuario(self, alumno):
        """Devuelve el User (sin guardar) con los datos del formulario."""
        d = self.cleaned_data
        if alumno.pk:
            user = alumno.usuario
        else:
            user = User(username=d["username"])
            user.set_password(d["password"])
        for campo in ("first_name", "last_name", "email"):
            if d.get(campo) or not alumno.pk:
                setattr(user, campo, d.get(campo, ""))
        return user


@admin.register(Alumno)
class AlumnoAdmin(admin.ModelAdmin):
    form = AlumnoForm
    inlines = [HorarioInline]
    list_display = ["__str__", "usuario", "dias_por_semana", "semana_tipo", "telefono", "activo"]
    list_filter = ["activo", "dias_por_semana"]
    search_fields = ["usuario__username", "usuario__first_name", "usuario__last_name"]

    def get_fieldsets(self, request, obj=None):
        datos = ["first_name", "last_name", "email"]
        if obj is None:
            datos = ["username", "password"] + datos
        return [("Acceso", {"fields": datos}),
                ("Plan", {"fields": ["dias_por_semana", "telefono", "activo"]})]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj:
            for campo in ("first_name", "last_name", "email"):
                form.base_fields[campo].initial = getattr(obj.usuario, campo)
        return form

    def save_model(self, request, obj, form, change):
        # Se guarda acá (y no en form.save) para no crear usuarios huérfanos
        # si la semana tipo resulta inválida.
        user = form.preparar_usuario(obj)
        user.save()
        obj.usuario = user
        super().save_model(request, obj, form, change)

    @admin.display(description="Semana tipo")
    def semana_tipo(self, obj):
        return ", ".join(str(h) for h in obj.horarios_vigentes()) or "—"


# ── Clases ────────────────────────────────────────────────────
class InscripcionInline(admin.TabularInline):
    model = Inscripcion
    fk_name = "clase"
    extra = 0
    fields = ["alumno", "estado", "origen", "sobrecupo", "asistio"]
    readonly_fields = ["origen", "sobrecupo"]
    autocomplete_fields = ["alumno"]


@admin.register(Clase)
class ClaseAdmin(admin.ModelAdmin):
    list_display = ["__str__", "ocupacion", "cupo_especial", "cancelada"]
    list_filter = ["cancelada", "fecha", "hora"]
    date_hierarchy = "fecha"
    list_editable = ["cancelada"]
    inlines = [InscripcionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).con_ocupacion()

    @admin.display(description="Ocupación", ordering="ocupados")
    def ocupacion(self, obj):
        colores = {"libre": "#2d6a4f", "medio": "#9a7400", "lleno": "#c62828", "sobrecupo": "#c62828"}
        return format_html('<b style="color:{}">{}/{}</b>{}', colores[obj.nivel_ocupacion],
                           obj.ocupados, obj.cupo, " ⚠" if obj.nivel_ocupacion == "sobrecupo" else "")


@admin.register(Inscripcion)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = ["alumno", "clase", "estado", "origen", "sobrecupo", "asistio", "creado_por"]
    list_filter = ["estado", "origen", "sobrecupo", "fecha"]
    search_fields = ["alumno__usuario__first_name", "alumno__usuario__last_name",
                     "alumno__usuario__username"]
    date_hierarchy = "fecha"
    autocomplete_fields = ["alumno"]
    raw_id_fields = ["clase", "reemplaza_a", "horario_origen"]
    readonly_fields = ["creado_por", "creado", "modificado"]


admin.site.site_header = "Gimnasio · Administración"
admin.site.site_title = "Gimnasio"
admin.site.index_title = "Gestión de turnos"


# ── Usuarios con nivel de profesor ────────────────────────────
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin  # noqa: E402

from .models import PerfilProfesor  # noqa: E402


class PerfilProfesorInline(admin.StackedInline):
    model = PerfilProfesor
    can_delete = False
    extra = 0
    max_num = 1
    verbose_name_plural = "Nivel de profesor"


admin.site.unregister(User)


@admin.register(User)
class UsuarioAdmin(BaseUserAdmin):
    inlines = [PerfilProfesorInline]
    list_display = ["username", "first_name", "last_name", "is_staff", "nivel"]

    @admin.display(description="Nivel", ordering="perfil_profesor__nivel")
    def nivel(self, obj):
        perfil = getattr(obj, "perfil_profesor", None)
        return perfil.nivel if perfil else "—"
