# Deploy en Hetzner + Coolify + Cloudflare Tunnel

Guía paso a paso para llevar el archivo a producción.

**Servidor target:** Hetzner CPX21 (3 vCPU / 4 GB RAM / 80 GB disco) con Ubuntu 24.04.
**Acceso público:** Cloudflare Tunnel (cero puertos abiertos al internet).
**Stack:** Docker Compose orquestado por Coolify.

> **Antes de empezar:** Ten a mano la IP del servidor, tus claves SSH, y acceso al panel de Cloudflare donde está `pietramonte.cl`.

---

## 1. Preparar el servidor (15 min)

### 1.1. Conectarte
```bash
ssh root@<IP-DEL-SERVIDOR>
```

### 1.2. Crear usuario no-root
```bash
adduser pietra
usermod -aG sudo pietra
mkdir -p /home/pietra/.ssh
cp ~/.ssh/authorized_keys /home/pietra/.ssh/
chown -R pietra:pietra /home/pietra/.ssh
chmod 700 /home/pietra/.ssh
chmod 600 /home/pietra/.ssh/authorized_keys
```

Salí (`exit`) y reconectate como `pietra`.

### 1.3. Hardening básico de SSH
Edita `/etc/ssh/sshd_config`:
```
PermitRootLogin no
PasswordAuthentication no
```
Recarga: `sudo systemctl restart ssh`.

### 1.4. UFW (firewall)
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw enable
```
Verifica: `sudo ufw status`. **Solo SSH (22) debe estar abierto**. El Tunnel sale, no entra.

### 1.5. Swap (importante con 4 GB RAM)
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 1.6. Actualizar
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw
```

---

## 2. Instalar Docker (5 min)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker run hello-world   # debe imprimir "Hello from Docker!"
```

---

## 3. Instalar Coolify (10 min)

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

Al final imprime una URL tipo `http://<IP>:8000`. Para acceder:
- Si NO tienes Tunnel todavía → crea uno provisorio (siguiente paso) o expón temporalmente `8000` sólo a tu IP.
- Si SÍ tienes Tunnel → crea ahora una regla para el panel de Coolify.

> ⚠️ **No expongas el panel de Coolify (puerto 8000) al internet sin protección.** Es el control total del servidor.

Crea cuenta de admin de Coolify (te lo pide al primer login).

---

## 4. Instalar y configurar Cloudflare Tunnel (10 min)

```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login         # abre URL, autorizas en navegador
cloudflared tunnel create archivo-pietramonte
```

Apunta el dominio en Cloudflare:
```bash
cloudflared tunnel route dns archivo-pietramonte archivo.pietramonte.cl
```

Crea `/etc/cloudflared/config.yml`:
```yaml
tunnel: archivo-pietramonte
credentials-file: /home/pietra/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: archivo.pietramonte.cl
    service: http://localhost:8001       # ← este es el puerto que expone nuestro Compose
  - hostname: coolify.pietramonte.cl     # OPCIONAL: panel de Coolify
    service: http://localhost:8000
  - service: http_status:404
```

Ahora instalalo como servicio:
```bash
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

Verifica logs: `sudo journalctl -u cloudflared -n 30`.

---

## 5. Deploy del proyecto vía Coolify (15 min)

### 5.1. Subir tu repo a GitHub
Si todavía no está, en tu PC:
```bash
git remote add origin git@github.com:tu-usuario/archivo_pietramonte.git
git push -u origin main
```

> ⚠️ **Verifica antes** que `.env`, `data/`, y `db.sqlite3` NO estén en git. Si dudas:
> ```bash
> git ls-files | grep -E "\.env$|\.sqlite|^data/" 
> ```
> No debe imprimir nada.

### 5.2. En Coolify (vía web)
1. **+ New Resource** → **Application** → **Public Repository** (o **Private** con tu PAT).
2. URL del repo, rama `main`.
3. **Build Pack: Dockerfile** (Coolify detecta el `Dockerfile` automáticamente).
4. **Domains**: `archivo.pietramonte.cl`.
5. **Port**: `8000` (lo que expone el contenedor; Coolify lo proxea internamente; el Tunnel apunta a Coolify).
6. **Environment variables**: copia el contenido de `.env.production.example` y rellena los valores reales (en Coolify, "Bulk add").

   **Genera SECRET_KEY**:
   ```bash
   docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(60))"
   ```

7. **Persistent Storage**: añade un volume `/app/data`. Coolify lo monta automáticamente y lo respalda.
8. **Healthcheck**: ya viene en el Dockerfile (`/healthz`). Coolify lo respeta.
9. **Deploy**.

Coolify clona el repo, hace `docker build`, levanta el contenedor, hace migraciones (NO automático — paso siguiente), y levanta.

### 5.3. Migrar BD en producción (1 sola vez)
Desde el panel de Coolify → **Terminal** del contenedor:
```bash
python manage.py migrate
python manage.py createsuperuser            # admin de Django (tú)
python manage.py seed_estructura            # crea los 7 buzones + 5 usuarios reales
```

> El `seed_estructura` te imprime los passwords de los empleados. **Anótalos** y compártelos por canal seguro.

---

## 6. Subir los `.mbox` históricos (varía según tamaño)

### 6.1. Desde tu PC (donde está Thunderbird)
Comprime los `.mbox` de cada cuenta:
```powershell
# PowerShell — repite por cada cuenta
Compress-Archive -Path "C:\Users\<TU>\AppData\Roaming\Thunderbird\Profiles\<HASH>\Mail\<servidor>\Inbox" `
                 -DestinationPath "$env:USERPROFILE\Desktop\aledezma.zip"
```

### 6.2. Subirlos al servidor
```bash
scp aledezma.zip pietra@<IP>:/tmp/
# repite por cada cuenta
```

### 6.3. En el servidor: extraer + importar
```bash
# Ubica la carpeta data del contenedor (Coolify la monta en algún path real)
docker volume inspect $(docker inspect <container-id> --format '{{ range .Mounts }}{{ .Source }}{{end}}')
# Para simplificar, usa la ruta dentro del compose:
mkdir -p /opt/coolify/.../data/mbox/import   # ajustar según donde Coolify pone tu volumen

# Descomprime cada uno
unzip /tmp/aledezma.zip -d ~/imports/aledezma/

# Copia al volumen del contenedor (más simple via terminal del contenedor)
# Desde Coolify → Terminal del container:
mkdir -p /app/data/mbox/import
# luego copia con docker cp desde fuera, o usa volumes share
```

Más fácil: **abre la terminal del contenedor en Coolify**, y desde otra ventana:
```bash
docker cp ~/imports/aledezma/Inbox <container-id>:/app/data/mbox/aledezma_inbox
```

Y ya en la terminal del contenedor:
```bash
python manage.py import_mbox aledezma@pietramonte.cl --archivo=/app/data/mbox/aledezma_inbox
```

Repite por cada buzón. Los adjuntos se extraen automáticamente a `/app/data/adjuntos/`.

---

## 7. Verificar (2 min)

Desde tu PC, navega:
- `https://archivo.pietramonte.cl/` → landing público
- `https://archivo.pietramonte.cl/intranet/` → login del portal
- `https://archivo.pietramonte.cl/admin-pm-<TU-SUFIJO>/` → admin Django

Verifica el healthcheck (en Coolify debe estar verde):
```bash
curl https://archivo.pietramonte.cl/healthz
# → ok
```

Si todo verde: **¡estás en producción!** 🎉

---

## 7.5 Backup de adjuntos a Backblaze B2 (post-deploy, 10 min) — cron 01:00 AM

Coolify ya respalda la DB Postgres a B2 (sección 8). Esto cubre **el otro
volumen crítico**: `/app/data/adjuntos/` (archivos adjuntos de correos).

El comando vive en el contenedor (`python manage.py backup_adjuntos_b2`)
porque ahí está `rclone` instalado (Dockerfile) y `MEDIA_ROOT` se resuelve
con `settings.py`. Lo dispara el cron del host vía `docker exec` para que
sobreviva al rebuild de imagen.

### 7.5.0. ⚠️ ANTES de todo — sacar el cap de Backblaze

Backblaze trae por default un **storage cap** (originalmente 10 GB free).
Si tus adjuntos pasan eso, el sync corta a mitad con
`storage_cap_exceeded (403)`. Aprendido en producción 2026-05-11.

1. Agregá método de pago: https://secure.backblaze.com/account_payment.htm
   (costo real ≈ $0.006/GB/mes → 15 GB ≈ $0.09/mes).
2. Subí el cap o desactivalo: https://secure.backblaze.com/account_alerts.htm
   → recomendado **$5/mes** como cap (≈ 800 GB, sobra para años).
3. Recién después de eso correr el primer sync real.

### 7.5.1. Env vars en Coolify (una vez)

```env
B2_KEY_ID=REEMPLAZAR
B2_APPLICATION_KEY=REEMPLAZAR
B2_BUCKET_NAME=pietramonte-backups
B2_REGION=us-west-002        # opcional, informativo
B2_ENDPOINT=https://s3.us-west-002.backblazeb2.com   # opcional, no se usa con backend b2 nativo
```

Marcar `B2_APPLICATION_KEY` como **Is Secret** en Coolify. Redeploy para
que las env vars lleguen al contenedor.

### 7.5.2. Verificar credenciales antes del primer sync

```bash
ssh dongo
CONT=$(docker ps --format '{{.Names}}' | grep o1rd | head -1)
docker exec -it $CONT python manage.py backup_adjuntos_b2 --check
# → debe imprimir el contenido del bucket (vacío al principio) + "OK"
```

Si falla con "credenciales o bucket inválidos": revisar que las env vars
estén bien escritas (sin espacios, sin comillas) y que la Application Key
tenga acceso al bucket (Read+Write).

### 7.5.3. Primera corrida — dry run primero

```bash
# Simula sin subir nada (ver qué archivos transferiría)
docker exec -it $CONT python manage.py backup_adjuntos_b2 --dry-run

# Si el output se ve sano, corre el sync real
docker exec -it $CONT python manage.py backup_adjuntos_b2
```

La primera vez sube todo (~varios GB según volumen). Las siguientes solo
los cambios. Tiempo estimado: ~1 min por GB con `--bwlimit 10M`.

### 7.5.4. Activar cron nocturno

Ver §11.5 abajo — agregá la línea `30 3 * * * ...` al crontab del host.

### 7.5.5. Soft-delete + retención

El comando usa `rclone sync --backup-dir`. Archivos borrados localmente
**no** se borran del bucket — se mueven a `adjuntos-archive/YYYYMMDD/`.

Recomendado en Backblaze: activar **Lifecycle Rules** en el bucket para
borrar los archive viejos automáticamente:
- "Keep prior versions for N days" → 30 días, por ejemplo.
- Y activar **Object Lock** una vez estabilizado (anti-ransomware).

---

## 8. Backup automático (recomendado, 5 min)

El archivo crítico es `/app/data/db.sqlite3` + `/app/data/adjuntos/`.

### 8.1. Snapshot periódico simple
```bash
sudo nano /usr/local/bin/backup-pietra.sh
```
Contenido:
```bash
#!/bin/bash
set -e
BACKUP_DIR=/var/backups/pietra
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d-%H%M)
docker exec pietramonte_archivo sqlite3 /app/data/db.sqlite3 ".backup /app/data/backup-$DATE.sqlite3"
tar -czf "$BACKUP_DIR/pietra-$DATE.tar.gz" \
    -C /opt/coolify/<.../data> \
    db.sqlite3 adjuntos/
# Conserva últimos 14 días
find "$BACKUP_DIR" -name "pietra-*.tar.gz" -mtime +14 -delete
```

```bash
sudo chmod +x /usr/local/bin/backup-pietra.sh
sudo crontab -e
# añadir:
0 3 * * * /usr/local/bin/backup-pietra.sh > /var/log/backup-pietra.log 2>&1
```

### 8.2. Offsite (opcional pero recomendado)
Sincroniza `/var/backups/pietra` a Backblaze B2 o S3 con `rclone`. Costo aprox $0.005/GB/mes.

---

## 9. Operación diaria — comandos útiles

```bash
# Ver logs del contenedor
docker logs -f pietramonte_archivo

# Reiniciar
docker compose -f /opt/coolify/.../docker-compose.yml restart

# Crear nuevo usuario portal
docker exec -it pietramonte_archivo python manage.py crear_usuario nuevo@gmail.com

# Importar nuevo .mbox
docker exec -it pietramonte_archivo python manage.py import_mbox correo@pietramonte.cl --archivo=/app/data/mbox/archivo

# Cantidad de correos por buzón
docker exec pietramonte_archivo python manage.py shell -c "from correos.models import Buzon; [print(b.email, b.correos.count()) for b in Buzon.objects.all()]"
```

---

## 10. Cuando agregues otros proyectos (clearentry, portafolio)

Tu CPX21 tiene 4 GB. Hoy este proyecto consume ~700 MB. Quedan ~3 GB para el resto.

- Para **portafolio** y **clearentry**: cada uno ~200-300 MB. Caben holgados.
- Para **Mailcow**: NO cabe sin upgrade a CPX31. Ver `memory/decision_mailcow.md`.

Cada nuevo proyecto repite el flujo:
1. Subir repo a GitHub
2. New Resource en Coolify → Dockerfile → Domain
3. Tunnel rule en `/etc/cloudflared/config.yml`
4. Restart `cloudflared`

---

## 11. Troubleshooting rápido

| Síntoma | Causa probable | Fix |
|---|---|---|
| 502 desde Cloudflare | Coolify no está corriendo o Tunnel mal configurado | `docker ps`, `journalctl -u cloudflared` |
| 400 Bad Request "DisallowedHost" | `archivo.pietramonte.cl` no está en `ALLOWED_HOSTS` | Edita `.env` → redeploy |
| 500 al cargar `/intranet/` | `SECRET_KEY` mal formado o falta | Genera uno nuevo y redeploy |
| Static no cargan (404 en CSS) | `collectstatic` no corrió | Rebuild en Coolify |
| Login no acepta nadie | El usuario no existe o está marcado inactivo en `UsuarioPortal` | Crea/activa desde `/admin-…/correos/usuarioportal/` |
| Adjuntos 404 | El volumen `/app/data` no se montó | `docker inspect` y revisa `Mounts` |

---

## 11.4 Sync Gmail — cron cada 15 minutos (post hardening completo)

### Pre-requisito: configurar Redis (CRÍTICO)

Sin Redis, el cache backend cae a LocMemCache que es **per-proceso** — el
lock anti-solapamiento del sync NO funciona entre procesos del cron (cada
tick crea su propio Python process con su propio LocMemCache, y NUNCA se
ven entre sí). Resultado: ticks del cron se solapan, abren conexiones
IMAP en paralelo, Gmail eventualmente rate-limitea con `[OVERQUOTA]` y
bloquea la cuenta 24h.

**Setup Redis en Coolify:**

1. Coolify panel → tu proyecto → **+ New Resource** → **Database** → **Redis**
2. Aceptás defaults (Redis 7, sin password si está aislado en la red del
   proyecto).
3. Anotás el "Internal connection URL" que muestra (ej. `redis://redis-xyz:6379/0`).
4. En tu app Django (también en el panel Coolify) → **Environment Variables**
   → agregar:
   ```
   REDIS_URL=redis://redis-xyz:6379/0
   ```
5. **Redeploy** la app.
6. Verificar:
   ```bash
   docker exec $CONT printenv | grep REDIS_URL
   # Debe imprimir tu URL
   docker exec $CONT python manage.py shell -c "from django.core.cache import cache; cache.set('test', 'ok'); print(cache.get('test'))"
   # Debe imprimir: ok
   ```

### Dedup garantizado en 4 capas

A partir del commit `8bfcc2e` + migración 0022:
1. Cursor `last_uid` por label.
2. Set en memoria por buzón dentro de cada run.
3. **UniqueConstraint partial en DB** sobre `(buzon, mensaje_id)` —
   garantía Postgres-level contra inserts duplicados de cualquier proceso.
4. **Lock de cache** anti-solapamiento (con Redis configurado — ver arriba).

### Hardening post-OVERQUOTA (commit `c0d664a` + posterior)

5. **`fetch_nuevos` como generator** — memoria constante (no carga miles
   de mensajes a RAM antes de procesar). Anti-OOM en buzones grandes.
6. **Timeout TCP 120s** en `imap_connection()` — si Gmail tarda, abortar
   en vez de colgarse hasta que el OOM killer mate.
7. **`imap.close()` antes de `logout()`** en finally — no quedan
   conexiones half-open del lado de Gmail (lo que disparaba OVERQUOTA).
8. **Detección de `OverquotaError`** — si Gmail bloquea, setea flag
   `gmail_overquota_until` en cache 24h. Próximas corridas salen limpias
   automáticamente. Cuando expira el flag (24h), retoma. El operador
   puede limpiar manual con:
   ```bash
   docker exec $CONT python manage.py shell -c "from django.core.cache import cache; cache.delete('sync_gmail:overquota_until'); print('cleared')"
   ```
   O forzar una corrida con `--ignore-overquota`.

### Línea de crontab — cada 15 minutos

```cron
# Sync Gmail cada 15 min — frecuencia conservadora respetando límites
# de IMAP Gmail (~2500 conexiones/día, ~7-9 GB bandwidth/día por cuenta).
*/15 * * * * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py sincronizar_gmail --quiet >> /var/log/pietramonte-gmail-sync.log 2>&1
```

Con `--max-labels=5` default (en el código), cada tick procesa 5 labels.
20 labels × tick cada 15 min = 1 hora para vuelta completa. Suficiente
fresh para uso PyME normal.

### Diagnóstico

```bash
# Estado de cada sync
docker exec $CONT python manage.py estado_sync

# Solo los que tienen error reciente
docker exec $CONT python manage.py estado_sync --solo-errores

# Inspeccionar un correo problemático
docker exec $CONT python manage.py inspeccionar_correo <id>

# Forzar 1 label saltando lock + overquota flag (PELIGROSO si Gmail no desbloqueó)
docker exec -it $CONT python manage.py sincronizar_gmail \
    --label "cpietrasanta@pietramonte.cl" --ignore-lock --ignore-overquota
```

---

## 11.5 Cron del taller + backups (Hetzner host)

Los jobs corren en el host (NO dentro del container, así sobreviven al
rebuild). Editá el crontab con `crontab -e`:

```cron
# Reminders 24h/1h + cleanup de pendientes vencidas. Cada 5 min.
*/5 * * * * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py enviar_recordatorios >> /var/log/pietramonte-recordatorios.log 2>&1

# Carga feriados oficiales del año actual + siguiente. 1ro de enero, 4 AM.
0 4 1 1 * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py cargar_feriados >> /var/log/pietramonte-feriados.log 2>&1

# Backup nocturno de adjuntos a Backblaze B2. 01:00 AM (después del pg_dump de Coolify).
0 1 * * * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py backup_adjuntos_b2 >> /var/log/pietramonte-backup-adjuntos.log 2>&1
```

Setup inicial (después del primer deploy con la app `taller` activa):

```bash
ssh dongo
docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py setup_grupos_taller
docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py cargar_catalogo_inicial
docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py cargar_feriados
```

Verificación del cron a la hora siguiente de configurarlo:

```bash
tail -20 /var/log/pietramonte-recordatorios.log
```


---

## 11.7 Seguridad implementada (estado actual)

**Mantener actualizado:** cada vez que se modifica `middleware.py`,
`admin_2fa.py`, `views.login_view`, `throttle.py`, `captcha.py`, `totp.py`
o el modelo `IntentoLogin`, actualizar esta tabla. Es el documento que
miramos cuando hay que responder "¿qué tenemos contra X?".

### Controles activos (auditoría 2026-05-11)

| Capa | Control | Implementación |
|---|---|---|
| **Transporte** | HTTPS obligatorio + HSTS 30d + Cloudflare Tunnel termina TLS | `SECURE_HSTS_SECONDS=2592000`, `SECURE_PROXY_SSL_HEADER` |
| **CSP** | Estricta sin `unsafe-inline` para script-src, separada para admin | `middleware.SecurityHeadersMiddleware`, test que la valida |
| **CSRF** | Token obligatorio + `HttpOnly` + `SameSite=Lax` + `CSRF_TRUSTED_ORIGINS` | Django built-in + override |
| **Sessions** | `HttpOnly` + `SameSite=Lax` + `Secure` (prod) + cycle_key en pre-2FA | `settings.py` §Sesiones |
| **Headers** | X-Frame DENY (override SAMEORIGIN solo en adjuntos inline), nosniff, referrer-policy strict-origin, Permissions-Policy (cam/mic/geo/etc OFF) | `settings.py` §Endurecimiento + `middleware.SecurityHeadersMiddleware` |
| **Captcha** | v3 propio: respuesta cifrada con Fernet (AES-128-CBC + HMAC-SHA256, key derivada de SECRET_KEY) — la solución NO viaja en plaintext | `correos/captcha.py` |
| **Anti-bot** | Cloudflare Turnstile en login + agenda pública + honeypot + tiempo mínimo en form | `views.login_view`, `taller/anti_bot.py` |
| **Rate-limit login portal** | Per-IP con cache backend (Redis multi-worker, sino LocMemCache) | `views._rl_intento`, `views._rl_bloqueado` |
| **Rate-limit login admin** | 8 fallos / 15 min / IP → 429 con `Retry-After` | `middleware.AdminLoginRateLimitMiddleware` |
| **Anti-timing en login** | `check_password` dummy si email no existe → tiempo de respuesta uniforme | `views.login_view` §4 |
| **Anti-enumeración** | Mensaje de error uniforme (`ERROR_GENERICO`) para todos los fallos | `views.fallo()` |
| **Password hashing** | PBKDF2-SHA256 600.000 iteraciones (Django default 5.x) | `AUTH_PASSWORD_VALIDATORS` |
| **Validadores password** | Min 10 chars, anti-similarity con email, anti-common, anti-numeric | `settings.AUTH_PASSWORD_VALIDATORS` |
| **2FA portal** | TOTP obligatorio + anti-replay (último código rechazado) + recovery codes hash PBKDF2 | `correos/totp.py`, `views.verify_2fa_view` |
| **2FA admin** | TOTP separado + `AdminTOTP` model + middleware bloquea admin sin 2FA | `archivo_pietramonte/admin_2fa.py` |
| **Adjuntos auth** | Ownership check por buzón + Http404 (NO 403) anti-enumeración | `views.adjunto_view` |
| **Adjuntos CSP** | CSP locked per-response, sandbox, nosniff, inline solo para tipos seguros (PDF/img) | `views.adjunto_view`, `views.adjunto_por_cid_view` |
| **CID inline** | Solo imágenes (mime image/*), scope al correo origen | `views.adjunto_por_cid_view` |
| **Logging** | `IntentoLogin` con `ip_hash` (no PII en claro) + `EventoAuditoria` | `correos/models.py` |
| **Admin ofuscado** | URL random vía `ADMIN_URL_PATH` env | `settings.ADMIN_URL_PATH` |
| **IP spoofing** | XFF solo respetado si conexión viene de `TRUSTED_PROXIES` | `views._get_ip`, `views._ip_in_trusted` |
| **Backup Postgres** | Automático nightly Coolify → Backblaze B2 (SSE-B2 encryption at rest) | Coolify Backups tab |
| **Backup adjuntos** | rclone nightly → Backblaze B2, soft-delete con `--backup-dir` versionado | `management/commands/backup_adjuntos_b2.py` |
| **TRUSTED_PROXIES** | Default RFC 1918 (10/8, 172.16/12, 192.168/16). Rate-limit funciona per-cliente real, no per-proxy. | `settings.TRUSTED_PROXIES`, `views._get_ip` |
| **Upload limits** | 5 MB body en RAM, 25 archivos/request, 500 fields/form. Anti-DoS por uploads gigantes y hash-collisions. | `settings.DATA_UPLOAD_*`, `settings.FILE_UPLOAD_*` |
| **Gunicorn caps** | `--limit-request-line 8190`, `--limit-request-field_size 8190`, `--limit-request-fields 100`. Anti-DoS por headers/URLs abusivas. | `Dockerfile` CMD |
| **Account lockout** | 5 fallos consecutivos / cuenta → bloqueo 30 min. Defensa anti botnet (IPs rotan, cuenta sigue bloqueada). | `models.UsuarioPortal.registrar_intento_fallido`, `views.login_view` |
| **Constraint dedup correos** | `UNIQUE(buzon, mensaje_id) WHERE mensaje_id != ''` a nivel Postgres. Race condition entre syncs → IntegrityError → dedup, no insert. | `models.Correo.Meta.constraints`, migración 0022 |
| **Lock sync concurrente** | Cache lock anti-solapamiento de `sincronizar_gmail`. 2 cron tick que se cruzan no compiten. | `management/commands/sincronizar_gmail.py` SYNC_LOCK_KEY |
| **Alertas admin** | Email a `PORTAL_ADMIN_EMAIL` cuando: (1) cuenta entra en lockout; (2) >20 fallos globales/10min (ataque distribuido). Throttle 1h. | `views._enviar_alerta_admin` |
| **External images en correos** | `<img src=https://...>` permitido con `referrerpolicy="no-referrer"` + `loading="lazy"` (mitiga tracking pixels). Toggle vía `EMAIL_ALLOW_EXTERNAL_IMAGES` env. | `correos_tags._img_attr_filter_safe`, `_inject_img_safety_attrs` |

### Lecciones de breaches en la competencia (anti-patrones a evitar)

Auditadas para mantener el sistema preparado:

**1. Microsoft Storm-0558 (2023)** — Actor estatal chino robó una signing
key de Microsoft Account y la usó para acceder a 22 organizaciones + 500
individuos vía Exchange Online. El CSRB concluyó que "la cultura de
seguridad de Microsoft era inadecuada".
- **Lección:** signing keys son la corona. SECRET_KEY rota acá deriva
  Fernet del captcha, sessions, tokens de pre-2FA. **Si se filtra todo
  cae**. Prefijos por contexto (`captcha-v3::SECRET_KEY`) limitan el
  blast radius pero no eliminan el riesgo. **Plan:** documentar
  rotación de SECRET_KEY como procedimiento (TODO).
- **Lección 2:** Microsoft escondía logs de seguridad detrás del tier
  premium. Acá los logs son iguales en todos los deploys (no hay tiers).

**2. Nextcloud CVE-2024-37313 (2FA Bypass)** — Bypass del segundo factor
después de proveer credenciales válidas. CVSS 7.3.
- **Lección:** la verificación de 2FA debe ser server-side en CADA
  request a recursos sensibles, no solo en el flujo de login.
- **Cómo lo evitamos:** `Admin2FAMiddleware.__call__` chequea
  `request.session.get('admin_2fa_ok')` en cada request a `/admin-*`.
  El portal cliente exige verify_2fa antes de marcar `usuario_email` en
  sesión. ✅

**3. Nextcloud CVE-2024-52518 (Improper Authentication)** — Cambiar
storage externo no requería confirmación de password.
- **Lección:** operaciones sensibles (cambiar password, cambiar email,
  agregar buzón al UsuarioPortal, etc) deben re-confirmar password.
- **Estado:** parcial. Cambiar password sí re-confirma. Cambiar email
  o agregar buzones desde admin: solo requiere 2FA admin, no
  re-confirmación. **TODO P1.**

**4. Nextcloud CVE-2024-52525 (Password en memoria)** — Password
quedaba en memoria del proceso PHP.
- **Lección:** no mantener password plain en variables más tiempo del
  necesario.
- **Estado:** acá `password` vive solo dentro de `login_view`,
  se pasa a `check_password()` que lo hashea, y la variable se libera
  al salir de la función. Python GC normal. ✅

**5. Nextcloud XSS en files_pdfviewer** — JS arbitrario vía PDF crafted.
- **Lección:** los PDFs adjuntos pueden contener JS y formularios.
- **Cómo lo evitamos:** `adjunto_view` setea CSP estricta en la
  respuesta de cada PDF inline:
  `default-src 'self'; script-src 'none'; object-src 'self'; frame-ancestors 'self'`.
  Sin scripts → no XSS. ✅

**6. Exchange ProxyShell/ProxyLogon (2021)** — RCE en Exchange Server
on-prem. Decenas de miles de servidores comprometidos.
- **Lección:** no exponer servidores backend a internet directo.
- **Cómo lo evitamos:** Hetzner solo abre SSH (UFW). Todo el tráfico
  HTTP entra por Cloudflare Tunnel saliente — **cero puertos abiertos
  al internet en el server**. ✅

### Cuándo actualizar esta sección

| Si cambiás… | Actualizá |
|---|---|
| `archivo_pietramonte/middleware.py` | Tabla "Controles activos" → fila correspondiente |
| `correos/views.login_view` o flujo 2FA | Filas anti-timing / 2FA / rate-limit |
| `correos/captcha.py`, `correos/totp.py` | Filas Captcha / 2FA |
| `correos/adjunto_view` o `adjunto_por_cid_view` | Filas Adjuntos |
| Modelos `IntentoLogin`, `EventoAuditoria` | Fila Logging |
| Dependencias (Django, Pillow, cryptography) | Anotar version bump + razón |
| Aparece un CVE nuevo relevante en competencia | Sumá a "Lecciones de breaches" con respuesta nuestra |

---

## 12. Lo que sigue (cuando quieras seguir mejorando)

- **Cloudflare Access frente al admin**: Zero Trust → Access Application → ruta `/admin-pm-*`. Solo emails específicos pasan, segunda capa de auth.
- **Sincronización Gmail (Fase 3)**: cron systemd cada 5 min vía Gmail API + OAuth para `soporte.dongo@gmail.com`.
- **Monitoreo externo**: UptimeRobot o BetterStack pingando `/healthz` cada 5 min, gratis hasta 50 monitores.
