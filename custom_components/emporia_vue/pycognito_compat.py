"""Compatibility shim for pycognito's Hosted-UI at_hash verification.

pycognito 2024.5.1 (the latest release as of writing; there has been no
release since 2024-05, see the open fix at
https://github.com/NabuCasa/pycognito/pull/339) verifies the OIDC
``at_hash`` claim on a Cognito Hosted-UI ID token by hashing the raw
``access_token`` string:

    alg_obj = jwt.get_algorithm_by_name(header["alg"])
    digest = alg_obj.compute_hash_digest(self.access_token)
    at_hash = base64.urlsafe_b64encode(digest[: (len(digest) // 2)]).rstrip("=")

On Python 3.14, PyJWT's ``compute_hash_digest`` requires a bytes-like
object and raises ``TypeError: Cannot convert str to buffer`` when given a
``str``. This only happens for ID tokens that carry an ``at_hash`` claim,
which Cognito Hosted-UI tokens do (the tokens used by this integration's
"paste your Google/Apple tokens" auth method, see AUTH_METHOD_TOKENS in
config_flow.py). Plain username/password login does not go through Cognito
Hosted UI and is unaffected.

Upstream reports and fixes:
- magico13/ha-emporia-vue#454, #439 (the failure as seen in this
  integration's ha-emporia-vue ancestor)
- magico13/ha-emporia-vue#461 (scoped fix: patches only the ``Cognito``
  class that PyEmVue's own ``pyemvue.auth`` module instantiates)
- magico13/ha-emporia-vue#458 (broader fix: monkeypatches
  ``jwt.algorithms.Algorithm.compute_hash_digest`` and
  ``base64.urlsafe_b64encode`` process-wide for the duration of
  ``Cognito.verify_token``)
- sgorilla/ha-emporia-vue ships a similar ``pycognito_compat.py`` shim
- NabuCasa/pycognito#339 (the real fix, unreleased): encode the access
  token to UTF-8 bytes before hashing, and decode the resulting at_hash
  back to ``str`` before comparing it to the claim (which PyJWT decodes as
  ``str``, not ``bytes``)

This module takes the #461-style scoped approach rather than #458's
process-wide monkeypatch: it replaces only ``pyemvue.auth.Cognito`` (the
name PyEmVue's ``Auth.__init__`` calls to build its own Cognito client) with
a subclass whose ``verify_token`` is otherwise identical to pycognito
2024.5.1 but fixes the at_hash computation per PR #339. It never touches
``pycognito.Cognito`` itself, so any other integration or library sharing
this Home Assistant's pycognito install is unaffected, and nothing here
weakens verification: a correct at_hash still passes, a mismatched or
missing one is still rejected exactly as before.

The patch is applied once, only if a runtime probe shows the underlying
str-hashing bug is actually present (rather than trusting a pycognito
version pin, which would silently stop protecting anything the day a fixed
release ships under an unpredictable version number). Once pycognito ships
the PR #339 fix, the probe below stops raising ``TypeError`` and
``apply_pycognito_compat()`` becomes a no-op. Deleting this module and its
call site is safe at any point after that; nothing else in the integration
depends on it existing.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import jwt
from pycognito import Cognito
from pycognito.exceptions import TokenVerificationException

_LOGGER = logging.getLogger(__name__)

_PROBE_ACCESS_TOKEN = "at-hash-compat-probe"


def _at_hash_hashing_is_broken() -> bool:
    """Return True if hashing a str access token raises TypeError.

    This exercises the exact call pycognito 2024.5.1 makes
    (``alg_obj.compute_hash_digest(self.access_token)`` where
    ``access_token`` is a ``str``), without touching any network or real
    token. If a future PyJWT/cryptography or pycognito release stops
    raising here, the shim below is skipped automatically.
    """
    try:
        alg_obj = jwt.get_algorithm_by_name("RS256")
        alg_obj.compute_hash_digest(_PROBE_ACCESS_TOKEN)
    except TypeError:
        return True
    except Exception:  # pylint: disable=broad-except
        # Unexpected failure mode: be conservative and patch anyway so a
        # real login attempt fails loudly with pycognito's own error
        # rather than silently skipping verification.
        _LOGGER.debug(
            "Unexpected error probing pycognito at_hash hashing; applying "
            "compatibility shim defensively",
            exc_info=True,
        )
        return True
    return False


class _EmporiaCompatCognito(Cognito):
    """pycognito's Cognito client with a fixed at_hash verification step.

    Everything except the at_hash block is copied unchanged from
    pycognito 2024.5.1's ``Cognito.verify_token`` so behaviour otherwise
    matches exactly (unverified claims still raise TokenVerificationException,
    the same required claims are enforced, iat is still checked).
    """

    def verify_token(
        self, token: str, id_name: str, token_use: str
    ) -> dict[str, Any]:
        kid = jwt.get_unverified_header(token).get("kid")
        hmac_key = jwt.api_jwk.PyJWK(self.get_key(kid)).key
        required_claims = (["aud"] if token_use != "access" else []) + [
            "iss",
            "exp",
        ]
        try:
            decoded = jwt.api_jwt.decode_complete(
                token,
                hmac_key,
                algorithms=["RS256"],
                audience=self.client_id if token_use != "access" else None,
                issuer=self.user_pool_url,
                options={
                    "require": required_claims,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as err:
            raise TokenVerificationException(
                f"Your {id_name!r} token could not be verified ({err})."
            ) from None
        verified, header = decoded["payload"], decoded["header"]

        if verified.get("token_use") != token_use:
            raise TokenVerificationException(
                f"Your {id_name!r} token use ({token_use!r}) could not be verified."
            )

        if (iat := verified.get("iat")) is not None:
            try:
                int(iat)
            except ValueError as exception:
                raise TokenVerificationException(
                    f"Your {id_name!r} token's iat claim is not a valid integer."
                ) from exception

        if "at_hash" in verified:
            alg_obj = jwt.get_algorithm_by_name(header["alg"])
            access_token = self.access_token
            if isinstance(access_token, str):
                access_token = access_token.encode("utf-8")
            digest = alg_obj.compute_hash_digest(access_token)
            at_hash = (
                base64.urlsafe_b64encode(digest[: (len(digest) // 2)])
                .rstrip(b"=")
                .decode("ascii")
            )
            if at_hash != verified["at_hash"]:
                raise TokenVerificationException(
                    "at_hash claim does not match access_token."
                )

        setattr(self, id_name, token)
        setattr(self, f"{token_use}_claims", verified)
        return verified


_APPLIED = False


def apply_pycognito_compat() -> None:
    """Point pyemvue.auth at the patched Cognito class, if the bug is present.

    This only rebinds the name ``pyemvue.auth.Cognito`` (which PyEmVue's
    ``Auth.__init__`` calls to construct its client); it does not modify
    ``pycognito.Cognito`` itself, so other consumers of pycognito are
    unaffected. Safe to call more than once or from multiple modules.
    """
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    if not _at_hash_hashing_is_broken():
        _LOGGER.debug(
            "pycognito at_hash hashing looks fixed upstream; "
            "skipping compatibility shim"
        )
        return

    import pyemvue.auth as pyemvue_auth

    if pyemvue_auth.Cognito is Cognito:
        pyemvue_auth.Cognito = _EmporiaCompatCognito
        _LOGGER.debug(
            "Applied pycognito at_hash compatibility shim for PyEmVue's "
            "Cognito client"
        )
    elif pyemvue_auth.Cognito is not _EmporiaCompatCognito:
        _LOGGER.debug(
            "pyemvue.auth.Cognito is not the class this shim expects "
            "(%s); leaving it alone",
            pyemvue_auth.Cognito,
        )
