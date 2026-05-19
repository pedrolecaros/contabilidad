#!/bin/bash
# ── Contabilidad Chile ── Inicio en Linux/Mac ─────────────────────────────────

cd "$(dirname "$0")"

# Cargar .env si existe
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Verificar Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python3 no encontrado."
    exit 1
fi

# Instalar dependencias si faltan
python3 -c "import flask" 2>/dev/null || pip3 install -r requirements.txt

echo ""
echo "============================================"
echo " Contabilidad Chile"
IP=$(hostname -I | awk '{print $1}')
echo " Local:  http://localhost:5000"
echo " Red:    http://$IP:5000"
echo " Ctrl+C para detener"
echo "============================================"
echo ""

python3 -m flask run --host 0.0.0.0 --port 5000
