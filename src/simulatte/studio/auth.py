"""Shared loopback capability boundary for read-only Viewer and mutable Studio."""

from __future__ import annotations

import hmac
import json
import secrets
import threading

from starlette.responses import HTMLResponse, Response

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; frame-src 'self'"
)
# Saved analysis files: never a document with script, styles or subresources, even when navigated to.
ARTIFACT_CSP = "sandbox; default-src 'none'"
# The packaged figure frame runs only the local renderer and receives data by message, never by network;
# only the Studio document may embed it.
FRAME_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)


def response_headers():
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": CSP,
    }


def artifact_headers(media_type):
    """An isolated download: declared type, no sniffing, attachment, sandboxed if opened directly."""
    return {
        **response_headers(),
        "Content-Type": media_type,
        "Content-Disposition": "attachment",
        "Content-Security-Policy": ARTIFACT_CSP,
    }


class SessionAuth:
    def __init__(self, port: int, *, mode="view"):
        if mode not in ("view", "studio"):
            raise ValueError("Invalid application mode")
        self.mode = mode
        self.origin = f"http://127.0.0.1:{port}"
        self.host = f"127.0.0.1:{port}"
        self.secret = secrets.token_urlsafe(32)
        self.cookie_name = "simulatte-" + secrets.token_hex(8)
        self.cookie = secrets.token_urlsafe(32)
        self.token = ""
        self.lock = threading.Lock()

    def link(self) -> str:
        with self.lock:
            self.token = secrets.token_urlsafe(32)
            return f"{self.origin}/bootstrap?token={self.token}"

    def headers_valid(self, connection, *, require_origin=False, allow_null_origin=False):
        """``allow_null_origin`` is for packaged static GET/HEAD only: Firefox sends ``Origin: null`` when
        the sandboxed figure frame (opaque origin) loads its classic script; never for commands or state."""
        hosts = connection.headers.getlist("host")
        origins = connection.headers.getlist("origin")
        if hosts != [self.host]:
            return False
        if allow_null_origin and origins == ["null"]:
            return not require_origin
        return origins == [self.origin] or not origins and not require_origin

    def cookie_valid(self, connection):
        return hmac.compare_digest(connection.cookies.get(self.cookie_name, "").encode(), self.cookie.encode())

    def authenticated(self, connection, *, secret=None):
        if secret is None:
            values = connection.headers.getlist("x-simulatte-session")
            if len(values) != 1:
                return False
            secret = values[0]
        return (
            type(secret) is str
            and self.cookie_valid(connection)
            and hmac.compare_digest(secret.encode(), self.secret.encode())
        )

    def bootstrap(self, request):
        headers = response_headers()
        tokens = request.query_params.getlist("token")
        token = tokens[0] if len(tokens) == 1 else ""
        with self.lock:
            accepted = bool(self.token and hmac.compare_digest(token.encode(), self.token.encode()))
            if accepted and request.method == "GET":
                self.token = ""
            else:
                accepted = False
        if not accepted:
            return Response(
                "Link unavailable. Enter link in the serving terminal, or restart with --no-browser.",
                status_code=403,
                headers=headers,
            )
        nonce = secrets.token_urlsafe(24)
        headers["Content-Security-Policy"] = (
            f"default-src 'none'; img-src data:; script-src 'nonce-{nonce}'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        body = (
            '<!doctype html><meta charset="utf-8"><title>Simulatte</title>'
            '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22/%3E">'
            '<p id="message">Opening Simulatte…</p>'
            f'<script nonce="{nonce}">try {{const key={json.dumps(self.secret)};'
            'sessionStorage.setItem("simulatte-session",key);'
            f'sessionStorage.setItem("simulatte-mode",{json.dumps(self.mode)});'
            'if(sessionStorage.getItem("simulatte-session")!==key)throw Error();'
            'location.replace("/");}catch{document.getElementById("message").textContent='
            '"Browser storage unavailable. Enable session storage, then enter link in the serving terminal '
            'or restart with --no-browser.";}</script>'
        )
        result = HTMLResponse(body, headers=headers)
        result.set_cookie(self.cookie_name, self.cookie, httponly=True, samesite="strict", path="/")
        return result
