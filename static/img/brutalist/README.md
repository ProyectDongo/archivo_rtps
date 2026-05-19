# Fondos brutalistas del escritorio

Las imágenes `.jpg` / `.png` / `.webp` en este directorio se usan como **fondo
rotativo del escritorio del portal** (`/intranet/escritorio/`).

## Cómo funciona

- Al cargar el escritorio, Python elige **una imagen al azar** de este
  directorio y la usa como `background-image`.
- Si el directorio está vacío, el escritorio cae al fondo CSS de hormigón
  (gradientes radiales) que ya tiene como fallback.
- Recarga la página → otra imagen aleatoria.

## Qué fotos subir

Estilo recomendado: **brutalismo arquitectónico** — hormigón crudo, geometría
sólida, luz lateral. La carga visual del escritorio compite con el texto
encima, así que las fotos deberían tener buen contraste (no pasteles, no
saturadas, no escenas con mucho ruido).

Referencias de búsqueda en Unsplash / Pexels (licencia comercial OK):
- `brutalism architecture`
- `concrete building`
- `Habitat 67 Montreal`
- `Boston City Hall`
- `Barbican London`
- `Tadao Ando`
- `concrete texture`

## Requisitos técnicos

| Cosa | Valor |
|---|---|
| **Formatos** | `.jpg`, `.png`, `.webp` |
| **Resolución** | 1920×1080 mínimo. Idealmente 2560×1440. |
| **Peso** | Comprimí a 300-700 KB (usá [squoosh.app](https://squoosh.app/) o `cwebp -q 80`). |
| **Orientación** | Horizontal (landscape) |
| **Cantidad** | 3-8 archivos. Mantenelo curado, no satures de imágenes. |

## Cómo agregar

1. Descargá la foto.
2. Comprimila a 300-700 KB.
3. Renombrala `1.jpg`, `2.jpg`, etc. (o el nombre que quieras — Python rota
   sobre todos los archivos del directorio).
4. Copiala a este directorio (`static/img/brutalist/`).
5. En producción, después de pushear: `docker exec $CONT python manage.py collectstatic --noinput`.
6. Recargá `/intranet/escritorio/` con Ctrl+F5.

## Atribuciones

Si subís fotos con licencia que requiera atribución (algunas de Pexels), agregalas
en un CREDITS.md acá al lado siguiendo el formato de `static/img/bg/CREDITS.md`.
