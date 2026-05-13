# Archivo Digital — Pietramonte Automotriz

**El cerebro digital de tu PyME.** Almacén unificado de correos, archivos,
contratos y agenda, con interfaz tipo escritorio. Se conecta con lo que
ya usás — no te obliga a reemplazar nada.

> **Pietramonte** es la primera implementación productiva. El stack es
> reutilizable y se está madurando como **producto SaaS complementario**
> para PyMEs de Chile/LatAm.

---

## Posicionamiento

**No competimos con Microsoft 365 ni Google Workspace.** Ellos te venden
una suite de oficina (correo + Word + Excel + Drive). Nosotros te damos
**la memoria viva de tu negocio**: el lugar donde correos, contratos,
archivos, citas e historia operativa quedan **conectados entre sí y
buscables desde un solo lugar**.

### Cómo encajamos con tu software actual

La mayoría de las PyMEs ya tienen 3-8 herramientas:

| Tu negocio ya usa… | Nosotros sumamos… |
|---|---|
| Gmail / Outlook | Archivo histórico de TODOS los correos + búsqueda total |
| Excel para facturas/clientes | Importás los Excel; quedan correlacionados con los correos del mismo cliente |
| WhatsApp Business (futuro) | Ingest del historial de chats + búsqueda unificada |
| gestioncar.cl / Defontana / Bsale | API connector — sus facturas/órdenes aparecen junto a los correos del cliente |
| Calendar (Google/Outlook) | App Taller integra y agrega recordatorios SMS |
| Drive / OneDrive | Espejo organizado por perfil/tema/fecha + audit log |

**Nada se reemplaza.** Pietramonte es la capa de **memoria + colaboración**
encima de las herramientas operativas que ya tenés.

### Por qué nos elegirías

| Argumento | vs. Microsoft 365 / Google Workspace | vs. Open Archiver / Mailpiler |
|---|---|---|
| **Costo flat, no por usuario** | ~20-70x más barato a partir de 10 usuarios | similar |
| **Datos en tu server (Hetzner UE)** | Sin dependencia de US cloud | similar |
| **Correos + docs + agenda unificados** | Microsoft tiene todo pero **separado en 6 apps**; nosotros UNA UI | Ellos solo correos |
| **UX moderna (escritorio Mac-style)** | Comparable | Muy por delante |
| **Verticales (Taller automotriz)** | No tienen | No tienen |
| **Aislamiento 1 servidor / 1 cliente** | SaaS multi-tenant | Variable |
| **Spanish-first, LatAm-aware** | EN-first, traducción | EN-only |

---

## Visión del producto

Más que un portal de correos, esto evoluciona hacia un **almacén digital
descentralizado**. El home es un escritorio con apps; cada app maneja un
tipo de información y todas comparten búsqueda, permisos y notificaciones.

### Apps planificadas

| App | Estado | Qué hace |
|---|---|---|
| **Correos** | ✅ Producción | Lectura, envío, snooze, etiquetas, hilo, drafts. Sync IMAP Gmail. |
| **Taller** | ✅ Producción | Agenda de servicios automotrices, público + admin. |
| **Archivos** | 🟡 Fase 2 | Documentos genéricos jerarquizados por perfil → tema → fecha. |
| **Contratos** | 🟡 Fase 2 | Documentos legales con versiones, vencimientos y firma digital. |
| **Ajustes** | 🟡 Fase 1.5 | Centro de control: tema, firma, notificaciones, password, 2FA, permisos (admin). |
| **Papelera** | 🟡 Fase 2 | Soft-delete + recuperación a N días. |

### Roadmap por fases

#### Fase 1 — Home + Dashboard (en curso)
- Escritorio tipo Windows como home (login → escritorio).
- Stats en vivo: ingresos de correos/archivos (gráfico), top 5 perfiles
  más activos, top 5 "temas" detectados (cotización, recibo, repuesto,
  cancelación, factura) por reglas keyword/regex.
- **Categorías de temas editables** por el usuario (CRUD desde Ajustes:
  agregar/quitar/renombrar/cambiar keywords).
- **Personalización del escritorio**: reordenar íconos de apps, mostrar/
  ocultar widgets, mover widgets entre posiciones (drag&drop). El layout
  se guarda por usuario (UserDesktopPrefs).
- Widgets: últimos correos, próximas citas taller, archivos recientes.
- Búsqueda global cross-app (correos + archivos + contratos).
- Taskbar persistente con apps abiertas + reloj.

#### Fase 2 — Apps + Permisos
- App Archivos con upload, drag&drop, jerarquía perfil/tema/fecha.
- App Contratos con vencimientos y alertas.
- Permisos por carpeta/perfil (área Taller no ve Facturación, etc).
- Audit log: quién bajó/subió/borró qué archivo y cuándo.
- Enlaces internos (`archivo:NN`, `correo:NN`, `contrato:NN`) que se
  renderizan como chip clickeable en cualquier campo de texto.
- Etiquetas transversales que crucen apps (tag "Cliente Andina" en
  correos + archivos + contratos).
- Comentarios polimórficos sobre cualquier objeto.

#### Fase 3 — Colaboración interna
- Mensajería interna entre usuarios ("¿tenés este archivo?").
- Solicitudes de recursos con workflow approve/deny.
- Recordatorios personales + recordatorios enviables a otros usuarios.
- Versiones de archivos (subir v2, ver historial, comparar).
- Links compartidos externos: enviar archivo a cliente sin login, con
  expiración configurable + audit de accesos.

#### Visión futura — App móvil
- Backend Django expone REST API (DRF) además de las vistas HTML.
- App móvil nativa (React Native o Flutter) que consume la API.
- Push notifications para correos nuevos, citas próximas y recordatorios.
- Captura de fotos directo al app Archivos (recepciones de taller, etc).
- Firma digital de contratos desde el teléfono.

---

## Integraciones — SaaS complementario

Pietramonte está diseñado para **vivir junto a otras herramientas**, no
reemplazarlas. Por eso priorizamos importación / exportación / conectores
desde el día 1.

### Importadores (Fase 2)

| Origen | Formato | Estado |
|---|---|---|
| Thunderbird mbox | `.mbox` | ✅ Producción (`management/commands/import_mbox.py`) |
| Gmail (IMAP por labels) | IMAP | ✅ Producción (`management/commands/sincronizar_gmail.py`) |
| Outlook PST | PST | 🟡 Fase 2 |
| Excel (clientes, facturas, contactos) | XLSX/CSV | 🟡 Fase 2 |
| WhatsApp Business (history export) | ZIP de WhatsApp | 🔴 Fase 3 |

### Connectores hacia ERPs/CRMs LatAm (Fase 3 — roadmap)

| Plataforma | Vertical | Modo |
|---|---|---|
| **gestioncar.cl** | Taller automotriz | API REST → cruzar facturas con correos del mismo cliente |
| **Defontana** | Contabilidad PyME Chile | API → órdenes de compra junto al hilo de correos del proveedor |
| **Bsale** | Facturación electrónica Chile | API → DTEs junto al cliente |
| **Khipu** | Pagos electrónicos | Webhook → vincular comprobantes a contratos |
| **Webpay Plus** | Pagos | Idem |
| **Google Calendar / Outlook** | Agenda | API bidireccional con app Taller |

### Exportadores (Fase 2)

- **Excel/CSV** de cualquier vista (inbox, archivos, contratos, citas).
- **PDF** de hilos de correos (audit-grade, con metadata).
- **ZIP** de adjuntos por filtro (ej. todos los PDF de un cliente).
- **API REST** read-only para pull desde otros sistemas (Fase 3).

### Modelo de pricing propuesto (cuando lo abramos a más clientes)

| Plan | Para quién | Precio |
|---|---|---|
| **Setup único** | Onboarding + customización inicial | USD 200-500 |
| **Mantención mensual** | Hosting + actualizaciones + soporte | USD 30-80/mes flat (no per-seat) |
| **Vertical addon** | App específica (Taller, Clínica, Estudio) | +USD 20/mes |
| **Connector LatAm** | Por integración activa (gestioncar, Bsale, etc) | +USD 10/mes |

Comparativa: una PyME de 20 personas paga **~$140-280/mes con Google
Workspace Business Standard** ($14/user). Pietramonte: **~$80/mes total**.
Ahorro: 50-70%, **con datos en server propio y verticales que ningún SaaS
mainstream ofrece**.

---

## Instalación en el servidor Linux (sin Docker, directo)

```bash
# 1. Clonar/copiar el proyecto
cd /opt
git clone ... archivo_pietramonte   # o copia manual
cd archivo_pietramonte

# 2. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
nano .env   # editar SECRET_KEY y ALLOWED_HOSTS

# 5. Generar SECRET_KEY segura
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# 6. Migrar base de datos
python manage.py migrate

# 7. Probar que funciona
python manage.py runserver 0.0.0.0:8001
```

---

## Instalación con Docker (recomendado)

```bash
# 1. Configurar .env
cp .env.example .env
nano .env

# 2. Construir imagen
docker compose build

# 3. Migrar base de datos (primera vez)
docker compose run --rm web python manage.py migrate

# 4. Levantar
docker compose up -d
```

---

## Importar archivos .mbox desde Thunderbird

Thunderbird guarda los correos en archivos .mbox en:
- Windows: C:\Users\TuUsuario\AppData\Roaming\Thunderbird\Profiles\xxx\Mail\
- Linux: ~/.thunderbird/xxx/Mail/

### Importar un buzón
```bash
# Sin Docker
python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/Inbox

# Con Docker
docker compose exec web python manage.py import_mbox aledezma@pietramonte.cl --archivo=/app/mbox/aledezma.mbox

# Reimportar limpio (borra e importa de nuevo)
python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/Inbox --limpiar
```

### Importar toda una carpeta de .mbox
```bash
python manage.py import_mbox cobranza@pietramonte.cl --carpeta=/opt/mboxes/cobranza/
```

### Copiar .mbox al servidor
```bash
# Desde tu PC con Thunderbird al servidor
scp "C:\Users\Anghello\AppData\Roaming\Thunderbird\Profiles\xxx\Mail\mail.pietramonte.cl\Inbox" \
    usuario@servidor:/opt/archivo_pietramonte/mbox/aledezma.mbox
```

---

## Configurar Cloudflare Tunnel

En Cloudflare → Zero Trust → Tunnels → tu tunnel existente:
```
Public hostname:  archivo.pietramonte.cl
Service:          http://localhost:8000   (o http://pietramonte_archivo:8000 si es Docker)
```

---

## Cuentas con acceso (editarlas en views.py)

```python
EMAILS_VALIDOS = [
    'aledezma@pietramonte.cl',
    'cobranza@pietramonte.cl',
    'contacto@pietramonte.cl',
    'cpietrasanta@pietramonte.cl',
    'vpietrasanta@pietramonte.cl',
    'ralbornoz@pietramonte.cl',
]
```

---

## Estructura del proyecto

```
archivo_pietramonte/
├── archivo_pietramonte/    # config Django
│   ├── settings.py
│   └── urls.py
├── correos/                # app principal
│   ├── models.py           # Buzon, Correo
│   ├── views.py            # login, inbox, detalle
│   ├── urls.py
│   └── management/commands/
│       └── import_mbox.py  # comando de importación
├── templates/
│   ├── base.html
│   └── correos/
│       ├── login.html
│       ├── inbox.html
│       └── detalle.html
├── mbox/                   # aquí van los archivos .mbox
├── .env
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
