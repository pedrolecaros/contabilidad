from models import db, AsientoAudit


def registrar_auditoria(asiento, accion: str, descripcion: str = None):
    """Registra una entrada de auditoría para un asiento."""
    if not descripcion:
        descripcion = {
            'CREAR': f'Asiento N°{asiento.numero} creado',
            'EDITAR': f'Asiento N°{asiento.numero} editado',
            'CONFIRMAR': f'Asiento N°{asiento.numero} confirmado',
            'ANULAR': f'Asiento N°{asiento.numero} anulado',
        }.get(accion, accion)
    db.session.add(AsientoAudit(
        asiento_id=asiento.id,
        accion=accion,
        descripcion=descripcion,
    ))
