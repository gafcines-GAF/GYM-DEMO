from django.urls import path, register_converter

from . import converters, views

register_converter(converters.FechaConverter, "fecha")
app_name = "turnos"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("dia/<fecha:fecha>/", views.vista_dia, name="dia"),
    path("semana/<fecha:fecha>/", views.vista_semana, name="semana"),
    path("mes/<int:anio>/<int:mes>/", views.vista_mes, name="mes"),
    path("mes/<int:anio>/<int:mes>/generar/", views.generar_mes_view, name="generar_mes"),
    path("clase/<fecha:fecha>/<int:hora>/", views.detalle_clase, name="clase"),
    path("clase/<fecha:fecha>/<int:hora>/asignar/", views.asignar_alumno, name="asignar"),
    path("clase/<fecha:fecha>/<int:hora>/cancelar/", views.cancelar_clase, name="cancelar_clase"),
    path("inscripcion/<int:inscripcion_id>/cambiar/", views.cambiar_horario, name="cambiar"),
    path("inscripcion/<int:inscripcion_id>/baja/", views.baja_inscripcion, name="baja"),
]
