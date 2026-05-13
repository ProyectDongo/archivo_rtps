"""
Migración 2FA:
  - Campos TOTP en UsuarioPortal.
  - Nuevos motivos en IntentoLogin.
  - Modelo nuevo AdminTOTP (1:1 con auth.User).

Compatible con Postgres (prod) y SQLite (dev).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('correos', '0005_correo_destacado_correo_notas_etiqueta_and_more'),
    ]

    operations = [
        # ─── UsuarioPortal: campos 2FA ───────────────────────────────────────
        migrations.AddField(
            model_name='usuarioportal',
            name='totp_secret',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='usuarioportal',
            name='totp_activo',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='usuarioportal',
            name='recovery_codes_hash',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='usuarioportal',
            name='totp_ultimo_codigo',
            field=models.CharField(blank=True, default='', max_length=10),
        ),

        # ─── IntentoLogin: ampliar choices de motivo ─────────────────────────
        migrations.AlterField(
            model_name='intentologin',
            name='motivo',
            field=models.CharField(
                blank=True,
                choices=[
                    ('exito',             'Login exitoso'),
                    ('honeypot',          'Honeypot lleno'),
                    ('muy_rapido',        'Tiempo sospechosamente bajo'),
                    ('captcha_fail',      'Captcha incorrecto/expirado'),
                    ('email_no_lista',    'Email fuera de allowlist'),
                    ('email_invalido',    'Formato de email inválido'),
                    ('password_invalida', 'Contraseña incorrecta'),
                    ('usuario_inactivo',  'Usuario marcado inactivo'),
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
                max_length=20,
            ),
        ),

        # ─── AdminTOTP: nuevo modelo ─────────────────────────────────────────
        migrations.CreateModel(
            name='AdminTOTP',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('totp_secret', models.CharField(blank=True, default='', max_length=64)),
                ('totp_activo', models.BooleanField(default=False)),
                ('recovery_codes_hash', models.JSONField(blank=True, default=list)),
                ('totp_ultimo_codigo', models.CharField(blank=True, default='', max_length=10)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('ultima_2fa_ok', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='totp',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': '2FA de admin',
                'verbose_name_plural': '2FA de admins',
            },
        ),
    ]
