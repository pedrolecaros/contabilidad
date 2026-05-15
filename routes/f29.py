from flask import Blueprint, render_template, request
from models import db, DocumentoSII, Liquidacion, Empresa
from datetime import date
import calendar

bp = Blueprint('f29', __name__)

MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
         'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def _calcular_f29(empresa_id, anio, mes, tasa_ppm_pct):
    desde = date(anio, mes, 1)
    hasta = date(anio, mes, calendar.monthrange(anio, mes)[1])
    periodo = f'{anio}-{mes:02d}'
    tasa_ppm = tasa_ppm_pct / 100.0

    # ── VENTAS ────────────────────────────────────────────────────────────────
    ventas = (DocumentoSII.query
              .filter_by(empresa_id=empresa_id, tipo_libro='VENTAS')
              .filter(DocumentoSII.fecha >= desde, DocumentoSII.fecha <= hasta)
              .all())

    ventas_neto   = sum(d.monto_neto  for d in ventas)
    ventas_exento = sum(d.monto_exento for d in ventas)
    ventas_total  = ventas_neto + ventas_exento

    iva_debito_bruto = sum(d.iva for d in ventas if d.iva > 0)
    nc_emitidas      = abs(sum(d.iva for d in ventas if d.iva < 0))
    iva_debito_neto  = iva_debito_bruto - nc_emitidas

    # ── COMPRAS ───────────────────────────────────────────────────────────────
    compras = (DocumentoSII.query
               .filter_by(empresa_id=empresa_id, tipo_libro='COMPRAS')
               .filter(DocumentoSII.fecha >= desde, DocumentoSII.fecha <= hasta)
               .all())

    compras_neto        = sum(d.monto_neto  for d in compras)
    credito_bruto       = sum(d.iva for d in compras if d.iva > 0)
    nc_recibidas        = abs(sum(d.iva for d in compras if d.iva < 0))
    credito_fiscal_neto = credito_bruto - nc_recibidas

    # ── IVA resultado ─────────────────────────────────────────────────────────
    iva_neto         = iva_debito_neto - credito_fiscal_neto
    iva_pagar        = max(0, round(iva_neto))
    remanente        = max(0, round(-iva_neto))

    # ── HONORARIOS ────────────────────────────────────────────────────────────
    honorarios = (DocumentoSII.query
                  .filter_by(empresa_id=empresa_id, tipo_libro='HONORARIOS')
                  .filter(DocumentoSII.fecha >= desde, DocumentoSII.fecha <= hasta)
                  .all())

    honor_bruto          = sum(d.total for d in honorarios)
    retencion_honorarios = round(sum(d.iva for d in honorarios))

    # ── SEGUNDA CATEGORÍA ─────────────────────────────────────────────────────
    liqqs = (Liquidacion.query
             .filter_by(empresa_id=empresa_id, periodo=periodo, estado='EMITIDA')
             .all())
    segunda_categoria = round(sum(l.impuesto_renta for l in liqqs))
    n_empleados = len(liqqs)

    # ── PPM ───────────────────────────────────────────────────────────────────
    ppm_base = ventas_total
    ppm      = round(ppm_base * tasa_ppm)

    # ── TOTAL ─────────────────────────────────────────────────────────────────
    total = iva_pagar + ppm + retencion_honorarios + segunda_categoria

    return dict(
        periodo=periodo,
        mes_nombre=MESES[mes - 1],
        desde=desde,
        hasta=hasta,
        # Ventas
        n_ventas=len(ventas),
        ventas_neto=ventas_neto,
        ventas_exento=ventas_exento,
        ventas_total=ventas_total,
        iva_debito_bruto=iva_debito_bruto,
        nc_emitidas=nc_emitidas,
        iva_debito_neto=iva_debito_neto,
        # Compras
        n_compras=len(compras),
        compras_neto=compras_neto,
        credito_bruto=credito_bruto,
        nc_recibidas=nc_recibidas,
        credito_fiscal_neto=credito_fiscal_neto,
        # IVA
        iva_neto=iva_neto,
        iva_pagar=iva_pagar,
        remanente=remanente,
        # Honorarios
        n_honorarios=len(honorarios),
        honor_bruto=honor_bruto,
        retencion_honorarios=retencion_honorarios,
        # Segunda categoría
        segunda_categoria=segunda_categoria,
        n_empleados=n_empleados,
        # PPM
        tasa_ppm_pct=tasa_ppm_pct,
        ppm_base=ppm_base,
        ppm=ppm,
        # Total
        total=total,
    )


@bp.route('/empresa/<int:eid>/f29', methods=['GET', 'POST'])
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()

    # Período por defecto: mes anterior
    if hoy.month == 1:
        anio_def, mes_def = hoy.year - 1, 12
    else:
        anio_def, mes_def = hoy.year, hoy.month - 1

    anio      = int(request.values.get('anio', anio_def))
    mes       = int(request.values.get('mes', mes_def))
    tasa_ppm  = float(request.values.get('tasa_ppm', empresa.tasa_ppm or 1.0))

    datos = _calcular_f29(eid, anio, mes, tasa_ppm)

    return render_template(
        'f29/resumen.html',
        empresa=empresa,
        datos=datos,
        anio=anio,
        mes=mes,
        meses=MESES,
        anios=list(range(hoy.year - 3, hoy.year + 1)),
    )
