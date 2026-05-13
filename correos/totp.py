"""
Helpers de TOTP (RFC 6238) y recovery codes para 2FA.

Diseño:
  - `pyotp` para TOTP estándar (compatible con Google Authenticator, Authy,
    1Password, Bitwarden, etc.).
  - Recovery codes en formato XXXX-XXXX (alfabeto sin chars confusos).
    Se guardan hasheados con PBKDF2 (Django default), nunca en plano.
  - Anti-replay: el código TOTP recién usado se guarda; verificar() lo rechaza
    si vuelve a venir dentro de la misma ventana de 30 s.
"""
from __future__ import annotations

import io
import secrets

import pyotp
import qrcode
import qrcode.image.svg
from django.contrib.auth.hashers import check_password, make_password


# Alfabeto sin caracteres ambiguos visualmente (sin 0/O, 1/I/L, etc.)
_RECOVERY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def generar_secret() -> str:
    """Devuelve un secret base32 de 32 chars (160 bits de entropía)."""
    return pyotp.random_base32()


def generar_recovery_codes_planos(n: int = 8) -> list[str]:
    """
    Genera N recovery codes legibles (formato XXXX-XXXX).
    Estos son los que se muestran al usuario UNA SOLA VEZ. Para guardar,
    pasarlos por `hashear_codes()`.
    """
    out = []
    for _ in range(n):
        a = ''.join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4))
        b = ''.join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4))
        out.append(f'{a}-{b}')
    return out


def hashear_codes(codes: list[str]) -> list[str]:
    """Hashea cada recovery code con PBKDF2 (Django default). Lista in/out."""
    return [make_password(c) for c in codes]


def verificar_totp(
    secret: str,
    code: str,
    ultimo_usado: str = '',
    valid_window: int = 1,
) -> bool:
    """
    Valida un código TOTP de 6 dígitos.

    `valid_window=1` permite ±30 s de skew de reloj (un step antes y uno después).
    `ultimo_usado` bloquea replay del mismo código dentro de la ventana.
    """
    code_limpio = (code or '').strip().replace(' ', '').replace('-', '')
    if not secret or len(code_limpio) != 6 or not code_limpio.isdigit():
        return False
    if ultimo_usado and code_limpio == ultimo_usado:
        return False
    return pyotp.TOTP(secret).verify(code_limpio, valid_window=valid_window)


def normalizar_codigo_totp(code: str) -> str:
    return (code or '').strip().replace(' ', '').replace('-', '')


def consumir_recovery_code(
    codes_hash: list[str],
    code_intentado: str,
) -> tuple[bool, list[str]]:
    """
    Si el código matchea uno de los hashes, devuelve (True, lista_sin_ese_hash).
    Si no, devuelve (False, lista_intacta).

    Cada recovery code es de un solo uso: al matchear se quema.
    """
    intentado = (code_intentado or '').strip().upper()
    if not intentado:
        return False, codes_hash
    for i, h in enumerate(codes_hash):
        if check_password(intentado, h):
            return True, codes_hash[:i] + codes_hash[i + 1:]
    return False, codes_hash


def url_otpauth(secret: str, email: str, issuer: str = 'Pietramonte Archivo') -> str:
    """
    URL provisioning estándar otpauth:// que las apps de TOTP entienden.
    Issuer aparece en la app del usuario.
    """
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def qr_svg(url: str) -> str:
    """
    Devuelve el QR como SVG inline (sin Pillow). Lo embebemos directo
    en el template para no servir un PNG separado.
    """
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(url, image_factory=factory, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode('utf-8')


def pdf_recovery_codes(codes: list[str], identidad: str, app_url: str = 'archivo.pietramonte.cl') -> bytes:
    """
    Genera un PDF A4 con los recovery codes para que el usuario imprima
    o guarde offline. Sin imágenes (todo vector). Pure Python (reportlab).
    """
    from datetime import datetime

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # ─── Cabecera ────────────────────────────────────────────────────────
    c.setFillColorRGB(0.78, 0.05, 0.06)  # #C80C0F
    c.setFont('Helvetica-Bold', 18)
    c.drawString(2 * cm, height - 2 * cm, 'PIETRAMONTE')
    c.setFillColorRGB(0.10, 0.10, 0.10)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(2 * cm, height - 2.7 * cm, 'Códigos de recuperación 2FA')

    c.setFont('Helvetica', 10)
    c.drawString(2 * cm, height - 3.6 * cm, f'Cuenta: {identidad}')
    c.drawString(2 * cm, height - 4.1 * cm, f'Generado: {datetime.now():%Y-%m-%d %H:%M}')

    # ─── Aviso ───────────────────────────────────────────────────────────
    c.setStrokeColorRGB(0.85, 0.70, 0.20)
    c.setFillColorRGB(1.00, 0.96, 0.80)
    c.rect(2 * cm, height - 7.4 * cm, width - 4 * cm, 2.6 * cm, fill=1, stroke=1)
    c.setFillColorRGB(0.40, 0.30, 0.00)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(2.3 * cm, height - 5.2 * cm, 'IMPORTANTE — leer antes de guardar')
    c.setFont('Helvetica', 9.5)
    text = c.beginText(2.3 * cm, height - 5.7 * cm)
    for line in [
        'Cada código sirve UNA SOLA VEZ. Cuando entres con uno, queda inutilizado.',
        'Si perdés tu app de autenticación y NO tenés ninguno de estos códigos a mano,',
        'no podrás acceder al archivo. Guardalo en un gestor de contraseñas (Bitwarden,',
        '1Password) o impreso en lugar seguro. NO lo compartas por mail ni chat.',
    ]:
        text.textLine(line)
    c.drawText(text)

    # ─── Códigos ─────────────────────────────────────────────────────────
    c.setFillColorRGB(0.10, 0.10, 0.10)
    c.setFont('Courier-Bold', 18)
    y_start = height - 9.5 * cm
    for i, code in enumerate(codes):
        col = i % 2
        row = i // 2
        x = (3 + col * 7.5) * cm
        cy = y_start - row * 1.4 * cm
        # Fondo gris claro
        c.setFillColorRGB(0.96, 0.96, 0.96)
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.rect(x - 0.4 * cm, cy - 0.4 * cm, 6 * cm, 1 * cm, fill=1, stroke=1)
        c.setFillColorRGB(0.10, 0.10, 0.10)
        c.drawString(x, cy, code)

    # ─── Pie ─────────────────────────────────────────────────────────────
    c.setFillColorRGB(0.50, 0.50, 0.50)
    c.setFont('Helvetica', 8)
    c.drawCentredString(width / 2, 1.5 * cm, f'Bóveda Pietramonte · {app_url}')

    c.showPage()
    c.save()
    return buf.getvalue()
