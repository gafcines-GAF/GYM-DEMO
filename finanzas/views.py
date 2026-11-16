import csv
from datetime import date

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from turnos.models import Alumno

from . import consultas, services
from .formato import pesos
from .forms import MotivoForm, PagoForm
from .models import Cuota, Pago, ParametrosFinanzas, primer_dia
from .permisos import requiere_finanzas


def _leer_periodo(valor: str | None) -> date:
    try:
        return date.fromisoformat(f"{valor}-01")
    except (TypeError, ValueError):
        return primer_dia(timezone.localdate())


def _siguiente(periodo: date) -> date:
    return date(periodo.year + periodo.month // 12, periodo.month % 12 + 1, 1)


# ── Dashboard ─────────────────────────────────────────────────
@requiere_finanzas
def dashboard(request):
    periodo = _leer_periodo(request.GET.get("periodo"))
    hoy = timezone.localdate()
    return render(request, "finanzas/dashboard.html", {
        "seccion": "dashboard",
        "periodo": periodo,
        "anterior": consultas.mes_anterior(periodo),
        "siguiente": _siguiente(periodo),
        "es_mes_actual": periodo == primer_dia(hoy),
        "ingresos": consultas.ingresos(periodo),
        "facturacion": consultas.facturacion(periodo),
        "alumnos": consultas.estado_alumnos(periodo, hoy),
        "deuda": consultas.total_deuda_vencida(hoy),
        "deudores": consultas.deudores(hoy)[:50],
        "evolucion": consultas.evolucion_ingresos(periodo),
        "parametros": ParametrosFinanzas.load(),
    })


@requiere_finanzas
@require_POST
def generar(request):
    periodo = _leer_periodo(request.POST.get("periodo"))
    res = services.generar_cuotas(periodo)
    messages.success(request, f"{periodo:%m/%Y}: {res.creadas} cuotas creadas "
                              f"({res.ya_existian} ya existían).")
    for aviso in res.omitidos:
        messages.warning(request, aviso)
    return redirect(f"{reverse('finanzas:dashboard')}?periodo={periodo:%Y-%m}")


def _celda(texto) -> str:
    """Evita la inyección de fórmulas en Excel (un nombre como '=HYPERLINK(...)')."""
    texto = str(texto or "")
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


@requiere_finanzas
def deudores_csv(request):
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="deudores_{timezone.localdate()}.csv"'
    respuesta.write("﻿")                       # BOM: Excel lee bien los acentos
    w = csv.writer(respuesta, delimiter=";")
    w.writerow(["Alumno", "Usuario", "Teléfono", "Activo", "Cuotas impagas", "Vencida desde",
                "Deuda vencida", "Deuda total", "Recargo estimado", "A cobrar"])
    for d in consultas.deudores():
        w.writerow([_celda(f'{d["alumno__usuario__last_name"]}, {d["alumno__usuario__first_name"]}'),
                    _celda(d["alumno__usuario__username"]), _celda(d["alumno__telefono"]),
                    "Sí" if d["alumno__activo"] else "No", d["cuotas_impagas"],
                    d["vencida_desde"].strftime("%d/%m/%Y") if d["vencida_desde"] else "",
                    str(d["deuda_vencida"]).replace(".", ","), str(d["deuda_total"]).replace(".", ","),
                    str(d["recargo_estimado"]).replace(".", ","), str(d["a_cobrar"]).replace(".", ",")])
    return respuesta


# ── Cobro ─────────────────────────────────────────────────────
@requiere_finanzas
def buscar(request):
    q = request.GET.get("q", "").strip()
    alumnos = Alumno.objects.select_related("usuario")
    if q:
        for palabra in q.split():
            alumnos = alumnos.filter(Q(usuario__first_name__icontains=palabra)
                                     | Q(usuario__last_name__icontains=palabra)
                                     | Q(usuario__username__icontains=palabra))
    alumnos = alumnos.order_by("-activo", "usuario__last_name", "usuario__first_name")[:40]
    return render(request, "finanzas/buscar.html",
                  {"seccion": "cobrar", "q": q, "alumnos": alumnos})


@requiere_finanzas
def ficha_alumno(request, alumno_id):
    alumno = get_object_or_404(Alumno.objects.select_related("usuario"), pk=alumno_id)
    pct = ParametrosFinanzas.load().recargo_porcentaje
    cuotas = list(alumno.cuotas.prefetch_related("pagos__registrado_por").order_by("-periodo"))
    impagas = []
    for c in cuotas:
        c.recargo_hoy = c.recargo_estimado(pct)
        c.a_cobrar_hoy = c.saldo_pendiente + c.recargo_hoy
        if c.estado in Cuota.IMPAGAS:
            # Precarga lo que se cobra hoy (incluye el recargo si ya venció)
            c.form = PagoForm(initial={"monto": c.a_cobrar_hoy})
            impagas.append(c)
    impagas.sort(key=lambda c: c.periodo)          # la más vieja primero
    return render(request, "finanzas/alumno.html", {
        "seccion": "cobrar", "alumno": alumno, "cuotas": cuotas, "impagas": impagas,
        "deuda": sum((c.a_cobrar_hoy for c in impagas), start=0),
        "descuentos": alumno.descuentos.vigentes_en(timezone.localdate()),
        "parametros": ParametrosFinanzas.load(),
        "motivo_form": MotivoForm(),
    })


@requiere_finanzas
@require_POST
def cobrar(request, cuota_id):
    cuota = get_object_or_404(Cuota, pk=cuota_id)
    form = PagoForm(request.POST)
    if form.is_valid():
        try:
            pago = services.registrar_pago(request.user, cuota.pk, **form.cleaned_data)
            pago.cuota.refresh_from_db()
            texto = f"Pago de {pesos(pago.monto)} registrado."
            if pago.cuota.recargo and pago.cuota.recargo_porcentaje:
                texto += f" Incluye recargo de {pesos(pago.cuota.recargo)} por pago fuera de término."
            texto += (" La cuota quedó pagada." if pago.cuota.estado == Cuota.Estado.PAGADA
                      else f" Saldo restante: {pesos(pago.cuota.saldo_pendiente)}.")
            messages.success(request, texto)
        except services.FinanzasError as e:
            messages.error(request, str(e))
    else:
        errores = "; ".join(f"{form.fields[c].label or c}: {', '.join(e)}"
                            for c, e in form.errors.items())
        messages.error(request, f"Revisá los datos del pago ({errores}).")
    return redirect("finanzas:alumno", alumno_id=cuota.alumno_id)


@requiere_finanzas
@require_POST
def anular_pago(request, pago_id):
    pago = get_object_or_404(Pago.objects.select_related("cuota"), pk=pago_id)
    try:
        services.anular_pago(request.user, pago.pk, request.POST.get("motivo", ""))
        messages.info(request, f"Pago de {pesos(pago.monto)} anulado.")
    except services.FinanzasError as e:
        messages.error(request, str(e))
    return redirect("finanzas:alumno", alumno_id=pago.cuota.alumno_id)


@requiere_finanzas
@require_POST
def anular_cuota(request, cuota_id):
    cuota = get_object_or_404(Cuota, pk=cuota_id)
    try:
        services.anular_cuota(request.user, cuota.pk, request.POST.get("motivo", ""))
        messages.info(request, f"Cuota {cuota.periodo:%m/%Y} anulada.")
    except services.FinanzasError as e:
        messages.error(request, str(e))
    return redirect("finanzas:alumno", alumno_id=cuota.alumno_id)


@requiere_finanzas
@require_POST
def generar_cuota_alumno(request, alumno_id):
    alumno = get_object_or_404(Alumno, pk=alumno_id)
    res = services.generar_cuotas(timezone.localdate(), alumnos=Alumno.objects.filter(pk=alumno.pk))
    if res.creadas:
        messages.success(request, "Cuota del mes generada.")
    for aviso in res.omitidos:
        messages.warning(request, aviso)
    if not res.creadas and not res.omitidos:
        messages.info(request, "El alumno ya tenía la cuota del mes (o está inactivo).")
    return redirect("finanzas:alumno", alumno_id=alumno.pk)
