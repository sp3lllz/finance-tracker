"""OpenID Connect login against Pocket ID (or any standard OIDC provider)."""

import logging
import secrets
from urllib.parse import quote, urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

log = logging.getLogger("finance.auth")

PUBLIC_PATHS = ("/login", "/auth/callback", "/healthz", "/static/")


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.oauth = OAuth()
        if not settings.auth_disabled:
            client_kwargs = {"scope": settings.oidc_scopes, "code_challenge_method": "S256"}
            if not settings.oidc_client_secret:
                # Pocket ID "public client": PKCE only, no secret.
                client_kwargs["token_endpoint_auth_method"] = "none"
            self.oauth.register(
                name="oidc",
                server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret or None,
                client_kwargs=client_kwargs,
            )

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.app_url}/auth/callback"

    def is_allowed(self, claims: dict) -> bool:
        s = self.settings
        if s.allowed_emails and (claims.get("email") or "").lower() not in s.allowed_emails:
            return False
        if s.allowed_groups and not set(claims.get("groups") or []) & set(s.allowed_groups):
            return False
        return True

    async def login(self, request: Request):
        if self.settings.auth_disabled:
            return RedirectResponse("/", status_code=303)
        request.session["next"] = safe_next(request.query_params.get("next", "/"))
        try:
            return await self.oauth.oidc.authorize_redirect(request, self.redirect_uri)
        except Exception as exc:  # discovery document unreachable or invalid
            log.error("Could not start sign-in with %s: %s", self.settings.oidc_issuer, exc)
            return PlainTextResponse(
                f"Can't reach the sign-in provider at {self.settings.oidc_issuer}. "
                "Check OIDC_ISSUER and that Pocket ID is running.", status_code=502)

    async def callback(self, request: Request):
        try:
            token = await self.oauth.oidc.authorize_access_token(request)
        except OAuthError as exc:
            log.warning("OIDC callback failed: %s", exc.error)
            return PlainTextResponse(
                f"Sign-in failed ({exc.error}). Go back to / to try again.", status_code=400
            )
        claims = token.get("userinfo") or {}
        if not self.is_allowed(claims):
            log.warning("Denied sign-in for subject %s", claims.get("sub"))
            return PlainTextResponse("Your account is not allowed to use this app.", status_code=403)
        next_url = request.session.pop("next", "/")
        request.session.clear()
        request.session["user"] = {
            "sub": claims.get("sub"),
            "name": claims.get("name") or claims.get("preferred_username") or claims.get("email"),
            "email": claims.get("email"),
        }
        request.session["id_token"] = token.get("id_token")
        request.session["csrf"] = secrets.token_urlsafe(32)
        return RedirectResponse(next_url, status_code=303)

    async def logout(self, request: Request):
        id_token = request.session.get("id_token")
        request.session.clear()
        if self.settings.auth_disabled:
            return RedirectResponse("/", status_code=303)
        try:
            metadata = await self.oauth.oidc.load_server_metadata()
        except Exception:  # provider unreachable: local logout is still done
            metadata = {}
        end_session = metadata.get("end_session_endpoint")
        if end_session:
            params = {"post_logout_redirect_uri": self.settings.app_url + "/"}
            if id_token:
                params["id_token_hint"] = id_token
            return RedirectResponse(f"{end_session}?{urlencode(params)}", status_code=303)
        return PlainTextResponse("Signed out.")


def safe_next(value: str) -> str:
    """Only allow same-site relative redirects after login."""
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


class RequireLogin:
    """ASGI middleware: every non-public path needs a signed-in session.

    Must sit inside SessionMiddleware so scope["session"] is populated.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        session = scope.setdefault("session", {})
        if self.settings.auth_disabled:
            session.setdefault("user", {"name": "Local user", "email": None, "sub": "local"})
            session.setdefault("csrf", secrets.token_urlsafe(32))
        path = scope["path"]
        if session.get("user") or path.startswith(PUBLIC_PATHS):
            return await self.app(scope, receive, send)
        if scope["method"] == "GET":
            query = scope.get("query_string", b"").decode()
            target = path + (f"?{query}" if query else "")
            response = RedirectResponse(f"/login?next={quote(target)}", status_code=303)
        else:
            response = PlainTextResponse("Not signed in", status_code=401)
        await response(scope, receive, send)


class SecurityHeaders:
    """Conservative browser security headers on every response."""

    def __init__(self, app: ASGIApp, form_target: str = ""):
        self.app = app
        csp = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
               f"frame-ancestors 'none'; base-uri 'none'; form-action 'self' {form_target}")
        self.headers = [
            (b"content-security-policy", csp.strip().encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"same-origin"),
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + self.headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
