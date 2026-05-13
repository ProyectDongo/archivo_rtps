# CLAUDE.md

Guía rápida para asistentes IA y colaboradores. Para deploy paso-a-paso ver
[DEPLOY.md](DEPLOY.md). Para uso general ver [README.md](README.md).

## Stack

- **Backend**: Django 5.x (Python 3.13+).
- **DB**: Postgres en producción (gestionado por Coolify); SQLite solo en
  dev local sin `DATABASE_URL`.
- **Estáticos**: WhiteNoise + ManifestStaticFilesStorage.
- **SMTP outbound**: Resend (vía `EMAIL_HOST=smtp.resend.com`). DKIM firma
  como `pietramonte.cl`. Si querés revertir a Gmail, cambiá `EMAIL_HOST_*`.
- **IMAP inbound**: Gmail (`imap.gmail.com`) vía `GMAIL_IMAP_USER` /
  `GMAIL_IMAP_PASSWORD` (App Password). El sync corre como management
  command `sincronizar_gmail`, idealmente por cron `*/10`.
- **Cache**: Django cache (Redis si hay `REDIS_URL`, sino LocMemCache).
- **Reverse proxy**: Cloudflare Tunnel (sin nginx propio). El servicio Django
  escucha 8000 dentro del contenedor; tunnel termina TLS afuera.
- **Hosting**: Coolify en Hetzner CPX21. Ver
  [memoria de endpoints](../.claude/projects/c--archivo-pietramonte/memory/reference_pietramonte_endpoints.md)
  para URLs y SSH.

## Apps Django

| App | Qué es |
|---|---|
| `archivo_pietramonte/` | Settings, URLs raíz, middleware (CSP + rate-limit admin), email_utils helper |
| `correos/` | Núcleo: modelos, views, templates, gmail_sync, throttle, totp, captcha. Todo lo del portal de correos vive acá |
| `taller/` | App separada: agenda online de servicios automotrices (público + admin). Tiene su propio modelo y URLs |

## Modelos principales (`correos/models.py`)

- `Buzon` — una cuenta de correo del cliente (cpietrasanta@, vpietrasanta@…). Tiene firma editable per-buzón (campos `firma_*`).
- `Correo` — un email indexado. `tipo_carpeta` ∈ {inbox, enviados, otros}.
- `UsuarioPortal` — login. M2M con `Buzon` (admins ven todos).
- `Etiqueta` — tag per-buzón.
- `Adjunto` — archivo del correo, servido vía `adjunto_view`.
- `CorreoLeido` — marca per-usuario (existe = leído).
- `CorreoSnooze` — snooze per-usuario, `until_at` filtra dinámicamente.
- `BorradorCorreo` — drafts del compose flotante (autosave).
- `BuzonGmailLabel` — mapping Gmail label → Buzon para el sync IMAP.
- `CorreoEnviado`, `ReenvioCorreo` — auditoría de envíos.
- `IntentoLogin` — bitácora con `ip_hash` (no PII en claro).
- `AdminTOTP` — 2FA del superuser de Django.

## Flujo del portal

1. **Login** → `correos:login` → captcha + password + 2FA TOTP.
2. **Inbox** → `/intranet/bandeja/` → lista a pantalla completa, sin preview pane.
3. **Detalle** → `/intranet/correo/N/` → incluye partial `_correo_preview.html`
   con todas las acciones (star, snooze, etiquetas, notas, hilo, reply/fwd).
4. **Compose flotante** → ventana en esquina inferior-derecha. Hijack del
   link "Redactar" del sidebar y de los botones Reply/Forward del detalle.
5. **Drafts** → autosave debounced 1.5s en `/intranet/borradores/`.
6. **Reenvío de archivado** → `reenviar_correo_view` (separa de "responder";
   logea en `ReenvioCorreo`).

## Frontend / JS (todos en `static/js/`)

| Archivo | Rol |
|---|---|
| `theme_init.js` | **Sync, en `<head>`**. Aplica `data-theme`/`data-density` antes del paint (anti-FOUC). NO inline (CSP). |
| `portal_helpers.js` | `PM.csrf`, `PM.post()`, `PM.debounce()`. Base de todo lo demás. |
| `inbox.js` | Lista del inbox: star toggle por fila, atajos j/k/Enter/s, sidebar drawer mobile, popup ayuda búsqueda, toggles stats/filtros, buzones colapsable. |
| `bulk_select.js` | Multi-select + bulk action bar (leer, no-leer, destacar, etiquetar). |
| `compose_floating.js` | Compose flotante (3 estados: normal/minimized/fullscreen). Autosave de draft. Hijack de Reply/Resp.todos/Reenviar del preview. |
| `compose_attach.js` | Drag&drop + lista de adjuntos en `/intranet/redactar/` (form clásico, fallback no-JS). |
| `correo_actions.js` | Handlers del detalle: star, etiquetas, notas, snooze, hilo, marcar no-leído. Auto-init buscando `.preview-card[data-correo-id]`. |
| `lightbox.js` | Imágenes adjuntas: nav prev/next, descarga, swipe mobile. |
| `adj_viewer.js` | Modal inline para PDF / audio / video adjuntos. |
| `theme.js` | Toggles tema/densidad en sidebar. |

## CSS

Todo en `static/css/correos.css` (~5000 líneas). El CSS del modo split-preview
(`.split-preview`, `.is-reading`, `.preview-back-mobile-btn`) fue limpiado.
Sin deuda CSS conocida pendiente.

`static/css/base.css` define vars (`--r`, `--gd`, `--gl`, `--bdr`, etc.) y
reset. Los tema dark se aplican vía `[data-theme="dark"]` overrides.

## Settings clave (`archivo_pietramonte/settings.py`)

- `DATABASE_URL` env → Postgres. Si vacío, SQLite local.
- `EMAIL_HOST/USER/PASSWORD/PORT/USE_TLS` → SMTP outbound (Resend).
- `GMAIL_IMAP_USER/PASSWORD` → IMAP de Gmail. Cae a `EMAIL_HOST_*` por
  fallback (compat con deploys anteriores donde compartían credenciales).
- `FIRMA_LOGO_URL` → URL absoluta del logo embebido en firmas.
- `BRAND_PRIMARY_COLOR` → color de acento en firmas (default `#C80C0F`).
  Cambiar para multi-tenant: cada deployment con su color.
- `ADMIN_URL_PATH` → prefijo random del admin Django (anti-discovery).
- `TURNSTILE_SITE_KEY/SECRET_KEY` → captcha en login y agendar.

## Comandos comunes

```bash
# Desde el server vía ssh dongo + docker exec:
CONT=$(docker ps -qf "name=archivo_pietramonte")
docker exec -it $CONT python manage.py migrate
docker exec -it $CONT python manage.py createsuperuser
docker exec -it $CONT python manage.py seed_estructura --password-default=ClaveTemp.2026!
docker exec -it $CONT python manage.py sincronizar_gmail   # corre el sync IMAP
docker exec -it $CONT python manage.py shell

# Local:
python manage.py runserver
python manage.py test correos
python manage.py makemigrations
```

## Tests

`correos/tests.py` cubre: CSP, X-Frame-Options, avatar iniciales, importer
mbox, login flow (captcha, throttle, 2FA). **No hay tests** para los
endpoints más nuevos (bulk, snooze, drafts, compose con adjuntos, firma,
threading). Pendiente.

## Gotchas conocidos

- **Django `{# … #}` es single-line**. Comentarios multi-línea se renderizan
  como texto y rompen grid/flex. Usar `{% comment %}…{% endcomment %}` o nada.
- **CSP estricto sin `'unsafe-inline'`** para script-src. Hay test que lo
  verifica. Cualquier `<script>` inline debe ir como archivo externo o con
  hash explícito en CSP.
- **Bleach con `strip=True` deja el contenido**. Por eso pre-strippeamos
  `<style>/<script>/<head>/<title>` con regex en `correos_tags.py` antes
  de pasar a bleach.
- **WhiteNoise con `ManifestStaticFilesStorage`**: collectstatic genera
  ambos nombres (hashed y un-hashed); WhiteNoise sirve los dos. La env var
  `FIRMA_LOGO_URL` puede usar el nombre un-hashed (`logo.jpg` directo).
- **Pylance warns sobre imports de Django** (`django.urls`, etc.) si la PC
  local no tiene venv con Django. Son falsos positivos, los imports se
  resuelven en runtime en el contenedor.
- **`X_FRAME_OPTIONS = 'DENY'`** global. El endpoint `adjunto_view` lo
  override a `SAMEORIGIN` solo cuando `disposition='inline'` (para que el
  modal de PDF funcione).
- **DMARC alignment con Resend**: DKIM firma como `pietramonte.cl` ✓.
  Microsoft/Outlook puede tardar semanas en confiar dominios nuevos —
  no es bug, warmup natural.

## Convenciones

- Nombres de índices DB **explícitos** en `Meta.indexes` cuando hay
  migración a mano (ver `models.Index(name='...')`). Sin name explícito,
  Django re-genera y rompe migraciones.
- No commitear `db.sqlite3` ni archivos de `data/`. `.gitignore` los cubre.
- Commits siguen formato `tipo(scope): descripción` (feat/fix/sec/config/ux/refactor).
- Nunca `--no-verify` ni amend de commits ya pushados. Nunca `git push --force` a main.

## Referencias rápidas

- Memorias del proyecto: `~/.claude/projects/c--archivo-pietramonte/memory/`
- Servicio en panel Coolify: `https://coolify.dongoproyect.com`
- Portal en producción: `https://archivo.pietramonte.cl`
- Repo: `https://github.com/ProyectDongo/archivo_pietramonte`
