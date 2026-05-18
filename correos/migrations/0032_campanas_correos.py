"""
Migración 0032: campañas de correos automáticos.

Crea ListaDestinatarios, ContactoLista, CampanaCorreo, EnvioCampana y
agrega el campo `puede_campanas` a UsuarioPortal.
"""
import datetime
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0031_pg_trgm_busqueda'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuarioportal',
            name='puede_campanas',
            field=models.BooleanField(
                default=False,
                help_text='Permite crear y gestionar campañas de correos automáticos. Los admins siempre pueden.',
            ),
        ),
        migrations.CreateModel(
            name='ListaDestinatarios',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=120)),
                ('descripcion', models.TextField(blank=True, default='')),
                ('activa', models.BooleanField(default=True)),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
                ('buzon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='listas', to='correos.buzon')),
                ('creada_por', models.ForeignKey(blank=True, null=True,
                                                 on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='listas_creadas', to='correos.usuarioportal')),
            ],
            options={
                'verbose_name': 'Lista de destinatarios',
                'verbose_name_plural': 'Listas de destinatarios',
                'ordering': ['nombre'],
                'unique_together': {('buzon', 'nombre')},
                'indexes': [models.Index(fields=['buzon', 'activa'], name='correos_lista_buz_act_idx')],
            },
        ),
        migrations.CreateModel(
            name='ContactoLista',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254)),
                ('nombre', models.CharField(blank=True, default='', max_length=120)),
                ('datos_extra', models.JSONField(blank=True, default=dict,
                                                 help_text='Variables custom para mail merge: {"empresa": "X", ...}')),
                ('activo', models.BooleanField(default=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('lista', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='contactos', to='correos.listadestinatarios')),
            ],
            options={
                'verbose_name': 'Contacto',
                'verbose_name_plural': 'Contactos',
                'ordering': ['email'],
                'unique_together': {('lista', 'email')},
                'indexes': [models.Index(fields=['lista', 'activo'], name='correos_ctc_lista_act_idx')],
            },
        ),
        migrations.CreateModel(
            name='CampanaCorreo',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(help_text='Solo interno, no se muestra al destinatario.', max_length=160)),
                ('asunto', models.CharField(help_text='Admite variables: {{ nombre }}, {{ extra.empresa }}.', max_length=300)),
                ('cuerpo_html', models.TextField(
                    help_text='Cuerpo del email (HTML). Admite variables tipo {{ nombre }}. La firma '
                              'del buzón y el logo se agregan automáticamente.')),
                ('emails_extra', models.TextField(
                    blank=True, default='',
                    help_text='Emails sueltos coma-separados o uno por línea. Se suman a las listas.')),
                ('dias_del_mes', models.JSONField(
                    blank=True, default=list,
                    help_text='Lista de días del mes en que se envía: [1, 15] o [30] o [1, 10, 20, 30].')),
                ('hora_envio', models.TimeField(
                    default=datetime.time(9, 0),
                    help_text='Hora local Chile en que arranca el envío. El cron debe correr cerca de esta hora.')),
                ('activa', models.BooleanField(
                    default=True,
                    help_text='Si está apagada, el cron no la envía pero podés editarla.')),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
                ('modificada_en', models.DateTimeField(auto_now=True)),
                ('buzon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='campanas', to='correos.buzon')),
                ('creada_por', models.ForeignKey(blank=True, null=True,
                                                 on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='campanas_creadas', to='correos.usuarioportal')),
                ('listas', models.ManyToManyField(blank=True, related_name='campanas',
                                                  to='correos.listadestinatarios')),
            ],
            options={
                'verbose_name': 'Campaña de correos',
                'verbose_name_plural': 'Campañas de correos',
                'ordering': ['-creada_en'],
                'indexes': [
                    models.Index(fields=['buzon', 'activa'], name='correos_camp_buz_act_idx'),
                    models.Index(fields=['activa'], name='correos_camp_act_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='EnvioCampana',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha', models.DateField(help_text='Fecha de envío (fecha local del cron).')),
                ('email', models.EmailField(max_length=254)),
                ('nombre', models.CharField(blank=True, default='', max_length=120)),
                ('estado', models.CharField(choices=[('ok', 'OK'), ('error', 'Error')], max_length=10)),
                ('error_msg', models.TextField(blank=True, default='')),
                ('enviado_en', models.DateTimeField(auto_now_add=True)),
                ('campana', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='envios', to='correos.campanacorreo')),
            ],
            options={
                'verbose_name': 'Envío de campaña',
                'verbose_name_plural': 'Envíos de campañas',
                'ordering': ['-enviado_en'],
                'unique_together': {('campana', 'fecha', 'email')},
                'indexes': [
                    models.Index(fields=['campana', '-fecha'], name='correos_env_camp_fec_idx'),
                    models.Index(fields=['campana', 'estado'], name='correos_env_camp_est_idx'),
                ],
            },
        ),
    ]
