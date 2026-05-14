"""
Test suite for the contabilidad app.
Run with:  python tests.py
"""
import sys
import os
import unittest
from datetime import date

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

# ── Engine unit tests ──────────────────────────────────────────────────────────

class TestRemuneracionesEngine(unittest.TestCase):
    """Tests for engine/remuneraciones.py — pure calculation, no DB."""

    def _emp(self, **kwargs):
        """Return a mock employee-like namespace."""
        from types import SimpleNamespace
        defaults = dict(
            sueldo_base=1_000_000,
            bono_colacion=55_000,
            bono_movilizacion=30_000,
            otros_haberes=0,
            afp='Habitat',
            tasa_afp_comision=0.0127,
            tipo_salud='FONASA',
            isapre=None,
            monto_isapre=0,
            tasa_mutual=0.0093,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_afp_descuento_fonasa(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        utm = 68_306
        r = calcular(emp, utm)
        # AFP = renta_imponible * (10% + 1.27%) = 1_000_000 * 0.1127
        self.assertEqual(r['afp'], round(1_000_000 * 0.1127))

    def test_salud_fonasa(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['salud'], round(1_000_000 * 0.07))

    def test_cesantia_trabajador(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['cesantia_trab'], round(1_000_000 * 0.006))

    def test_sis_empleador(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['sis'], round(1_000_000 * 0.0149))

    def test_total_haberes(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        expected = 1_000_000 + 55_000 + 30_000
        self.assertEqual(r['total_haberes'], expected)

    def test_renta_imponible_excluye_colacion_movil(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        # colación y movilización no son imponibles
        self.assertEqual(r['renta_imponible'], 1_000_000)

    def test_liquido_equals_haberes_minus_descuentos(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['liquido'], r['total_haberes'] - r['total_descuentos'])

    def test_costo_empresa(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        expected = r['total_haberes'] + r['sis'] + r['cesantia_emp'] + r['mutual']
        self.assertEqual(r['costo_empresa'], expected)

    def test_impuesto_primer_tramo_es_cero(self):
        """Sueldo base bajo → sin impuesto."""
        from engine.remuneraciones import calcular
        emp = self._emp(sueldo_base=500_000, bono_colacion=0, bono_movilizacion=0)
        r = calcular(emp, 68_306)
        self.assertEqual(r['impuesto_renta'], 0)

    def test_impuesto_segundo_tramo(self):
        """1 500 000 → debe caer en tramo 4% (entre 13.5 y 30 UTM)."""
        from engine.remuneraciones import calcular, _calcular_impuesto, TASA_AFP_OBLIGATORIO, AFP_COMISIONES, TASA_SALUD_FONASA, TASA_CESANTIA_TRAB
        utm = 68_306
        renta = 1_500_000
        afp_desc = round(renta * (TASA_AFP_OBLIGATORIO + AFP_COMISIONES['Habitat']))
        sal_desc = round(renta * TASA_SALUD_FONASA)
        ces_desc = round(renta * TASA_CESANTIA_TRAB)
        base = renta - afp_desc - sal_desc - ces_desc
        imp = _calcular_impuesto(base, utm)
        self.assertGreaterEqual(imp, 0)
        # base_utm ~= 17.3 → tramo 4%
        base_utm = base / utm
        self.assertGreater(base_utm, 13.5)
        self.assertLess(base_utm, 30.0)

    def test_isapre_usa_maximo(self):
        """ISAPRE: descuento = max(7% renta, monto_isapre)."""
        from engine.remuneraciones import calcular
        emp = self._emp(tipo_salud='ISAPRE', monto_isapre=200_000)
        r = calcular(emp, 68_306)
        # 7% de 1_000_000 = 70_000 < 200_000 → debe usar 200_000
        self.assertEqual(r['salud'], 200_000)

    def test_isapre_usa_7pct_si_mayor(self):
        """ISAPRE: si 7% > monto_isapre, usar 7%."""
        from engine.remuneraciones import calcular
        emp = self._emp(sueldo_base=5_000_000, tipo_salud='ISAPRE', monto_isapre=50_000,
                        bono_colacion=0, bono_movilizacion=0)
        r = calcular(emp, 68_306)
        # 7% de 5_000_000 = 350_000 > 50_000 → usa 350_000
        self.assertEqual(r['salud'], 350_000)

    def test_horas_extra_aumentan_renta_imponible(self):
        from engine.remuneraciones import calcular
        emp = self._emp(bono_colacion=0, bono_movilizacion=0)
        r_sin = calcular(emp, 68_306)
        r_con = calcular(emp, 68_306, horas_extra=50_000)
        self.assertEqual(r_con['renta_imponible'], r_sin['renta_imponible'] + 50_000)

    def test_afp_comision_modelo(self):
        from engine.remuneraciones import calcular, AFP_COMISIONES, TASA_AFP_OBLIGATORIO
        emp = self._emp(afp='Modelo', tasa_afp_comision=AFP_COMISIONES['Modelo'])
        r = calcular(emp, 68_306)
        expected = round(1_000_000 * (TASA_AFP_OBLIGATORIO + AFP_COMISIONES['Modelo']))
        self.assertEqual(r['afp'], expected)

    def test_utm_cero_no_falla(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, utm=0)
        self.assertEqual(r['impuesto_renta'], 0)

    def test_gratificacion_es_imponible(self):
        from engine.remuneraciones import calcular
        emp = self._emp(bono_colacion=0, bono_movilizacion=0)
        r_sin = calcular(emp, 68_306)
        r_con = calcular(emp, 68_306, gratificacion=100_000)
        self.assertEqual(r_con['renta_imponible'], r_sin['renta_imponible'] + 100_000)


# ── Flask integration tests ────────────────────────────────────────────────────

class TestFlaskRoutes(unittest.TestCase):
    """Integration tests: create test DB, hit routes, check 200/302."""

    @classmethod
    def setUpClass(cls):
        from app import create_app
        from config import Config

        class TestConfig(Config):
            TESTING = True
            # Isolated in-memory DB — never touches contabilidad.db
            SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
            SECRET_KEY = 'test-key'

        # Pass config BEFORE init_db runs inside create_app
        cls.app = create_app(config_override=TestConfig)
        from models import db
        with cls.app.app_context():
            db.create_all()
            cls._seed(cls.app)
        cls.client = cls.app.test_client()

    @classmethod
    def _seed(cls, app):
        from models import db, Empresa, Empleado
        emp = Empresa(rut='76.123.456-7', razon_social='Empresa Test SpA', activa=True)
        db.session.add(emp)
        db.session.flush()
        cls.eid = emp.id
        worker = Empleado(
            empresa_id=emp.id,
            rut='12.345.678-9',
            nombre='Juan Prueba',
            cargo='Analista',
            tipo_contrato='INDEFINIDO',
            sueldo_base=1_200_000,
            afp='Habitat',
            tasa_afp_comision=0.0127,
            tipo_salud='FONASA',
            bono_colacion=55_000,
            bono_movilizacion=30_000,
            otros_haberes=0,
            tasa_mutual=0.0093,
            activo=True,
        )
        db.session.add(worker)
        db.session.commit()
        cls.emp_id = worker.id

    def get(self, url):
        return self.client.get(url, follow_redirects=True)

    def post(self, url, data):
        return self.client.post(url, data=data, follow_redirects=True)

    # ── Remuneraciones module ──────────────────────────────────────────────────

    def test_r01_index(self):
        r = self.get(f'/empresa/{self.eid}/remuneraciones')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Juan Prueba', r.data)

    def test_r02_form_nuevo(self):
        r = self.get(f'/empresa/{self.eid}/remuneraciones/nuevo')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Nuevo Empleado', r.data)

    def test_r03_crear_empleado(self):
        r = self.post(f'/empresa/{self.eid}/remuneraciones/nuevo', {
            'rut': '11.111.111-1', 'nombre': 'María López', 'cargo': 'Contadora',
            'tipo_contrato': 'INDEFINIDO', 'sueldo_base': '800000',
            'afp': 'Modelo', 'tasa_afp_comision': '0.58',
            'tipo_salud': 'FONASA', 'isapre': '', 'monto_isapre': '0',
            'bono_colacion': '40000', 'bono_movilizacion': '20000',
            'otros_haberes': '0', 'tasa_mutual': '0.93', 'activo': 'on',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Mar', r.data)

    def test_r04_form_editar(self):
        r = self.get(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/editar')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Juan Prueba', r.data)

    def test_r05_liquidar_form(self):
        r = self.get(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Calcular', r.data)

    def test_r06_generar_liquidacion(self):
        r = self.post(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar', {
            'periodo': '2025-01', 'utm': '68306',
            'horas_extra': '0', 'gratificacion': '0', 'otros': '0',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'2025-01', r.data)

    def test_r07_detalle_liquidacion(self):
        from models import Liquidacion
        with self.app.app_context():
            liq = Liquidacion.query.filter_by(empleado_id=self.emp_id).first()
            self.assertIsNotNone(liq, 'Liquidación no fue creada')
            liq_id = liq.id
        r = self.get(f'/empresa/{self.eid}/remuneraciones/liquidacion/{liq_id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Sueldo L', r.data)

    def test_r08_imprimir(self):
        from models import Liquidacion
        with self.app.app_context():
            liq = Liquidacion.query.filter_by(empleado_id=self.emp_id).first()
            liq_id = liq.id
        r = self.get(f'/empresa/{self.eid}/remuneraciones/liquidacion/{liq_id}/imprimir')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'LIQUIDACI', r.data)

    def test_r09_historial(self):
        r = self.get(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/historial')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'2025-01', r.data)

    def test_r10_no_duplicar_periodo(self):
        """Second liquidation for same period should redirect with warning, not create a duplicate."""
        r = self.post(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar', {
            'periodo': '2025-01', 'utm': '68306',
            'horas_extra': '0', 'gratificacion': '0', 'otros': '0',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Ya existe', r.data)

    def test_r11_segundo_periodo(self):
        r = self.post(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar', {
            'periodo': '2025-02', 'utm': '68500',
            'horas_extra': '50000', 'gratificacion': '0', 'otros': '0',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'2025-02', r.data)

    def test_r12_eliminar_liquidacion(self):
        from models import Liquidacion
        with self.app.app_context():
            liq = Liquidacion.query.filter_by(empleado_id=self.emp_id, periodo='2025-02').first()
            self.assertIsNotNone(liq)
            liq_id = liq.id
        r = self.post(
            f'/empresa/{self.eid}/remuneraciones/liquidacion/{liq_id}/eliminar', {})
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(Liquidacion.query.get(liq_id))

    # ── Core app routes still work ─────────────────────────────────────────────

    def test_a01_home(self):
        r = self.get('/')
        self.assertEqual(r.status_code, 200)

    def test_a02_asientos_lista(self):
        r = self.get(f'/empresa/{self.eid}/asientos')
        self.assertEqual(r.status_code, 200)

    def test_a03_pendientes(self):
        r = self.get(f'/empresa/{self.eid}/pendientes')
        self.assertEqual(r.status_code, 200)

    def test_a04_importar(self):
        r = self.get(f'/empresa/{self.eid}/importar')
        self.assertEqual(r.status_code, 200)

    def test_a05_conciliacion(self):
        r = self.get(f'/empresa/{self.eid}/conciliacion')
        self.assertEqual(r.status_code, 200)

    def test_a06_contrapartes(self):
        r = self.get(f'/empresa/{self.eid}/contrapartes')
        self.assertEqual(r.status_code, 200)

    def test_a07_plan_cuentas(self):
        r = self.get(f'/empresa/{self.eid}/cuentas')
        self.assertEqual(r.status_code, 200)

    def test_a08_reportes_balance(self):
        r = self.get(f'/empresa/{self.eid}/reportes/balance')
        self.assertEqual(r.status_code, 200)

    def test_a09_reportes_diario(self):
        r = self.get(f'/empresa/{self.eid}/reportes/diario')
        self.assertEqual(r.status_code, 200)

    def test_a10_validacion(self):
        r = self.get(f'/empresa/{self.eid}/validar')
        self.assertEqual(r.status_code, 200)


# ── Calculation correctness cross-check ───────────────────────────────────────

class TestCalculationCrossCheck(unittest.TestCase):
    """Cross-check: manual expected values vs engine output."""

    def _run(self, sueldo, afp_nombre, tasa_com, utm=68_306, colacion=0, movil=0):
        from types import SimpleNamespace
        from engine.remuneraciones import calcular
        emp = SimpleNamespace(
            sueldo_base=sueldo, bono_colacion=colacion, bono_movilizacion=movil,
            otros_haberes=0, afp=afp_nombre, tasa_afp_comision=tasa_com,
            tipo_salud='FONASA', isapre=None, monto_isapre=0, tasa_mutual=0.0093,
        )
        return calcular(emp, utm)

    def test_sueldo_minimo_2024(self):
        """Sueldo mínimo 2024 = $500.000 → sin impuesto, cálculos básicos correctos."""
        r = self._run(500_000, 'Habitat', 0.0127)
        self.assertEqual(r['total_haberes'], 500_000)
        self.assertEqual(r['renta_imponible'], 500_000)
        self.assertEqual(r['afp'], round(500_000 * 0.1127))
        self.assertEqual(r['salud'], round(500_000 * 0.07))
        self.assertEqual(r['cesantia_trab'], round(500_000 * 0.006))
        self.assertEqual(r['impuesto_renta'], 0)
        self.assertEqual(r['liquido'],
                         500_000 - r['afp'] - r['salud'] - r['cesantia_trab'])

    def test_sueldo_alto_con_impuesto(self):
        """Sueldo alto → impuesto > 0."""
        r = self._run(5_000_000, 'Cuprum', 0.0144)
        self.assertGreater(r['impuesto_renta'], 0)

    def test_todos_los_campos_presentes(self):
        r = self._run(1_000_000, 'Uno', 0.0049)
        campos = ['sueldo_base','horas_extra','bono_colacion','bono_movilizacion',
                  'otros_haberes','gratificacion','total_haberes','renta_imponible',
                  'afp','salud','cesantia_trab','impuesto_renta','total_descuentos',
                  'liquido','sis','cesantia_emp','mutual','costo_empresa','utm',
                  'afp_nombre','tasa_afp','tipo_salud','isapre']
        for c in campos:
            self.assertIn(c, r, f'Campo ausente: {c}')

    def test_liquido_positivo(self):
        """Sueldo razonable → líquido siempre positivo."""
        r = self._run(1_000_000, 'Habitat', 0.0127)
        self.assertGreater(r['liquido'], 0)


if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestRemuneracionesEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestCalculationCrossCheck))
    suite.addTests(loader.loadTestsFromTestCase(TestFlaskRoutes))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
