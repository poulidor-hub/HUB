"""Helpers TOTP (RFC 6238) purs, sans dépendance à la base de données."""
import base64
import hmac
import io
import time

import pyotp
import qrcode
import qrcode.image.svg

TOTP_PERIOD = 30


def generate_totp_secret() -> str:
    """Génère un secret base32 aléatoire pour un nouvel authentificateur."""
    return pyotp.random_base32()


def build_provisioning_uri(secret: str, account_name: str, issuer: str) -> str:
    """Construit l'URI otpauth:// à encoder dans le QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def build_qr_data_uri(otpauth_uri: str) -> str:
    """Rend l'URI otpauth en QR code SVG, retourné en data-URI base64."""
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(otpauth_uri, image_factory=factory)
    buf = io.BytesIO()
    img.save(buf)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def verify_totp_code(secret: str, code: str, last_counter: int | None) -> int | None:
    """Vérifie un code TOTP avec une tolérance de +/- 1 pas de temps.

    Anti-rejeu : refuse tout code dont le pas de temps est <= last_counter.
    Retourne le pas de temps accepté si valide, sinon None.
    """
    if not code or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now_counter = int(time.time()) // TOTP_PERIOD
    for offset in (-1, 0, 1):
        candidate = now_counter + offset
        if last_counter is not None and candidate <= last_counter:
            continue
        expected = totp.generate_otp(candidate)
        if hmac.compare_digest(expected, code):
            return candidate
    return None
