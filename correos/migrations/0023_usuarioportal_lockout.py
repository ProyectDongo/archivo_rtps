"""
0023 — Account lockout per-usuario (anti brute-force).

Suma 3 fields a UsuarioPortal:
  - intentos_fallidos: contador (reset en login OK).
  - bloqueado_hasta: timestamp futuro durante el cual el usuario no puede
    loguear. Index db para query rápida de "bloqueados ahora".
  - ultimo_intento_fallido: timestamp del último fallo, para auditoría.

Defensa que el rate-limit por IP no cubre: atacante con botnet rotando
IPs puede iterar passwords de un mismo email sin que ninguna IP exceda
threshold. Ahora 5 fallos en una cuenta → bloqueo por 30 min, sin
importar de qué IPs vino.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0022_correo_unique_buzon_msgid'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuarioportal',
            name='intentos_fallidos',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Contador de logins fallidos consecutivos. Se resetea en login OK.',
            ),
        ),
        migrations.AddField(
            model_name='usuarioportal',
            name='bloqueado_hasta',
            field=models.DateTimeField(
                null=True, blank=True, db_index=True,
                help_text='Si está en el futuro, el usuario no puede loguear hasta esa fecha. '
                          'Se setea automáticamente tras LOCKOUT_THRESHOLD fallos consecutivos.',
            ),
        ),
        migrations.AddField(
            model_name='usuarioportal',
            name='ultimo_intento_fallido',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Timestamp del último intento fallido — útil para auditoría.',
            ),
        ),
        # Sumamos el nuevo motivo a IntentoLogin.MOTIVOS choices.
        migrations.AlterField(
            model_name='intentologin',
            name='motivo',
            field=models.CharField(
                blank=True, max_length=20,
                choices=[
                    ('exito',             'Login exitoso'),
                    ('honeypot',          'Honeypot lleno'),
                    ('muy_rapido',        'Tiempo sospechosamente bajo'),
                    ('captcha_fail',      'Captcha incorrecto/expirado'),
                    ('email_no_lista',    'Email fuera de allowlist'),
                    ('email_invalido',    'Formato de email inválido'),
                    ('password_invalida', 'Contraseña incorrecta'),
                    ('usuario_inactivo',  'Usuario marcado inactivo'),
                    ('usuario_bloqueado', 'Cuenta bloqueada por brute-force lockout'),
                    ('buzon_inexist',     'Buzón no importado'),
                    ('throttled',         'Bloqueado por rate-limit'),
                    ('csrf',              'CSRF inválido'),
                    ('pwd_ok_2fa_pend',   'Password OK, 2FA pendiente'),
                    ('totp_fail',         'Código 2FA incorrecto'),
                    ('totp_ok',           '2FA verificado'),
                    ('recovery_used',     'Recovery code usado'),
                    ('recovery_inval',    'Recovery code inválido'),
                    ('totp_setup',        '2FA configurado por primera vez'),
                    ('totp_reset',        '2FA reseteado por admin'),
                ],
            ),
        ),
    ]
