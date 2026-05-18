# BIMI Setup — RT Río San Pedro

BIMI (Brand Indicators for Message Identification) hace que el **logo de la marca** aparezca al lado del remitente en los clientes de email que lo soportan. **Sin certificado VMC ($1500/año)**: funciona en Apple Mail (iOS 16+), Yahoo Mail y Fastmail. **No** funciona en Gmail sin VMC.

## Estado actual

- ✅ SVG en formato Tiny PS creado: [`static/img/bimi.svg`](static/img/bimi.svg) — viewBox cuadrado 64×64, sin scripts ni animaciones, fondo blanco obligatorio.
- ✅ WhiteNoise sirve el archivo en `https://portal.rtriosanpedro.cl/static/img/bimi.svg`
- ⏳ **Pendiente**: configurar DNS TXT record en Cloudflare (1 minuto).
- ⏳ **Pendiente**: verificar que DMARC tenga `p=quarantine` o `p=reject` (no `p=none`).

## Paso 1 — Verificar DMARC del dominio

Desde el server o cualquier terminal con `dig`:

```bash
dig TXT _dmarc.rtriosanpedro.cl +short
```

Salida esperada (algo así):
```
"v=DMARC1; p=quarantine; rua=mailto:..."
```

Si dice `p=none` → **NO funciona BIMI**. Hay que cambiar a `p=quarantine` (o `p=reject` más estricto) desde Cloudflare DNS antes de seguir. Esperar 24h de propagación.

## Paso 2 — Crear el DNS TXT en Cloudflare

1. Entrá a **Cloudflare** → seleccioná `rtriosanpedro.cl` → **DNS** → **Records**
2. Click **Add record**
3. Configurá así:
   - **Type:** `TXT`
   - **Name:** `default._bimi`
   - **Content:**
     ```
     v=BIMI1; l=https://portal.rtriosanpedro.cl/static/img/bimi.svg;
     ```
   - **TTL:** Auto
   - **Proxy status:** DNS only (gris, no proxied)
4. Save

## Paso 3 — Verificar

Esperá ~5 minutos a que propague. Después:

```bash
dig TXT default._bimi.rtriosanpedro.cl +short
```

Debe devolver el contenido configurado.

Online validator: https://bimigroup.org/bimi-generator/ → pegá tu dominio en "Inspect existing BIMI record".

## Paso 4 — Probar

Mandate un email a una cuenta **Yahoo Mail** o **Apple Mail** (iOS 16+). El logo debería aparecer al lado del remitente en ~24h (Apple/Yahoo cachean la imagen 1 vez).

Si querés que aparezca también en **Gmail**, requiere comprar **VMC certificate** de DigiCert/Entrust (~USD $1500/año). Sin certificado, Gmail muestra la inicial naranja (como ya viste). No vale la pena para tu escala.

## Notas técnicas

- El SVG `bimi.svg` está en formato **Tiny PS 1.2** estricto: sin `<text>` (las letras RSP están como paths), sin `<script>`, sin animaciones, sin filtros, sin gradients (solo fills planos), `<title>` obligatorio, `baseProfile="tiny-ps"` obligatorio.
- Fondo blanco obligatorio para mejor contraste con UIs claras de los clientes de email.
- viewBox cuadrado 64×64 (BIMI exige cuadrado). El logo horizontal de RSP no aplica acá — esto es solo el escudo.

## Costo total

| Item | Costo |
|---|---|
| SVG Tiny PS | $0 |
| DNS TXT record | $0 |
| Tiempo setup DNS | 5 min |
| Tiempo verificación | 24h pasivas |
| **TOTAL** | **$0 y 5 min de trabajo** |

Vale la pena si tenés clientes que usan Apple Mail o Yahoo. Cero costo, no rompe nada si está mal.
