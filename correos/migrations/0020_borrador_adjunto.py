import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0019_eventoauditoria'),
    ]

    operations = [
        migrations.CreateModel(
            name='BorradorAdjunto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre_original', models.CharField(max_length=500)),
                ('mime_type', models.CharField(default='application/octet-stream', max_length=200)),
                ('archivo', models.FileField(upload_to='borradores/%Y/%m/')),
                ('tamanio', models.PositiveIntegerField(default=0)),
                ('subido', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Adjunto de borrador',
                'verbose_name_plural': 'Adjuntos de borradores',
                'ordering': ['subido'],
            },
        ),
        migrations.AddField(
            model_name='borradoradjunto',
            name='borrador',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='adjuntos_borrador', to='correos.borradorcorreo'),
        ),
    ]
