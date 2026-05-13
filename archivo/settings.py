"""
Configuración Django — Archivo RSP (Río San Pedro).

Lee casi todo desde variables de entorno (.env en dev, secrets en prod).
Docs de cada variable: ver .env.example
"""
import os
from pathlib import Path

# Carga .env si existe (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# ─── Core ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-local-cambiar-en-prod')
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('1', 'true', 'yes')

_hosts_raw = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1')
ALLOWED_HOSTS = [h.strip() for h in _hosts_raw.split(',') if h.strip()]

# ─── Apps ──────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'correos',
    'taller',
]

# ─── Middleware ─────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'archivo.middleware.CSPMiddleware',
    'archivo.middleware.AdminLoginRateLimitMiddleware',
    'archivo.admin_2fa.Admin2FAMiddleware',
]

ROOT_URLCONF = 'archivo.urls'
WSGI_APPLICATION = 'archivo.wsgi.application'

# ─── Templates ─────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'correos.context_processors.portal',
            ],
        },
    },
]

# ─── Base de datos ──────────────────────────────────────────────────────────
_db_url = os.environ.get('DATABASE_URL', '')
if _db_url:
    import dj_database_url
    DATABASES = {'default': dj_database_url.config(default=_db_url, conn_max_age=600)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ─── Auth ───────────────────────────────────────────────────────────────────
# Nota: AUTH_USER_MODEL queda en el default 'auth.User'. El portal de correos
# usa su propio modelo UsuarioPortal (no Django auth), por eso no se redefine.
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ─── Internacionalización ───────────────────────────────────────────────────
LANGUAGE_CODE = 'es-cl'
TIME_ZONE = 'America/Santiago'
USE_I18N = True
USE_TZ = True

# ─── Estáticos (WhiteNoise) ─────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# ─── Media (adjuntos) ───────────────────────────────────────────────────────
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'data' / 'adjuntos'

# ─── Sesiones ───────────────────────────────────────────────────────────────
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
# Sesión default 8h. Con "Recordarme" en login: 30 días vía set_expiry (ver
# correos.views._promover_sesion). SAVE_EVERY_REQUEST=True hace sliding: cada
# acción extiende la sesión, así si el usuario está activo no expira.
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# ─── Seguridad ──────────────────────────────────────────────────────────────
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True

# ─── Email / SMTP ───────────────────────────────────────────────────────────
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST     = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT     = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS  = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('1', 'true', 'yes')
EMAIL_HOST_USER     = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL  = os.environ.get('DEFAULT_FROM_EMAIL', 'RSP <noreply@rtriosanpedro.cl>')

EMAIL_AGENDA_FROM        = os.environ.get('EMAIL_AGENDA_FROM', DEFAULT_FROM_EMAIL)
EMAIL_COTIZACIONES_FROM  = os.environ.get('EMAIL_COTIZACIONES_FROM', DEFAULT_FROM_EMAIL)
EMAIL_REENVIO_FROM       = os.environ.get('EMAIL_REENVIO_FROM', DEFAULT_FROM_EMAIL)
EMAIL_REPLY_TO_AGENDA    = os.environ.get('EMAIL_REPLY_TO_AGENDA', '')
EMAIL_REPLY_TO_COTIZACIONES = os.environ.get('EMAIL_REPLY_TO_COTIZACIONES', '')

# ─── Gmail IMAP (sync inbound) ──────────────────────────────────────────────
GMAIL_IMAP_USER     = os.environ.get('GMAIL_IMAP_USER', EMAIL_HOST_USER)
GMAIL_IMAP_PASSWORD = os.environ.get('GMAIL_IMAP_PASSWORD', EMAIL_HOST_PASSWORD)

# ─── Cache (Redis opcional) ──────────────────────────────────────────────────
_redis_url = os.environ.get('REDIS_URL', '')
if _redis_url:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _redis_url,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }

# ─── Anti-DoS ───────────────────────────────────────────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE  = 5 * 1024 * 1024   # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE  = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 500

# ─── Admin ──────────────────────────────────────────────────────────────────
ADMIN_URL_PATH = os.environ.get('ADMIN_URL_PATH', 'admin-rsp-staff') + '/'

# ─── Portal personalizado ────────────────────────────────────────────────────
# 2FA obligatorio en el portal de correos. False solo en dev sin configurar 2FA.
# En producción (Coolify) setear PORTAL_REQUIRE_2FA=true via env.
PORTAL_REQUIRE_2FA = os.environ.get('PORTAL_REQUIRE_2FA', 'false' if DEBUG else 'true').lower() in ('1', 'true', 'yes')

PORTAL_ADMIN_EMAIL       = os.environ.get('PORTAL_ADMIN_EMAIL', '')
ADMIN_NOTIFY_AGENDA      = [e.strip() for e in os.environ.get('ADMIN_NOTIFY_AGENDA', '').split(',') if e.strip()]
ADMIN_NOTIFY_COTIZACIONES = [e.strip() for e in os.environ.get('ADMIN_NOTIFY_COTIZACIONES', '').split(',') if e.strip()]

# Proxies de confianza para X-Forwarded-For (CSV de IPs/CIDRs)
_trusted = os.environ.get('TRUSTED_PROXIES', '')
TRUSTED_PROXIES = [e.strip() for e in _trusted.split(',') if e.strip()] if _trusted else []

# ─── Anti-bot Turnstile ──────────────────────────────────────────────────────
TURNSTILE_SITE_KEY   = os.environ.get('TURNSTILE_SITE_KEY', '')
TURNSTILE_SECRET_KEY = os.environ.get('TURNSTILE_SECRET_KEY', '')

# ─── Branding ────────────────────────────────────────────────────────────────
BRAND_PRIMARY_COLOR = os.environ.get('BRAND_PRIMARY_COLOR', '#1e7d32')
FIRMA_LOGO_URL      = os.environ.get('FIRMA_LOGO_URL', 'https://rtriosanpedro.cl/images/logo_wide.png')

# ─── Dominios desechables extra ──────────────────────────────────────────────
_disp = os.environ.get('DISPOSABLE_DOMAINS_EXTRA', '')
DISPOSABLE_DOMAINS_EXTRA = [d.strip() for d in _disp.split(',') if d.strip()]

# ─── Imágenes externas en correos ────────────────────────────────────────────
EMAIL_ALLOW_EXTERNAL_IMAGES = os.environ.get('EMAIL_ALLOW_EXTERNAL_IMAGES', 'False').lower() in ('1', 'true', 'yes')

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[%(levelname)s] %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        'correos': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'taller':  {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'django':  {'handlers': ['console'], 'level': 'WARNING',  'propagate': False},
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
