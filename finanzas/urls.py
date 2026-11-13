from django.urls import path

from . import views

app_name = "finanzas"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("generar/", views.generar, name="generar"),
    path("deudores.csv", views.deudores_csv, name="deudores_csv"),
    path("cobrar/", views.buscar, name="buscar"),
    path("alumno/<int:alumno_id>/", views.ficha_alumno, name="alumno"),
    path("alumno/<int:alumno_id>/generar/", views.generar_cuota_alumno, name="generar_alumno"),
    path("cuota/<int:cuota_id>/cobrar/", views.cobrar, name="cobrar"),
    path("cuota/<int:cuota_id>/anular/", views.anular_cuota, name="anular_cuota"),
    path("pago/<int:pago_id>/anular/", views.anular_pago, name="anular_pago"),
]
