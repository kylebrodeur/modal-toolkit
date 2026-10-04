"""U1: the fleet dashboard's session auth, lifted from the inference
dashboard's battle-tested machinery (issued-timestamp + HMAC cookie).

Differences from the inference version are naming only: the credential
token comes from `MODAL_TOOLKIT_DASHBOARD_TOKEN`, the cookie is
`modal_toolkit_dashboard_session`, and every route lives under
`/_toolkit/...`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets as pysecrets
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

COOKIE_NAME = "modal_toolkit_dashboard_session"
TOKEN_ENV = "MODAL_TOOLKIT_DASHBOARD_TOKEN"
SESSION_MAX_AGE = 7 * 24 * 3600


def dashboard_login_html() -> str:
    # Hash-fragment autologin (ported verbatim): `#key=<credential>` is filled
    # and submitted by JS, then stripped; the credential never lands in
    # browsing history via query params.
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toolkit Dashboard Login</title>
<style>
body{font:16px system-ui,sans-serif;max-width:28rem;margin:5rem auto;padding:0 1rem;background:#101218;color:#edf0f7}
main{padding:2rem;border:1px solid #303746;border-radius:10px;background:#171b24}
input,button{font:inherit;padding:.7rem;width:100%;box-sizing:border-box;margin-top:.7rem}
button{cursor:pointer}
</style></head>
<body><main><h1>Modal Toolkit Dashboard</h1><p>Sign in to view private fleet usage.</p>
<form method="post" action="/_toolkit/login"><label>Dashboard credential
<input name="credential" type="password" autocomplete="current-password" required></label>
<button type="submit">Sign in</button></form>
<script>
(function(){
  var m = /^#key=(.+)$/.exec(window.location.hash);
  if (!m) return;
  var input = document.querySelector('input[name=credential]');
  input.focus(); input.value = decodeURIComponent(m[1]);
  history.replaceState(null, '', window.location.pathname + window.location.search);
  document.querySelector('form').submit();
})();
</script>
</main></body></html>"""


class Session:
    """Issued-timestamp + HMAC session over one dashboard token."""

    def __init__(self, token: str, cookie_name: str = COOKIE_NAME, max_age: int = SESSION_MAX_AGE):
        self.token = token
        self.cookie_name = cookie_name
        self.max_age = max_age

    def value(self) -> str:
        issued = str(int(time.time()))
        signature = hmac.new(self.token.encode(), issued.encode(), hashlib.sha256).hexdigest()
        return f"{issued}.{signature}"

    def valid(self, request: Request) -> bool:
        if not self.token:
            return False
        raw = request.cookies.get(self.cookie_name, "")
        issued, separator, supplied = raw.partition(".")
        if not separator or not issued.isdigit() or time.time() - int(issued) > self.max_age:
            return False
        expected = hmac.new(self.token.encode(), issued.encode(), hashlib.sha256).hexdigest()
        return pysecrets.compare_digest(supplied, expected)

    def authorized(self, request: Request) -> bool:
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {self.token}" if self.token else ""
        return self.valid(request) or (bool(self.token) and hmac.compare_digest(supplied, expected))


def mount_auth(app: FastAPI, session: Session) -> None:
    """Attach /_toolkit/login|logout to the fleet ASGI app."""

    @app.get("/_toolkit/login")
    async def login_page() -> Response:
        return Response(content=dashboard_login_html(), media_type="text/html")

    @app.post("/_toolkit/login")
    async def login(request: Request) -> Response:
        body = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        supplied = body.get("credential", [""])[0]
        if not session.token or not hmac.compare_digest(supplied, session.token):
            return Response(content=dashboard_login_html(), status_code=401, media_type="text/html")
        response = RedirectResponse("/_toolkit", status_code=303)
        response.set_cookie(
            session.cookie_name, session.value(), max_age=session.max_age, httponly=True, secure=True, samesite="lax"
        )
        return response

    @app.get("/_toolkit/logout")
    async def logout() -> Response:
        response = RedirectResponse("/_toolkit/login", status_code=303)
        response.delete_cookie(session.cookie_name)
        return response


def session_from_env() -> Session:
    return Session(token=os.getenv(TOKEN_ENV, "").strip())
