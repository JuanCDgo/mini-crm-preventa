"""
Utilidades de formato para Argentina (GMT-3, Reloj 24hs, Moneda ARS $, Cero Rombos WhatsApp).
"""
import re
from datetime import datetime, timezone, timedelta

TZ_AR = timezone(timedelta(hours=-3))


def ahora_ar_iso() -> str:
    """Devuelve la fecha/hora actual en zona horaria Argentina en formato ISO."""
    return datetime.now(TZ_AR).isoformat()


def ahora_ar_legible() -> str:
    """Devuelve la fecha/hora actual en formato legible (ej: 08/11/2026 14:30 hs)."""
    return datetime.now(TZ_AR).strftime("%d/%m/%Y %H:%M hs")


def fmt_moneda(monto: float) -> str:
    """Formatea moneda argentina: punto para miles, coma para decimales (ej: $ 12.500,00)."""
    if monto is None:
        monto = 0.0
    try:
        val = float(monto)
    except (ValueError, TypeError):
        val = 0.0
    partes = f"{val:,.2f}".split(".")
    enteros = partes[0].replace(",", ".")
    decimales = partes[1]
    return f"$ {enteros},{decimales}"


def fmt_cantidad(cant: float) -> str:
    """Formatea cantidad eliminando ceros decimales innecesarios (ej: 1 o 2,5)."""
    if cant is None:
        return "0"
    try:
        f = float(cant)
    except (ValueError, TypeError):
        return "0"
    if f.is_integer():
        return str(int(f))
    return f"{f:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def limpiar_telefono_argentino(tel: str) -> str:
    """Limpia el teléfono para enviar por WhatsApp API en Argentina (+54 9 ...).
    Elimina guiones, paréntesis, espacios y el '0' / '15' típico.
    """
    if not tel:
        return ""
    digits = re.sub(r"\D", "", tel)
    if digits.startswith("549"):
        return digits
    if digits.startswith("54"):
        # Agregar 9 si falta
        resto = digits[2:]
        if not resto.startswith("9"):
            resto = "9" + resto
        return "54" + resto
    if digits.startswith("0"):
        digits = digits[1:]
    if digits.startswith("15"):
        digits = digits[2:]
    return f"549{digits}"


def generar_url_whatsapp(telefono: str, texto_sin_emojis: str) -> str:
    """Genera la URL para abrir WhatsApp con protocolo seguro y codificación URI.
    ESTRICTAMENTE PROHIBIDO EL USO DE EMOJIS en el texto para evitar rombos corruptos.
    """
    import urllib.parse
    tel_limpio = limpiar_telefono_argentino(telefono)
    # Filtro de seguridad: garantizar que no haya emojis en el mensaje
    texto_seguro = re.sub(r"[\U00010000-\U0010ffff]", "", texto_sin_emojis)
    texto_enc = urllib.parse.quote(texto_seguro)
    if tel_limpio:
        return f"https://api.whatsapp.com/send?phone={tel_limpio}&text={texto_enc}"
    return f"https://api.whatsapp.com/send?text={texto_enc}"
