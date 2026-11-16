from calendar import monthrange
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .calendario import construir_grilla, dias_habiles, hoy_habil, lunes_de, proximo_habil
from .forms import AsignarAlumnoForm, CambioHorarioForm
from .models import Clase, Inscripcion, ParametrosGimnasio

ACTIVA = Inscripcion.Estado.ACTIVA


# ── Helpers ───────────────────────────────────────────────────
def _volver(request, defecto):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()},
                                               require_https=request.is_secure()):
        return redirect(nxt)
    return redirect(defecto)


def _mostrar(request, res):
    for adv in res.advertencias:
        messages.warning(request, adv)


def _mis_turnos(usuario, desde, hasta):
    """Turnos del alumno que todavía puede mover (alimenta el modal)."""
    if not hasattr(usuario, "alumno"):
        return []
    desde = max(desde, services.fecha_limite_cambios())
    return (Inscripcion.objects
            .filter(alumno=usuario.alumno, estado=ACTIVA, fecha__range=(desde, hasta))
            .select_related("clase"))


def _validar_franja(fecha, hora):
    if fecha.weekday() > 4 or hora not in ParametrosGimnasio.load().horas_disponibles:
        raise Http404("Franja inexistente")


# ── Vistas de calendario ──────────────────────────────────────
@login_required
def inicio(request):
    return redirect("turnos:dia", fecha=hoy_habil())


@login_required
def vista_dia(request, fecha):
    if fecha.weekday() > 4:
        return redirect("turnos:dia", fecha=proximo_habil(fecha))
    grilla = construir_grilla([fecha], request.user)
    lunes = lunes_de(fecha)
    return render(request, "turnos/dia.html", {
        "vista": "dia", "fecha": fecha, "franjas": grilla[fecha],
        "anterior": proximo_habil(fecha, -1), "siguiente": proximo_habil(fecha),
        "mis_turnos": _mis_turnos(request.user, lunes, lunes + timedelta(days=4)),
    })


@login_required
def vista_semana(request, fecha):
    lunes = lunes_de(fecha)
    dias = dias_habiles(lunes, lunes + timedelta(days=4))
    grilla = construir_grilla(dias, request.user)
    horas = [f.hora for f in grilla[dias[0]]]
    filas = [(h, [grilla[d][i] for d in dias]) for i, h in enumerate(horas)]
    return render(request, "turnos/semana.html", {
        "vista": "semana", "fecha": fecha if fecha.weekday() < 5 else lunes,
        "dias": dias, "filas": filas,
        "anterior": lunes - timedelta(days=7), "siguiente": lunes + timedelta(days=7),
        "mis_turnos": _mis_turnos(request.user, dias[0], dias[-1]),
    })


@login_required
def vista_mes(request, anio, mes):
    if not (1 <= mes <= 12 and 2000 <= anio <= 2099):
        raise Http404
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, monthrange(anio, mes)[1])
    dias = dias_habiles(primero, ultimo)
    grilla = construir_grilla(dias, request.user)
    offset = dias[0].weekday() if dias else 0
    anterior = primero - timedelta(days=1)
    siguiente = ultimo + timedelta(days=1)
    hoy = hoy_habil()
    return render(request, "turnos/mes.html", {
        "vista": "mes",
        # La pestaña Día/Semana apunta a hoy si estamos en este mes
        "fecha": hoy if (hoy.year, hoy.month) == (anio, mes) else dias[0],
        "mes_fecha": primero, "offset": range(offset), "hoy": hoy,
        "dias": [(d, grilla[d]) for d in dias],
        "anterior": anterior, "siguiente": siguiente,
        "generado": Inscripcion.objects.filter(fecha__range=(primero, ultimo)).exists(),
    })


# ── Detalle de clase (profesor) ───────────────────────────────
@staff_member_required
def detalle_clase(request, fecha, hora):
    _validar_franja(fecha, hora)
    clase = Clase.objects.filter(fecha=fecha, hora=hora).con_ocupacion().first()
    inscriptos = []
    if clase:
        inscriptos = (clase.inscripciones.filter(estado=ACTIVA)
                      .select_related("alumno__usuario").order_by("alumno__usuario__last_name"))
    params = ParametrosGimnasio.load()
    cupo = clase.cupo if clase else params.cupo_maximo
    ocupados = clase.ocupados if clase else 0
    return render(request, "turnos/clase_detalle.html", {
        "vista": "dia", "fecha": fecha, "hora": hora, "clase": clase,
        "inscriptos": inscriptos, "cupo": cupo, "ocupados": ocupados,
        "lleno": ocupados >= cupo,
        "nivel": clase.nivel_ocupacion if clase else "libre",
        "form": AsignarAlumnoForm(),
    })


@staff_member_required
@require_POST
def asignar_alumno(request, fecha, hora):
    _validar_franja(fecha, hora)
    form = AsignarAlumnoForm(request.POST)
    if form.is_valid():
        try:
            res = services.asignar_por_profesor(request.user, form.cleaned_data["alumno"],
                                                fecha, hora)
            messages.success(request, f"{form.cleaned_data['alumno']} asignado/a.")
            _mostrar(request, res)
        except services.ReglaNegocioError as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "Elegí un alumno.")
    return redirect("turnos:clase", fecha=fecha, hora=hora)


@staff_member_required
@require_POST
def cancelar_clase(request, fecha, hora):
    _validar_franja(fecha, hora)
    clase = services.alternar_cancelacion_clase(request.user, fecha, hora)
    messages.info(request, f"Clase {clase} {'cancelada' if clase.cancelada else 'reactivada'}.")
    return redirect("turnos:clase", fecha=fecha, hora=hora)


@staff_member_required
@require_POST
def generar_mes_view(request, anio, mes):
    res = services.generar_mes(anio, mes)
    messages.success(request, f"{len(res.inscripciones)} inscripciones generadas.")
    _mostrar(request, res)
    return redirect("turnos:mes", anio=anio, mes=mes)


# ── Acciones del alumno (y del profesor) ──────────────────────
@login_required
@require_POST
def cambiar_horario(request, inscripcion_id):
    qs = Inscripcion.objects.filter(estado=ACTIVA)
    if not services.es_staff(request.user):
        qs = qs.filter(alumno__usuario=request.user)
    insc = get_object_or_404(qs, pk=inscripcion_id)

    form = CambioHorarioForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Datos inválidos.")
        return _volver(request, "turnos:inicio")

    d = form.cleaned_data
    try:
        if d["alcance"] == "PUNTUAL":
            res = services.cambiar_puntual(request.user, insc.pk, d["fecha"], d["hora"])
        else:
            if not insc.horario_origen_id:
                raise services.ReglaNegocioError(
                    "Este turno no proviene de tu semana tipo: usá «Solo este día»."
                )
            res = services.cambiar_permanente(
                request.user, insc.horario_origen_id, d["fecha"].weekday(), d["hora"],
                desde=min(insc.fecha, d["fecha"]),
            )
        messages.success(request, "Horario actualizado.")
        _mostrar(request, res)
    except services.ReglaNegocioError as e:
        messages.error(request, str(e))
    return _volver(request, "turnos:inicio")


@login_required
@require_POST
def baja_inscripcion(request, inscripcion_id):
    try:
        insc = services.dar_de_baja(request.user, inscripcion_id)
        messages.info(request, f"Se dio de baja a {insc.alumno} del {insc.clase}.")
    except Inscripcion.DoesNotExist:
        raise Http404
    except services.ReglaNegocioError as e:
        messages.error(request, str(e))
    return _volver(request, "turnos:inicio")
