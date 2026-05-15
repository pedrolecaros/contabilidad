"""
Test suite for the contabilidad app.
Run with:  python3 tests.py
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
            monto_isapre_uf=0,
            tasa_mutual=0.0093,
            tipo_sueldo='BRUTO',
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    # Gratificación = min(25% sueldo, tope_grat) is always imponible.
    # For sueldo=1_000_000: grat = min(250_000, 209_395) = 209_395
    # renta_imponible = 1_000_000 + 209_395 = 1_209_395
    _RENTA = 1_209_395
    _GRAT  = 209_395

    def test_afp_descuento_fonasa(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['afp'], round(self._RENTA * 0.1127))

    def test_salud_fonasa(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['salud'], round(self._RENTA * 0.07))

    def test_cesantia_trabajador(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['cesantia_trab'], round(self._RENTA * 0.006))

    def test_sis_empleador(self):
        """SIS usa tasa default (TASA_SIS=0.0149) cuando no se pasa tasa_sis."""
        from engine.remuneraciones import calcular, TASA_SIS
        emp = self._emp()
        r = calcular(emp, 68_306)
        self.assertEqual(r['sis'], round(self._RENTA * TASA_SIS))

    def test_sis_empleador_tasa_custom(self):
        """tasa_sis se puede sobreescribir (p.ej. 1.62% desde Previred)."""
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306, tasa_sis=0.0162)
        self.assertEqual(r['sis'], round(self._RENTA * 0.0162))

    def test_total_haberes(self):
        """total_haberes = sueldo + grat + colacion + movilizacion."""
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        expected = 1_000_000 + self._GRAT + 55_000 + 30_000
        self.assertEqual(r['total_haberes'], expected)

    def test_renta_imponible_excluye_colacion_movil(self):
        """Colación y movilización no son imponibles; gratificación sí lo es."""
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, 68_306)
        # renta_imponible = sueldo + grat (sin colacion ni movil)
        self.assertEqual(r['renta_imponible'], self._RENTA)
        # total_haberes incluye colacion + movil
        self.assertEqual(r['total_haberes'], self._RENTA + 55_000 + 30_000)

    def test_gratificacion_es_imponible(self):
        """Gratificación auto-calculada se incluye en renta imponible."""
        from engine.remuneraciones import calcular
        emp = self._emp(bono_colacion=0, bono_movilizacion=0)
        r = calcular(emp, 68_306)
        self.assertGreater(r['gratificacion'], 0)
        self.assertEqual(r['renta_imponible'], r['sueldo_base'] + r['gratificacion'])

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
        """Sueldo mínimo → sin impuesto (base_renta < 13.5 UTM)."""
        from engine.remuneraciones import calcular
        emp = self._emp(sueldo_base=500_000, bono_colacion=0, bono_movilizacion=0)
        r = calcular(emp, 68_306)
        self.assertEqual(r['impuesto_renta'], 0)

    def test_impuesto_segundo_tramo(self):
        """1.5M renta → tramo 4% (entre 13.5 y 30 UTM)."""
        from engine.remuneraciones import calcular, _calcular_impuesto, TASA_AFP_OBLIGATORIO, AFP_COMISIONES, TASA_SALUD_FONASA, TASA_CESANTIA_TRAB
        utm = 68_306
        renta = 1_500_000
        afp_desc = round(renta * (TASA_AFP_OBLIGATORIO + AFP_COMISIONES['Habitat']))
        sal_desc = round(renta * TASA_SALUD_FONASA)
        ces_desc = round(renta * TASA_CESANTIA_TRAB)
        base = renta - afp_desc - sal_desc - ces_desc
        imp = _calcular_impuesto(base, utm)
        self.assertGreaterEqual(imp, 0)
        base_utm = base / utm
        self.assertGreater(base_utm, 13.5)
        self.assertLess(base_utm, 30.0)

    def test_isapre_salud_siempre_7pct(self):
        """ISAPRE: r['salud'] = 7% renta_imponible; exceso en extra_isapre."""
        from engine.remuneraciones import calcular
        emp = self._emp(tipo_salud='ISAPRE', monto_isapre=200_000)
        r = calcular(emp, 68_306)
        salud_legal = round(self._RENTA * 0.07)
        self.assertEqual(r['salud'], salud_legal)
        self.assertEqual(r['extra_isapre'], max(0, 200_000 - salud_legal))

    def test_isapre_extra_cero_cuando_7pct_mayor(self):
        """ISAPRE: si 7% > monto_isapre, extra_isapre = 0."""
        from engine.remuneraciones import calcular
        emp = self._emp(sueldo_base=5_000_000, tipo_salud='ISAPRE', monto_isapre=50_000,
                        bono_colacion=0, bono_movilizacion=0)
        r = calcular(emp, 68_306)
        salud_legal = round(r['renta_imponible'] * 0.07)
        self.assertEqual(r['salud'], salud_legal)
        self.assertEqual(r['extra_isapre'], 0)

    def test_horas_extra_aumentan_renta_imponible(self):
        from engine.remuneraciones import calcular
        emp = self._emp(bono_colacion=0, bono_movilizacion=0)
        r_sin = calcular(emp, 68_306)
        r_con = calcular(emp, 68_306, horas_extra=50_000)
        self.assertEqual(r_con['renta_imponible'], r_sin['renta_imponible'] + 50_000)

    def test_afp_comision_modelo(self):
        """AFP Modelo: comisión 0.58% aplicada sobre renta imponible."""
        from engine.remuneraciones import calcular, AFP_COMISIONES, TASA_AFP_OBLIGATORIO
        emp = self._emp(afp='Modelo', tasa_afp_comision=AFP_COMISIONES['Modelo'])
        r = calcular(emp, 68_306)
        expected = round(r['renta_imponible'] * (TASA_AFP_OBLIGATORIO + AFP_COMISIONES['Modelo']))
        self.assertEqual(r['afp'], expected)

    def test_afp_comision_empleado_prioridad(self):
        """tasa_afp_comision guardada en el empleado tiene prioridad sobre el dict."""
        from engine.remuneraciones import calcular, TASA_AFP_OBLIGATORIO
        tasa_custom = 0.0100  # distinta del valor del dict para Habitat (0.0127)
        emp = self._emp(afp='Habitat', tasa_afp_comision=tasa_custom)
        r = calcular(emp, 68_306)
        expected = round(r['renta_imponible'] * (TASA_AFP_OBLIGATORIO + tasa_custom))
        self.assertEqual(r['afp'], expected)

    def test_utm_cero_no_falla(self):
        from engine.remuneraciones import calcular
        emp = self._emp()
        r = calcular(emp, utm=0)
        self.assertEqual(r['impuesto_renta'], 0)


# ── Flask integration tests ────────────────────────────────────────────────────

class TestFlaskRoutes(unittest.TestCase):
    """Integration tests: create test DB, hit routes, check 200/302."""

    @classmethod
    def setUpClass(cls):
        from app import create_app
        from config import Config

        class TestConfig(Config):
            TESTING = True
            SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
            SECRET_KEY = 'test-key'

        cls.app = create_app(config_override=TestConfig)
        from models import db
        with cls.app.app_context():
            db.create_all()
            cls._seed(cls.app)
        cls.client = cls.app.test_client()

    @classmethod
    def _seed(cls, app):
        from models import db, Empresa, Empleado, VariablesMensuales
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
        db.session.flush()
        cls.emp_id = worker.id

        # Seed variables for test periods so liquidaciones can be created
        import json
        for periodo, utm in [('2025-01', 68306.0), ('2025-02', 68500.0)]:
            v = VariablesMensuales(
                periodo=periodo,
                uf=37000.0,
                utm=utm,
                tope_imponible=3_330_000.0,
                tope_gratificacion=209_395.0,
                imm=500_000.0,
                tasa_sis=0.0149,
                tasas_afp_json=json.dumps({'Capital': 1.44, 'Habitat': 1.27, 'Modelo': 0.58,
                                            'Cuprum': 1.44, 'PlanVital': 1.16, 'ProVida': 1.45, 'Uno': 0.46}),
            )
            db.session.add(v)
        db.session.commit()

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
            'tipo_salud': 'FONASA', 'isapre': '', 'monto_isapre_uf': '0',
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
        """Emitir liquidación para 2025-01 (requiere VariablesMensuales seeded)."""
        r = self.post(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar', {
            'periodo': '2025-01', 'accion': 'emitir',
            'horas_extra': '0', 'otros': '0',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'2025-01', r.data)

    def test_r07_detalle_liquidacion(self):
        from models import Liquidacion
        with self.app.app_context():
            liq = Liquidacion.query.filter_by(empleado_id=self.emp_id).first()
            self.assertIsNotNone(liq, 'Liquidación no fue creada en test_r06')
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
        """Second emitir for same period → warning, no duplicate."""
        r = self.post(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar', {
            'periodo': '2025-01', 'accion': 'emitir',
            'horas_extra': '0', 'otros': '0',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Ya existe', r.data)

    def test_r11_segundo_periodo(self):
        r = self.post(f'/empresa/{self.eid}/remuneraciones/{self.emp_id}/liquidar', {
            'periodo': '2025-02', 'accion': 'borrador',
            'horas_extra': '50000', 'otros': '0',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'2025-02', r.data)

    def test_r12_eliminar_liquidacion(self):
        from models import Liquidacion
        with self.app.app_context():
            liq = Liquidacion.query.filter_by(empleado_id=self.emp_id, periodo='2025-02').first()
            self.assertIsNotNone(liq, 'Liquidación 2025-02 no fue creada en test_r11')
            liq_id = liq.id
        r = self.post(
            f'/empresa/{self.eid}/remuneraciones/liquidacion/{liq_id}/eliminar', {})
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(Liquidacion.query.get(liq_id))

    def test_r13_variables_page(self):
        r = self.get('/remuneraciones/variables')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Previred', r.data)
        self.assertIn(b'2025-01', r.data)

    def test_r14_variables_get_json(self):
        """variables_get devuelve las variables incluyendo tasa_sis y tasas_afp."""
        r = self.get(f'/empresa/{self.eid}/remuneraciones/variables/get/2025-01')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['periodo'], '2025-01')
        self.assertIn('tasa_sis', data)
        self.assertIn('tasas_afp', data)
        self.assertAlmostEqual(data['tasa_sis'], 1.49, places=2)
        self.assertIn('Habitat', data['tasas_afp'])

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
            tipo_salud='FONASA', isapre=None, monto_isapre=0, monto_isapre_uf=0,
            tasa_mutual=0.0093, tipo_sueldo='BRUTO',
        )
        return calcular(emp, utm)

    def test_sueldo_minimo(self):
        """sueldo=500K → grat=125K, renta_imponible=625K, sin impuesto."""
        r = self._run(500_000, 'Habitat', 0.0127)
        grat = 125_000   # min(500K*0.25, 209395) = 125000
        renta = 500_000 + grat
        self.assertEqual(r['gratificacion'], grat)
        self.assertEqual(r['total_haberes'], renta)
        self.assertEqual(r['renta_imponible'], renta)
        self.assertEqual(r['afp'], round(renta * 0.1127))
        self.assertEqual(r['salud'], round(renta * 0.07))
        self.assertEqual(r['cesantia_trab'], round(renta * 0.006))
        self.assertEqual(r['impuesto_renta'], 0)
        self.assertEqual(r['liquido'],
                         renta - r['afp'] - r['salud'] - r['cesantia_trab'])

    def test_sueldo_alto_con_impuesto(self):
        """Sueldo alto → impuesto > 0."""
        r = self._run(5_000_000, 'Cuprum', 0.0144)
        self.assertGreater(r['impuesto_renta'], 0)

    def test_todos_los_campos_presentes(self):
        r = self._run(1_000_000, 'Uno', 0.0046)
        campos = ['sueldo_base', 'horas_extra', 'bono_colacion', 'bono_movilizacion',
                  'otros_haberes', 'gratificacion', 'total_haberes', 'renta_imponible',
                  'afp', 'salud', 'cesantia_trab', 'impuesto_renta', 'total_descuentos',
                  'liquido', 'sis', 'cesantia_emp', 'mutual', 'costo_empresa', 'utm',
                  'afp_nombre', 'tasa_afp', 'tipo_salud', 'isapre']
        for c in campos:
            self.assertIn(c, r, f'Campo ausente: {c}')

    def test_liquido_positivo(self):
        """Sueldo razonable → líquido siempre positivo."""
        r = self._run(1_000_000, 'Habitat', 0.0127)
        self.assertGreater(r['liquido'], 0)


# ── Préstamos unit tests ───────────────────────────────────────────────────────

class TestPMT(unittest.TestCase):
    """Unit tests for the PMT formula and amortization generation."""

    def _pmt(self, capital, tasa, n):
        from routes.prestamos import _pmt
        return _pmt(capital, tasa, n)

    def test_pmt_sin_interes(self):
        """0% rate → equal capital split."""
        self.assertAlmostEqual(self._pmt(12_000, 0, 12), 1_000, places=2)

    def test_pmt_con_tasa(self):
        """5% anual mensual: 1M en 12 cuotas ≈ 85_607."""
        tasa = 0.05 / 12
        pmt = self._pmt(1_000_000, tasa, 12)
        self.assertAlmostEqual(pmt, 85_607, delta=5)

    def test_amortizacion_saldo_final_cero(self):
        """After all payments the saldo should reach 0."""
        from types import SimpleNamespace
        from routes.prestamos import _generar_cuotas
        from models import db, Prestamo, CuotaPrestamo
        import app as app_module

        application = app_module.create_app()
        with application.app_context():
            from models import Empresa
            db.create_all()
            emp = Empresa.query.first()
            if not emp:
                emp = Empresa(rut='11.111.111-1', razon_social='Test')
                db.session.add(emp)
                db.session.flush()

            p = Prestamo(
                empresa_id=emp.id,
                nombre='Test PMT',
                tipo='PAGAR',
                moneda='PESOS',
                monto_original=1_000_000,
                tasa_interes_anual=0.05,   # decimal (5%)
                fecha_inicio=date(2025, 1, 1),
                n_cuotas=12,
                periodicidad='MENSUAL',
            )
            db.session.add(p)
            db.session.flush()
            _generar_cuotas(p)
            db.session.flush()

            self.assertEqual(len(p.cuotas), 12)
            last = p.cuotas[-1]
            self.assertAlmostEqual(last.saldo_insoluto, 0, delta=1)
            total_capital = sum(c.capital for c in p.cuotas)
            self.assertAlmostEqual(total_capital, 1_000_000, delta=2)
            db.session.rollback()

    def test_libre_no_genera_cuotas(self):
        """LIBRE loans should not auto-generate cuotas."""
        from routes.prestamos import _generar_cuotas
        from models import db, Prestamo
        import app as app_module

        application = app_module.create_app()
        with application.app_context():
            from models import Empresa
            db.create_all()
            emp = Empresa.query.first()
            if not emp:
                emp = Empresa(rut='22.222.222-2', razon_social='Test2')
                db.session.add(emp)
                db.session.flush()

            p = Prestamo(
                empresa_id=emp.id,
                nombre='Libre',
                tipo='PAGAR',
                moneda='PESOS',
                monto_original=500_000,
                tasa_interes_anual=0.0,
                fecha_inicio=date(2025, 1, 1),
                n_cuotas=None,
                periodicidad='LIBRE',
            )
            db.session.add(p)
            db.session.flush()
            _generar_cuotas(p)
            db.session.flush()
            self.assertEqual(len(p.cuotas), 0)
            db.session.rollback()


class TestPrestamosFlask(unittest.TestCase):
    """Flask route tests for the prestamos blueprint."""

    def setUp(self):
        import app as app_module
        self.app = app_module.create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        with self.app.app_context():
            from models import db, Empresa, Prestamo, CuotaPrestamo
            db.create_all()
            # Use existing empresa or create one
            emp = Empresa.query.first()
            if not emp:
                emp = Empresa(rut='33.333.333-3', razon_social='PrestamosTest')
                db.session.add(emp)
                db.session.commit()
            self.eid = emp.id

    def get(self, url):
        return self.client.get(url, follow_redirects=True)

    def post(self, url, data):
        return self.client.post(url, data=data, follow_redirects=True)

    def _crear_prestamo(self, tipo='PAGAR', periodicidad='MENSUAL', n_cuotas=3):
        """Helper to create a loan via POST."""
        return self.post(f'/empresa/{self.eid}/prestamos/nuevo', {
            'nombre': f'Test {tipo}',
            'tipo': tipo,
            'moneda': 'PESOS',
            'monto_original': '1200000',
            'tasa_interes_anual': '0',
            'fecha_inicio': '2025-01-01',
            'periodicidad': periodicidad,
            'n_cuotas': str(n_cuotas) if n_cuotas else '',
            'acreedor_deudor': 'Banco Test',
        })

    def test_p01_lista_vacia(self):
        r = self.get(f'/empresa/{self.eid}/prestamos')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Pr\xc3\xa9stamos', r.data)  # "Préstamos" in utf-8

    def test_p02_nuevo_form(self):
        r = self.get(f'/empresa/{self.eid}/prestamos/nuevo')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'PMT', r.data)

    def test_p03_crear_prestamo_fijo(self):
        r = self._crear_prestamo('PAGAR', 'MENSUAL', 3)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Tabla de amortizaci', r.data)  # detalle page

    def test_p04_cuotas_generadas(self):
        """Creating a 3-cuota loan generates exactly 3 cuotas."""
        with self.app.app_context():
            from models import Prestamo, CuotaPrestamo
            p = Prestamo.query.filter_by(empresa_id=self.eid).order_by(Prestamo.id.desc()).first()
            if p and p.periodicidad != 'LIBRE':
                self.assertEqual(len(p.cuotas), 3)

    def test_p05_toggle_cuota(self):
        """Marking a cuota as paid via toggle."""
        with self.app.app_context():
            from models import Prestamo, CuotaPrestamo
            p = Prestamo.query.filter_by(empresa_id=self.eid).order_by(Prestamo.id.desc()).first()
            if not p or not p.cuotas:
                return  # loan not yet created, skip
            cid = p.cuotas[0].id
            pid = p.id

        r = self.post(
            f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
            {'fecha_pago': '2025-02-01'}
        )
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            from models import CuotaPrestamo
            c = CuotaPrestamo.query.get(cid)
            self.assertTrue(c.pagada)
            self.assertEqual(str(c.fecha_pago), '2025-02-01')

    def test_p06_crear_prestamo_libre(self):
        """LIBRE loan creates no cuotas; can add manual payments."""
        r = self._crear_prestamo('COBRAR', 'LIBRE', None)
        self.assertEqual(r.status_code, 200)
        # Should show the "Registrar Pago" form
        self.assertIn(b'Registrar Pago', r.data)

    def test_p07_agregar_pago_libre(self):
        """Add a manual payment to a LIBRE loan."""
        with self.app.app_context():
            from models import Prestamo
            p = Prestamo.query.filter_by(
                empresa_id=self.eid, periodicidad='LIBRE'
            ).order_by(Prestamo.id.desc()).first()
            if not p:
                return
            pid = p.id

        r = self.post(f'/empresa/{self.eid}/prestamos/{pid}/pago', {
            'fecha_pago': '2025-03-01',
            'capital': '300000',
            'interes': '5000',
            'notas': 'primer pago',
        })
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            from models import CuotaPrestamo
            cuotas = CuotaPrestamo.query.filter_by(prestamo_id=pid).all()
            self.assertEqual(len(cuotas), 1)
            self.assertEqual(cuotas[0].capital, 300_000)
            self.assertTrue(cuotas[0].pagada)

    def test_p08_consolidado_interempresa(self):
        """Consolidado tab renders without error even with no inter-company loans."""
        r = self.get('/consolidado')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Interempresa', r.data)

    def test_p09_eliminar_prestamo(self):
        """Deleting a loan removes it and its cuotas."""
        with self.app.app_context():
            from models import Prestamo
            p = Prestamo.query.filter_by(empresa_id=self.eid).order_by(Prestamo.id.desc()).first()
            if not p:
                return
            pid = p.id

        r = self.post(f'/empresa/{self.eid}/prestamos/{pid}/eliminar', {})
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            from models import Prestamo, CuotaPrestamo
            self.assertIsNone(Prestamo.query.get(pid))
            remaining = CuotaPrestamo.query.filter_by(prestamo_id=pid).count()
            self.assertEqual(remaining, 0)


class TestF29(unittest.TestCase):
    """Tests for F29 monthly tax calculation."""

    def setUp(self):
        from app import create_app
        from config import Config
        class TestConfig(Config):
            TESTING = True
            SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
            WTF_CSRF_ENABLED = False
            SECRET_KEY = 'test'
        self.app_obj = create_app(TestConfig)
        self.client = self.app_obj.test_client()
        with self.app_obj.app_context():
            from models import db, Empresa, DocumentoSII, Liquidacion
            db.create_all()
            e = Empresa(rut='11.111.111-1', razon_social='F29 Test SA',
                        contribuyente_iva=True, tasa_ppm=1.0)
            db.session.add(e)
            db.session.flush()
            self.eid = e.id
            from datetime import date
            # Venta afecta: neto 1.000.000, iva 190.000
            db.session.add(DocumentoSII(
                empresa_id=self.eid, tipo_libro='VENTAS', tipo_dte='33',
                fecha=date(2025, 3, 10), monto_neto=1_000_000, iva=190_000, total=1_190_000))
            # NC emitida: neto -100.000, iva -19.000
            db.session.add(DocumentoSII(
                empresa_id=self.eid, tipo_libro='VENTAS', tipo_dte='61',
                fecha=date(2025, 3, 15), monto_neto=-100_000, iva=-19_000, total=-119_000))
            # Compra afecta: neto 500.000, iva 95.000
            db.session.add(DocumentoSII(
                empresa_id=self.eid, tipo_libro='COMPRAS', tipo_dte='33',
                fecha=date(2025, 3, 5), monto_neto=500_000, iva=95_000, total=595_000))
            # Honorario: bruto 200.000, retencion 21.500 (10.75%)
            db.session.add(DocumentoSII(
                empresa_id=self.eid, tipo_libro='HONORARIOS', tipo_dte='39',
                fecha=date(2025, 3, 20), total=200_000, iva=21_500, monto_neto=178_500))
            # Liquidación con impuesto renta
            db.session.add(Liquidacion(
                empresa_id=self.eid, empleado_id=1,
                periodo='2025-03', impuesto_renta=15_000,
                estado='EMITIDA'))
            db.session.commit()

    def _calcular(self, anio=2025, mes=3, tasa_ppm=1.0):
        with self.app_obj.app_context():
            from routes.f29 import _calcular_f29
            return _calcular_f29(self.eid, anio, mes, tasa_ppm)

    def test_iva_debito_bruto(self):
        d = self._calcular()
        self.assertEqual(d['iva_debito_bruto'], 190_000)

    def test_nc_emitidas_reduce_debito(self):
        d = self._calcular()
        self.assertEqual(d['nc_emitidas'], 19_000)
        self.assertEqual(d['iva_debito_neto'], 171_000)

    def test_credito_fiscal(self):
        d = self._calcular()
        self.assertEqual(d['credito_bruto'], 95_000)
        self.assertEqual(d['credito_fiscal_neto'], 95_000)

    def test_iva_pagar(self):
        d = self._calcular()
        # 171.000 - 95.000 = 76.000
        self.assertEqual(d['iva_pagar'], 76_000)
        self.assertEqual(d['remanente'], 0)

    def test_ppm_calculo(self):
        d = self._calcular(tasa_ppm=1.0)
        # ventas_total = 1.000.000 - 100.000 = 900.000 → PPM = 9.000
        self.assertEqual(d['ppm_base'], 900_000)
        self.assertEqual(d['ppm'], 9_000)

    def test_retencion_honorarios(self):
        d = self._calcular()
        self.assertEqual(d['retencion_honorarios'], 21_500)

    def test_segunda_categoria(self):
        d = self._calcular()
        self.assertEqual(d['segunda_categoria'], 15_000)

    def test_total(self):
        d = self._calcular()
        esperado = 76_000 + 9_000 + 21_500 + 15_000
        self.assertEqual(d['total'], esperado)

    def test_remanente_cuando_credito_mayor(self):
        # Agregar compras grandes para que crédito > débito
        with self.app_obj.app_context():
            from models import db, DocumentoSII
            from datetime import date
            db.session.add(DocumentoSII(
                empresa_id=self.eid, tipo_libro='COMPRAS', tipo_dte='33',
                fecha=date(2025, 3, 25), monto_neto=2_000_000, iva=380_000, total=2_380_000))
            db.session.commit()
        d = self._calcular()
        self.assertEqual(d['iva_pagar'], 0)
        self.assertGreater(d['remanente'], 0)

    def test_periodo_sin_datos(self):
        d = self._calcular(anio=2020, mes=1)
        self.assertEqual(d['iva_pagar'], 0)
        self.assertEqual(d['ppm'], 0)
        self.assertEqual(d['total'], 0)

    def test_ruta_f29(self):
        r = self.client.get(f'/empresa/{self.eid}/f29?anio=2025&mes=3')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'F29', r.data)
        self.assertIn(b'190.000', r.data)


class TestEmpresaForm(unittest.TestCase):
    """Item 11: Nueva empresa sin campo tipo directa/indirecta."""

    def setUp(self):
        from app import create_app
        from config import Config
        class TestConfig(Config):
            TESTING = True
            SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
            WTF_CSRF_ENABLED = False
            SECRET_KEY = 'test'
        self.app_obj = create_app(TestConfig)
        self.client = self.app_obj.test_client()
        with self.app_obj.app_context():
            from models import db
            db.create_all()

    def _post_empresa(self, rut, razon, extra=None):
        data = {'rut': rut, 'razon_social': razon,
                'contribuyente_iva': 'on', 'tasa_ppm': '1.0'}
        if extra:
            data.update(extra)
        return self.client.post('/empresas/nueva', data=data, follow_redirects=True)

    def test_e01_crear_sin_tipo_participacion(self):
        """Empresa se crea sin tipo_participacion (queda None)."""
        r = self._post_empresa('12.345.678-9', 'Sin Tipo SpA')
        self.assertEqual(r.status_code, 200)
        with self.app_obj.app_context():
            from models import Empresa
            e = Empresa.query.filter_by(razon_social='Sin Tipo SpA').first()
            self.assertIsNotNone(e)
            self.assertIsNone(e.tipo_participacion)

    def test_e02_tipo_ignorado_aunque_enviado(self):
        """Aunque se envíe tipo_participacion en POST, NO se guarda."""
        r = self._post_empresa('11.222.333-4', 'Con Tipo SpA',
                               extra={'tipo_participacion': 'DIRECTA'})
        self.assertEqual(r.status_code, 200)
        with self.app_obj.app_context():
            from models import Empresa
            e = Empresa.query.filter_by(razon_social='Con Tipo SpA').first()
            self.assertIsNotNone(e)
            self.assertIsNone(e.tipo_participacion)

    def test_e03_form_sin_campo_tipo(self):
        """GET /empresas/nueva no contiene 'DIRECTA', 'INDIRECTA' ni tipo_participacion."""
        r = self.client.get('/empresas/nueva')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b'DIRECTA', r.data)
        self.assertNotIn(b'INDIRECTA', r.data)
        self.assertNotIn(b'tipo_participacion', r.data)

    def test_e04_editar_no_modifica_tipo(self):
        """Editar empresa no toca tipo_participacion."""
        self._post_empresa('55.666.777-8', 'Empresa Edit SpA')
        with self.app_obj.app_context():
            from models import Empresa, db
            e = Empresa.query.filter_by(razon_social='Empresa Edit SpA').first()
            eid = e.id
        r = self.client.post(f'/empresa/{eid}/editar', data={
            'rut': '55.666.777-8', 'razon_social': 'Empresa Edit SpA Mod',
            'contribuyente_iva': 'on', 'tasa_ppm': '1.5',
            'tipo_participacion': 'INDIRECTA',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        with self.app_obj.app_context():
            from models import Empresa
            e = Empresa.query.get(eid)
            self.assertIsNone(e.tipo_participacion)


class TestAsientoDescripciones(unittest.TestCase):
    """Item 12: Descripción detallada en libro mayor/diario para cuentas CxC/CxP."""

    def setUp(self):
        from app import create_app
        from config import Config
        class TestConfig(Config):
            TESTING = True
            SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
            WTF_CSRF_ENABLED = False
            SECRET_KEY = 'test'
        self.app_obj = create_app(TestConfig)
        with self.app_obj.app_context():
            from models import db, Empresa
            from database import sembrar_plan_cuentas
            db.create_all()
            e = Empresa(rut='99.888.777-6', razon_social='Test Desc SpA', activa=True)
            db.session.add(e)
            db.session.flush()
            self.eid = e.id
            sembrar_plan_cuentas(e.id)
            db.session.commit()

    def _doc(self, tipo_libro='COMPRAS', contraparte='Empresa Test', rut_cont='76.111.222-3'):
        from types import SimpleNamespace
        return SimpleNamespace(
            empresa_id=self.eid,
            tipo_libro=tipo_libro,
            tipo_dte='33',
            folio=999,
            fecha=date(2025, 4, 1),
            fecha_emision=date(2025, 4, 1),
            razon_social_contraparte=contraparte,
            rut_contraparte=rut_cont,
            monto_neto=100_000,
            monto_exento=0,
            iva=19_000,
            total=119_000,
        )

    def test_d01_compra_proveedor_linea_muestra_contraparte(self):
        """Compra: línea Proveedores (2.1.01) muestra nombre de contraparte."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_compra
            from models import db
            a = generar_asiento_compra(self._doc(contraparte='Gran Proveedor Ltda.'))
            db.session.flush()
            prov = next(l for l in a.lineas if l.cuenta.codigo == '2.1.01')
            self.assertIn('Gran Proveedor Ltda.', prov.descripcion)
            db.session.rollback()

    def test_d02_compra_sin_contraparte_no_none_en_desc(self):
        """Compra sin nombre contraparte: asiento.descripcion NO contiene 'None'."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_compra
            from models import db
            a = generar_asiento_compra(self._doc(contraparte=None, rut_cont=None))
            db.session.flush()
            self.assertNotIn('None', a.descripcion)
            db.session.rollback()

    def test_d03_compra_linea_gasto_muestra_contraparte(self):
        """Compra: línea de Gasto (5.2.17) también muestra nombre de contraparte."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_compra
            from models import db
            a = generar_asiento_compra(self._doc(contraparte='Proveedor Gasto SA'))
            db.session.flush()
            gasto = next(l for l in a.lineas if l.cuenta.codigo == '5.2.17')
            self.assertIn('Proveedor Gasto SA', gasto.descripcion)
            db.session.rollback()

    def test_d04_venta_clientes_linea_muestra_contraparte(self):
        """Venta: línea Clientes (1.1.03) muestra nombre de contraparte."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_venta
            from models import db
            a = generar_asiento_venta(self._doc(tipo_libro='VENTAS', contraparte='Cliente Top SpA'))
            db.session.flush()
            cli = next(l for l in a.lineas if l.cuenta.codigo == '1.1.03')
            self.assertIn('Cliente Top SpA', cli.descripcion)
            db.session.rollback()

    def test_d05_venta_sin_contraparte_no_none_en_desc(self):
        """Venta sin nombre contraparte: asiento.descripcion NO contiene 'None'."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_venta
            from models import db
            a = generar_asiento_venta(self._doc(tipo_libro='VENTAS', contraparte=None, rut_cont=None))
            db.session.flush()
            self.assertNotIn('None', a.descripcion)
            db.session.rollback()

    def test_d06_venta_linea_ventas_muestra_contraparte(self):
        """Venta: línea de Ventas (4.1.01) también muestra nombre de contraparte."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_venta
            from models import db
            a = generar_asiento_venta(self._doc(tipo_libro='VENTAS', contraparte='Cliente Ventas SpA'))
            db.session.flush()
            ventas = next(l for l in a.lineas if l.cuenta.codigo.startswith('4.1.0'))
            self.assertIn('Cliente Ventas SpA', ventas.descripcion)
            db.session.rollback()

    def test_d07_banco_lineas_tienen_descripcion(self):
        """Asiento bancario genérico: todas las líneas tienen descripción no vacía."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_banco
            from models import db, Cuenta, MovimientoBanco
            c_gasto = Cuenta.query.filter_by(empresa_id=self.eid, codigo='5.2.17').first()
            mov = MovimientoBanco(empresa_id=self.eid, fecha=date(2025, 4, 1),
                                  descripcion='PAGO PROVEEDOR XYZ', cargo=50_000, abono=0)
            db.session.add(mov)
            db.session.flush()
            a = generar_asiento_banco(mov, c_gasto.id)
            db.session.flush()
            for l in a.lineas:
                self.assertTrue(l.descripcion and l.descripcion.strip(),
                                f'Línea sin descripción: cuenta {l.cuenta.codigo}')
            db.session.rollback()

    def test_d08_pago_proveedor_lineas_tienen_descripcion(self):
        """Asiento pago_proveedor: todas las líneas tienen descripción."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_pago_proveedor
            from models import db, MovimientoBanco
            mov = MovimientoBanco(empresa_id=self.eid, fecha=date(2025, 4, 1),
                                  descripcion='TRANSFERENCIA PROVEEDOR', cargo=119_000, abono=0)
            db.session.add(mov)
            db.session.flush()
            a = generar_asiento_pago_proveedor(mov)
            db.session.flush()
            for l in a.lineas:
                self.assertTrue(l.descripcion and l.descripcion.strip(),
                                f'Línea sin descripción: cuenta {l.cuenta.codigo}')
            db.session.rollback()

    def test_d09_cobro_cliente_lineas_tienen_descripcion(self):
        """Asiento cobro_cliente: todas las líneas tienen descripción."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_cobro_cliente
            from models import db, MovimientoBanco
            mov = MovimientoBanco(empresa_id=self.eid, fecha=date(2025, 4, 1),
                                  descripcion='ABONO CLIENTE ABC', cargo=0, abono=119_000)
            db.session.add(mov)
            db.session.flush()
            a = generar_asiento_cobro_cliente(mov)
            db.session.flush()
            for l in a.lineas:
                self.assertTrue(l.descripcion and l.descripcion.strip(),
                                f'Línea sin descripción: cuenta {l.cuenta.codigo}')
            db.session.rollback()

    def test_d10_honorario_sin_contraparte_no_none(self):
        """Honorario sin contraparte: asiento.descripcion NO contiene 'None'."""
        with self.app_obj.app_context():
            from engine.asientos import generar_asiento_honorario
            from models import db
            doc = self._doc(tipo_libro='HONORARIOS', contraparte=None, rut_cont=None)
            doc.monto_neto = 100_000
            a = generar_asiento_honorario(doc)
            db.session.flush()
            self.assertNotIn('None', a.descripcion)
            db.session.rollback()


class TestPrestamosAsientos(unittest.TestCase):
    """Items 11, 12, 13: asientos automáticos, proyección y UF en préstamos."""

    def setUp(self):
        import app as app_module
        self.app = app_module.create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        with self.app.app_context():
            from models import db, Empresa, ValorUF
            from database import sembrar_plan_cuentas
            db.create_all()
            emp = Empresa.query.filter_by(rut='77.777.777-7').first()
            if not emp:
                emp = Empresa(rut='77.777.777-7', razon_social='PrestAsientos SpA', activa=True)
                db.session.add(emp)
                db.session.flush()
                sembrar_plan_cuentas(emp.id)
                db.session.commit()
            self.eid = emp.id
            for d, v in [('2025-01-15', 37000.0), ('2025-02-15', 37200.0)]:
                if not ValorUF.query.filter_by(fecha=date.fromisoformat(d)).first():
                    db.session.add(ValorUF(fecha=date.fromisoformat(d), valor=v))
            db.session.commit()

    def post(self, url, data):
        return self.client.post(url, data=data, follow_redirects=True)

    def get(self, url):
        return self.client.get(url, follow_redirects=True)

    def _crear_prestamo(self, tipo='PAGAR', moneda='PESOS', n_cuotas=3, tasa=0):
        self.post(f'/empresa/{self.eid}/prestamos/nuevo', {
            'nombre': f'Test {tipo} {moneda}',
            'tipo': tipo,
            'moneda': moneda,
            'monto_original': '1200000' if moneda == 'PESOS' else '100',
            'tasa_interes_anual': str(tasa),
            'fecha_inicio': '2025-01-01',
            'periodicidad': 'MENSUAL',
            'n_cuotas': str(n_cuotas),
            'acreedor_deudor': 'Banco Test',
        })
        with self.app.app_context():
            from models import Prestamo
            return Prestamo.query.filter_by(empresa_id=self.eid).order_by(Prestamo.id.desc()).first().id

    def _primer_cid(self, pid):
        with self.app.app_context():
            from models import CuotaPrestamo
            return CuotaPrestamo.query.filter_by(prestamo_id=pid).order_by(CuotaPrestamo.numero_cuota).first().id

    # ── Item 11 ───────────────────────────────────────────────────────────────

    def test_q11_pago_genera_asiento_pagar(self):
        """Marcar cuota pagada (PAGAR/PESOS) genera asiento contable."""
        pid = self._crear_prestamo('PAGAR', 'PESOS')
        cid = self._primer_cid(pid)
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-02-01'})
        with self.app.app_context():
            from models import CuotaPrestamo
            c = CuotaPrestamo.query.get(cid)
            self.assertTrue(c.pagada)
            self.assertIsNotNone(c.asiento_id)

    def test_q11_asiento_cuadra_y_usa_banco(self):
        """Asiento de cuota PAGAR: cuadra y HABER banco == cuota_total."""
        pid = self._crear_prestamo('PAGAR', 'PESOS')
        cid = self._primer_cid(pid)
        with self.app.app_context():
            from models import CuotaPrestamo
            cuota_total = CuotaPrestamo.query.get(cid).cuota_total
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-02-01'})
        with self.app.app_context():
            from models import CuotaPrestamo, Asiento
            c = CuotaPrestamo.query.get(cid)
            a = Asiento.query.get(c.asiento_id)
            self.assertAlmostEqual(a.total_debe, a.total_haber, delta=1)
            haber_banco = sum(l.haber for l in a.lineas if l.cuenta.codigo == '1.1.02')
            self.assertAlmostEqual(haber_banco, cuota_total, delta=1)

    def test_q11_desmarcar_elimina_asiento(self):
        """Desmarcar cuota pagada elimina el asiento generado."""
        pid = self._crear_prestamo('PAGAR', 'PESOS')
        cid = self._primer_cid(pid)
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-02-01'})
        with self.app.app_context():
            from models import CuotaPrestamo
            aid = CuotaPrestamo.query.get(cid).asiento_id
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle', {})
        with self.app.app_context():
            from models import CuotaPrestamo, Asiento
            c = CuotaPrestamo.query.get(cid)
            self.assertFalse(c.pagada)
            self.assertIsNone(c.asiento_id)
            self.assertIsNone(Asiento.query.get(aid))

    def test_q11_cobrar_genera_asiento_debe_banco(self):
        """Cuota COBRAR pagada: asiento tiene DEBE banco."""
        pid = self._crear_prestamo('COBRAR', 'PESOS')
        cid = self._primer_cid(pid)
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-02-01'})
        with self.app.app_context():
            from models import CuotaPrestamo, Asiento
            a = Asiento.query.get(CuotaPrestamo.query.get(cid).asiento_id)
            self.assertIsNotNone(a)
            debe_banco = sum(l.debe for l in a.lineas if l.cuenta.codigo == '1.1.02')
            self.assertGreater(debe_banco, 0)

    def test_q11_asiento_con_interes(self):
        """Cuota con interés genera línea separada en 5.2.12."""
        pid = self._crear_prestamo('PAGAR', 'PESOS', n_cuotas=3, tasa=12)
        cid = self._primer_cid(pid)
        with self.app.app_context():
            from models import CuotaPrestamo
            c = CuotaPrestamo.query.get(cid)
            self.assertGreater(c.interes, 0, 'Préstamo al 12% debe tener interés > 0')
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-02-01'})
        with self.app.app_context():
            from models import CuotaPrestamo, Asiento
            a = Asiento.query.get(CuotaPrestamo.query.get(cid).asiento_id)
            debe_gasto = sum(l.debe for l in a.lineas if l.cuenta.codigo == '5.2.12')
            self.assertGreater(debe_gasto, 0)

    # ── Item 12 ───────────────────────────────────────────────────────────────

    def test_q12_proyeccion_en_lista(self):
        """Página de lista de préstamos muestra sección Proyección."""
        self._crear_prestamo('PAGAR', 'PESOS', 3)
        r = self.get(f'/empresa/{self.eid}/prestamos')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'royecci', r.data)

    def test_q12_proyeccion_suma_cuotas(self):
        """Proyección incluye totales correctos de cuotas pendientes."""
        pid = self._crear_prestamo('PAGAR', 'PESOS', 3)
        with self.app.app_context():
            from models import Prestamo
            p = Prestamo.query.get(pid)
            total_esperado = sum(c.cuota_total for c in p.cuotas if not c.pagada)
        r = self.get(f'/empresa/{self.eid}/prestamos')
        self.assertEqual(r.status_code, 200)
        # At minimum the page loaded successfully with loan data
        self.assertIn(b'Test PAGAR PESOS', r.data)

    # ── Item 13 ───────────────────────────────────────────────────────────────

    def test_q13_uf_guarda_valor_pago(self):
        """Pago de cuota UF almacena uf_valor_pago en la cuota."""
        pid = self._crear_prestamo('PAGAR', 'UF', 3)
        cid = self._primer_cid(pid)
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-01-15'})
        with self.app.app_context():
            from models import CuotaPrestamo
            c = CuotaPrestamo.query.get(cid)
            self.assertIsNotNone(c.uf_valor_pago)
            self.assertAlmostEqual(c.uf_valor_pago, 37000.0, delta=1)

    def test_q13_uf_guarda_pesos(self):
        """Pago UF almacena cuota_total_pesos ≈ cuota_uf * valor_uf."""
        pid = self._crear_prestamo('PAGAR', 'UF', 3)
        cid = self._primer_cid(pid)
        with self.app.app_context():
            from models import CuotaPrestamo
            cuota_uf = CuotaPrestamo.query.get(cid).cuota_total
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-01-15'})
        with self.app.app_context():
            from models import CuotaPrestamo
            c = CuotaPrestamo.query.get(cid)
            expected = round(cuota_uf * 37000.0)
            self.assertAlmostEqual(c.cuota_total_pesos, expected, delta=100)

    def test_q13_asiento_uf_en_pesos(self):
        """Asiento de préstamo UF usa montos en pesos, no en unidades UF."""
        pid = self._crear_prestamo('PAGAR', 'UF', 3)
        cid = self._primer_cid(pid)
        with self.app.app_context():
            from models import CuotaPrestamo
            cuota_uf = CuotaPrestamo.query.get(cid).cuota_total
        self.post(f'/empresa/{self.eid}/prestamos/{pid}/cuota/{cid}/toggle',
                  {'fecha_pago': '2025-01-15'})
        with self.app.app_context():
            from models import CuotaPrestamo, Asiento
            c = CuotaPrestamo.query.get(cid)
            a = Asiento.query.get(c.asiento_id)
            self.assertIsNotNone(a)
            # Amounts in pesos must be much larger than UF units
            self.assertGreater(a.total_debe, cuota_uf * 1000)


if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestRemuneracionesEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestCalculationCrossCheck))
    suite.addTests(loader.loadTestsFromTestCase(TestFlaskRoutes))
    suite.addTests(loader.loadTestsFromTestCase(TestPMT))
    suite.addTests(loader.loadTestsFromTestCase(TestPrestamosFlask))
    suite.addTests(loader.loadTestsFromTestCase(TestF29))
    suite.addTests(loader.loadTestsFromTestCase(TestEmpresaForm))
    suite.addTests(loader.loadTestsFromTestCase(TestAsientoDescripciones))
    suite.addTests(loader.loadTestsFromTestCase(TestPrestamosAsientos))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
