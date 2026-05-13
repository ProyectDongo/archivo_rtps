"""
Tests básicos del módulo taller. Cubren los puntos críticos:

  - Disponibilidad de slots (laboral / fin de semana / feriado / hoy)
  - Anti-bot (blocklist de emails desechables, código de verificación)
  - Tokens públicos (hash determinista, comparación)
  - Constraint UNIQUE de slot activo (no doble-booking)
  - Vista pública /agendar/ devuelve 200
  - Panel admin /agenda/ requiere staff + permiso

No cubre el flujo end-to-end de envío de emails (eso necesita SMTP mockeado
— V2 si querés). Para correr:

    python manage.py test taller
"""
from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import anti_bot
from .disposable_domains import es_email_desechable
from .models import (
    BloqueoCalendario,
    ItemCatalogo,
    Reserva,
    generar_token_publico,
    hash_token,
)
from .utils import es_dia_laboral, slots_de_la_fecha


# ─── Helpers ───────────────────────────────────────────────────────────────
def _lunes_proximo() -> date:
    """Próximo lunes (para tener un día laboral consistente en los tests)."""
    hoy = timezone.localdate()
    dias = (0 - hoy.weekday()) % 7 or 7   # nunca hoy, siempre futuro
    return hoy + timedelta(days=dias)


# ─── Disposable email blocklist ────────────────────────────────────────────
class DisposableDomainsTest(TestCase):
    def test_dominios_conocidos_bloqueados(self):
        for email in ['x@mailinator.com', 'y@10minutemail.com',
                      'z@guerrillamail.com', 'w@yopmail.com']:
            with self.subTest(email=email):
                self.assertTrue(es_email_desechable(email),
                                f'{email} debería estar en blocklist')

    def test_dominios_legitimos_pasan(self):
        for email in ['x@gmail.com', 'y@hotmail.com',
                      'z@pietramonte.cl', 'persona@empresa.com']:
            with self.subTest(email=email):
                self.assertFalse(es_email_desechable(email))

    def test_extras_runtime(self):
        extra = frozenset({'spam.example.com'})
        self.assertTrue(es_email_desechable('x@spam.example.com', extra=extra))
        self.assertFalse(es_email_desechable('x@spam.example.com'))   # sin extra


# ─── Email verification code (anti_bot) ────────────────────────────────────
class CodigoEmailTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_generar_y_verificar_ok(self):
        code = anti_bot.generar_codigo_email('foo@bar.com')
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertTrue(anti_bot.verificar_codigo_email('foo@bar.com', code))

    def test_codigo_es_un_solo_uso(self):
        code = anti_bot.generar_codigo_email('foo@bar.com')
        self.assertTrue(anti_bot.verificar_codigo_email('foo@bar.com', code))
        # Segundo intento con el mismo código debe fallar
        self.assertFalse(anti_bot.verificar_codigo_email('foo@bar.com', code))

    def test_codigo_es_case_insensitive_email(self):
        code = anti_bot.generar_codigo_email('Foo@Bar.com')
        self.assertTrue(anti_bot.verificar_codigo_email('foo@bar.com', code))

    def test_codigo_ajeno_falla(self):
        anti_bot.generar_codigo_email('foo@bar.com')
        self.assertFalse(anti_bot.verificar_codigo_email('foo@bar.com', '000000'))


# ─── Tokens públicos ───────────────────────────────────────────────────────
class TokenPublicoTest(TestCase):
    def test_token_es_unico(self):
        a, b, c = (generar_token_publico() for _ in range(3))
        self.assertEqual(len({a, b, c}), 3)
        self.assertTrue(len(a) >= 40)   # 32 bytes urlsafe = 43 chars

    def test_hash_es_determinista(self):
        t = generar_token_publico()
        self.assertEqual(hash_token(t), hash_token(t))

    def test_hash_no_es_invertible(self):
        t = generar_token_publico()
        h = hash_token(t)
        self.assertNotEqual(t, h)
        self.assertEqual(len(h), 64)    # SHA-256 hex


# ─── Disponibilidad de slots ───────────────────────────────────────────────
class SlotsTest(TestCase):
    def test_finde_no_es_laboral(self):
        # 2026-05-09 sábado
        sabado  = date(2026, 5, 9)
        domingo = date(2026, 5, 10)
        for d in (sabado, domingo):
            laboral, _ = es_dia_laboral(d)
            self.assertFalse(laboral, f'{d} ({d.strftime("%A")}) no debería ser laboral')

    def test_feriado_bloquea_dia(self):
        d = _lunes_proximo()
        BloqueoCalendario.objects.create(fecha=d, motivo='Test feriado', activo=True)
        laboral, motivo = es_dia_laboral(d)
        self.assertFalse(laboral)
        self.assertIn('Test feriado', motivo)

    def test_dia_laboral_tiene_slots(self):
        d = _lunes_proximo()
        slots = slots_de_la_fecha(d)
        self.assertTrue(len(slots) > 0, 'Un lunes debería tener slots')
        # No debe haber slot a las 13:00 (almuerzo)
        horas = {s['hora'] for s in slots}
        self.assertNotIn('13:00', horas)
        self.assertNotIn('13:30', horas)
        # 9:00 sí (apertura)
        self.assertIn('09:00', horas)

    def test_reserva_activa_ocupa_slot(self):
        d = _lunes_proximo()
        Reserva.objects.create(
            token_hash=hash_token(generar_token_publico()),
            cliente_nombre='Test', cliente_email='t@example.com',
            cliente_telefono='+56912345678',
            patente='ABCD12', marca='Toyota', modelo='Hilux',
            fecha=d, hora_inicio=time(9, 0),
            estado=Reserva.Estado.CONFIRMADA_EMAIL,
        )
        slots = slots_de_la_fecha(d)
        s9 = next(s for s in slots if s['hora'] == '09:00')
        self.assertFalse(s9['disponible'], 'El slot de las 9 debería estar ocupado')

    def test_reserva_cancelada_libera_slot(self):
        d = _lunes_proximo()
        Reserva.objects.create(
            token_hash=hash_token(generar_token_publico()),
            cliente_nombre='Test', cliente_email='t@example.com',
            cliente_telefono='+56912345678',
            patente='ABCD12', marca='Toyota', modelo='Hilux',
            fecha=d, hora_inicio=time(9, 0),
            estado=Reserva.Estado.CANCELADA_CLIENTE,
        )
        slots = slots_de_la_fecha(d)
        s9 = next(s for s in slots if s['hora'] == '09:00')
        self.assertTrue(s9['disponible'], 'Slot de reserva cancelada debe estar libre')


# ─── Vistas públicas ───────────────────────────────────────────────────────
class VistasPublicasTest(TestCase):
    def setUp(self):
        ItemCatalogo.objects.create(
            nombre='Cambio de aceite', tipo='servicio', categoria='mantencion',
            precio_referencia_clp=35000, duracion_min=30, activo=True,
        )

    def test_agendar_responde_200(self):
        resp = self.client.get(reverse('agendar'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Cambio de aceite')

    def test_disponibilidad_devuelve_json(self):
        d = _lunes_proximo()
        resp = self.client.get(reverse('disponibilidad'), {'fecha': d.strftime('%Y-%m-%d')})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/json')
        data = resp.json()
        self.assertTrue(data['laboral'])
        self.assertTrue(len(data['slots']) > 0)

    def test_disponibilidad_finde_devuelve_no_laboral(self):
        sabado = date(2026, 5, 9)
        resp = self.client.get(reverse('disponibilidad'), {'fecha': '2026-05-09'})
        data = resp.json()
        self.assertFalse(data['laboral'])
        self.assertEqual(data['slots'], [])


# ─── Panel admin: requiere staff + permiso ────────────────────────────────
class PanelAdminTest(TestCase):
    def setUp(self):
        self.user_normal = User.objects.create_user('normal', password='x')
        self.user_staff  = User.objects.create_user('staff',  password='x', is_staff=True)
        self.user_super  = User.objects.create_superuser('super', password='x')

        # Asignar permiso al staff (como lo haría setup_grupos_taller)
        self.user_staff.user_permissions.add(
            Permission.objects.get(codename='view_reserva', content_type__app_label='taller'),
        )

    def test_no_logueado_redirige(self):
        resp = self.client.get('/admin-pm-staff/agenda/')
        # staff_member_required redirige a login (302)
        self.assertEqual(resp.status_code, 302)

    def test_user_no_staff_redirige(self):
        self.client.login(username='normal', password='x')
        resp = self.client.get('/admin-pm-staff/agenda/')
        self.assertEqual(resp.status_code, 302)

    def test_staff_con_permiso_pasa(self):
        # Marcamos también la sesión como 2FA-OK para bypass del middleware
        self.client.login(username='staff', password='x')
        session = self.client.session
        session['admin_2fa_ok'] = True
        session.save()
        resp = self.client.get('/admin-pm-staff/agenda/')
        self.assertEqual(resp.status_code, 200)


# ─── Constraint de slot único ─────────────────────────────────────────────
class SlotUniqueTest(TestCase):
    def test_dos_reservas_activas_mismo_slot_falla(self):
        from django.db import IntegrityError
        d = _lunes_proximo()
        Reserva.objects.create(
            token_hash=hash_token(generar_token_publico()),
            cliente_nombre='A', cliente_email='a@x.com', cliente_telefono='+56911111111',
            patente='AAAA11', marca='X', modelo='Y',
            fecha=d, hora_inicio=time(10, 0),
            estado=Reserva.Estado.CONFIRMADA_EMAIL,
        )
        with self.assertRaises(IntegrityError):
            Reserva.objects.create(
                token_hash=hash_token(generar_token_publico()),
                cliente_nombre='B', cliente_email='b@x.com', cliente_telefono='+56922222222',
                patente='BBBB22', marca='X', modelo='Y',
                fecha=d, hora_inicio=time(10, 0),
                estado=Reserva.Estado.PENDIENTE_EMAIL,
            )

    def test_reserva_cancelada_libera_slot_para_nueva(self):
        d = _lunes_proximo()
        Reserva.objects.create(
            token_hash=hash_token(generar_token_publico()),
            cliente_nombre='A', cliente_email='a@x.com', cliente_telefono='+56911111111',
            patente='AAAA11', marca='X', modelo='Y',
            fecha=d, hora_inicio=time(10, 0),
            estado=Reserva.Estado.CANCELADA_CLIENTE,
        )
        # No debería levantar excepción
        Reserva.objects.create(
            token_hash=hash_token(generar_token_publico()),
            cliente_nombre='B', cliente_email='b@x.com', cliente_telefono='+56922222222',
            patente='BBBB22', marca='X', modelo='Y',
            fecha=d, hora_inicio=time(10, 0),
            estado=Reserva.Estado.CONFIRMADA_EMAIL,
        )
        self.assertEqual(Reserva.objects.filter(fecha=d, hora_inicio=time(10, 0)).count(), 2)
