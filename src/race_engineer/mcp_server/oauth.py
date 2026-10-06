"""Servidor OAuth 2.1 mínimo — autorización de ChatGPT para Race Engineer.

Implementa lo justo para que ChatGPT pueda autenticarse como cliente MCP:
authorization code + PKCE (S256), CIMD, tokens en memoria.

El AS (este módulo) y el RS (token_verifier en FastMCP) viven en el mismo proceso.
"""

from __future__ import annotations

import base64
import hashlib
import html as html_mod
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Almacén en memoria ────────────────────────────────────────────────


@dataclass
class AuthCodeEntry:
    """Código de autorización pendiente de canje."""

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    scope: str
    resource: str
    state: str
    expires_at: float


@dataclass
class TokenEntry:
    """Token de acceso emitido."""

    token: str
    client_id: str
    scope: str
    resource: str
    created_at: float
    expires_at: float


class OAuthStore:
    """Almacén en memoria de códigos y tokens OAuth.

    Para un proyecto personal con un solo usuario no necesita persistencia.
    Los tokens sobreviven mientras el proceso esté arriba.
    """

    def __init__(self, pin: str = "", token_lifetime_s: int = 86400 * 30) -> None:
        self.pin = pin
        self.token_lifetime_s = token_lifetime_s
        self._codes: dict[str, AuthCodeEntry] = {}
        self._tokens: dict[str, TokenEntry] = {}

    # ── Códigos de autorización ───────────────────────────────────────

    def create_code(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scope: str,
        resource: str,
        state: str = "",
    ) -> str:
        """Genera un código de autorización (160 bits de entropía)."""
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthCodeEntry(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope,
            resource=resource,
            state=state,
            expires_at=time.time() + 300,
        )
        logger.info("Codigo de autorizacion creado para client=%s", client_id[:60])
        return code

    def exchange_code(
        self,
        code: str,
        code_verifier: str,
        client_id: str,
    ) -> Optional[TokenEntry]:
        """Canjea un código por un token (con verificación PKCE S256)."""
        entry = self._codes.pop(code, None)
        if not entry:
            logger.warning("Codigo no encontrado o ya canjeado")
            return None

        if entry.expires_at < time.time():
            logger.warning("Codigo expirado")
            return None

        if entry.client_id != client_id:
            logger.warning("client_id no coincide: esperado=%s, recibido=%s",
                           entry.client_id[:60], client_id[:60])
            return None

        # Verificar PKCE S256
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

        if not secrets.compare_digest(expected, entry.code_challenge):
            logger.warning("PKCE S256 verification failed")
            return None

        # Emitir token
        token = secrets.token_urlsafe(48)
        tok = TokenEntry(
            token=token,
            client_id=client_id,
            scope=entry.scope,
            resource=entry.resource,
            created_at=time.time(),
            expires_at=time.time() + self.token_lifetime_s,
        )
        self._tokens[token] = tok
        logger.info("Token emitido para client=%s (expira en %d dias)",
                     client_id[:60], self.token_lifetime_s // 86400)
        return tok

    # ── Tokens ────────────────────────────────────────────────────────

    def verify_token(self, token: str) -> Optional[TokenEntry]:
        """Verifica que un token es válido y no ha expirado."""
        entry = self._tokens.get(token)
        if not entry:
            return None
        if entry.expires_at < time.time():
            del self._tokens[token]
            logger.info("Token expirado y eliminado")
            return None
        return entry

    # ── PIN ───────────────────────────────────────────────────────────

    def verify_pin(self, pin: str) -> bool:
        """Verifica el PIN de acceso. Sin PIN configurado siempre permite."""
        if not self.pin:
            return True
        return secrets.compare_digest(pin, self.pin)


# ── HTML de consentimiento ────────────────────────────────────────────


def build_authorize_html(
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str,
    require_pin: bool = True,
    error_msg: str = "",
) -> str:
    """Genera la página HTML de consentimiento OAuth."""
    esc = html_mod.escape

    pin_field = ""
    if require_pin:
        pin_field = """
        <div class="field">
            <label for="pin">PIN de acceso:</label>
            <input type="password" id="pin" name="pin" required
                   placeholder="PIN" autocomplete="off"
                   inputmode="numeric" pattern="[0-9]*">
        </div>"""

    error_html = ""
    if error_msg:
        error_html = f'<div class="error">{esc(error_msg)}</div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Race Engineer - Autorizacion</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#1a1a2e;color:#e0e0e0;
  display:flex;justify-content:center;align-items:center;min-height:100vh;
}}
.card{{
  background:#16213e;border:1px solid #0f3460;border-radius:12px;
  padding:2rem;max-width:420px;width:90%;
  box-shadow:0 8px 32px rgba(0,0,0,0.3);
}}
h1{{font-size:1.4rem;margin-bottom:.5rem;color:#e94560}}
.sub{{color:#888;font-size:.9rem;margin-bottom:1.5rem}}
.badge{{
  display:inline-block;background:#0f3460;border:1px solid #e94560;
  border-radius:4px;padding:.2rem .6rem;font-size:.85rem;margin:.5rem 0 1rem;
}}
.field{{margin-bottom:1rem}}
.field label{{display:block;margin-bottom:.3rem;font-size:.9rem}}
.field input{{
  width:100%;padding:.6rem;border:1px solid #0f3460;border-radius:6px;
  background:#1a1a2e;color:#e0e0e0;font-size:1rem;
}}
.btn{{
  display:block;width:100%;padding:.8rem;border:none;border-radius:6px;
  font-size:1rem;font-weight:600;cursor:pointer;margin-top:1rem;
  background:#e94560;color:white;
}}
.btn:hover{{background:#c73e54}}
.error{{
  background:#4a1a1a;border:1px solid #e94560;border-radius:6px;
  padding:.6rem;margin-bottom:1rem;font-size:.9rem;color:#ff6b6b;
}}
.info{{font-size:.8rem;color:#666;margin-top:1rem;word-break:break-all}}
</style>
</head>
<body>
<div class="card">
  <h1>Race Engineer</h1>
  <p class="sub">Una aplicacion quiere acceder a tu telemetria de RaceRoom.</p>
  <div class="badge">{esc(scope)}</div>
  {error_html}
  <form method="POST" action="/authorize">
    <input type="hidden" name="client_id" value="{esc(client_id)}">
    <input type="hidden" name="redirect_uri" value="{esc(redirect_uri)}">
    <input type="hidden" name="scope" value="{esc(scope)}">
    <input type="hidden" name="state" value="{esc(state)}">
    <input type="hidden" name="code_challenge" value="{esc(code_challenge)}">
    <input type="hidden" name="code_challenge_method" value="{esc(code_challenge_method)}">
    <input type="hidden" name="resource" value="{esc(resource)}">
    {pin_field}
    <button type="submit" class="btn">Autorizar acceso</button>
  </form>
  <p class="info">Client: {esc(client_id[:80])}</p>
</div>
</body>
</html>"""
