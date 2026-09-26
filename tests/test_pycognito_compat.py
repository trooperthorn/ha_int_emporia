"""Tests for the pycognito at_hash verification compatibility shim.

Builds a real RS256 JWK/JWT pair locally (no network) and drives
pycognito's own ``Cognito.verify_token`` and this integration's patched
``_EmporiaCompatCognito.verify_token`` against it, so these tests fail
against the unpatched pycognito 2024.5.1 on Python 3.14 and pass with the
shim applied, without weakening what gets verified.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pycognito import Cognito
from pycognito.exceptions import TokenVerificationException

from custom_components.emporia_vue.pycognito_compat import (
    _EmporiaCompatCognito,
    _at_hash_hashing_is_broken,
)

USER_POOL_ID = "us-east-2_ghlOXVLi1"
CLIENT_ID = "4qte47jbstod8apnfic0bunmrq"
USER_POOL_URL = f"https://cognito-idp.us-east-2.amazonaws.com/{USER_POOL_ID}"
KID = "test-key"


@pytest.fixture(scope="module")
def rsa_keypair():
    """A throwaway RSA keypair used to sign and verify test tokens."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_id_token(
    private_key,
    access_token: str,
    *,
    correct_at_hash: bool = True,
) -> str:
    """Build a signed ID token carrying an at_hash claim for access_token."""
    if correct_at_hash:
        digest = hashlib.sha256(access_token.encode("ascii")).digest()
        at_hash = (
            base64.urlsafe_b64encode(digest[: len(digest) // 2])
            .rstrip(b"=")
            .decode("ascii")
        )
    else:
        at_hash = "not-the-right-hash-value"

    payload = {
        "aud": CLIENT_ID,
        "iss": USER_POOL_URL,
        "token_use": "id",
        "at_hash": at_hash,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=5),
    }
    return jwt.encode(
        payload, private_key, algorithm="RS256", headers={"kid": KID}
    )


def _cognito_client(cls, public_key, access_token: str):
    """Build a Cognito-family client with a canned JWKS and access token."""
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    public_jwk["kid"] = KID
    # Dummy static credentials so boto3 client construction never touches
    # the network (no IMDS/credential-provider lookups) under the test
    # harness's socket block.
    client = cls(
        USER_POOL_ID,
        CLIENT_ID,
        user_pool_region="us-east-2",
        access_key="testing",
        secret_key="testing",
    )
    client.pool_jwk = {"keys": [public_jwk]}
    client.access_token = access_token
    client.client = MagicMock()
    return client


def test_at_hash_hashing_is_broken_probe_matches_reality() -> None:
    """The runtime probe used to gate the shim reflects this environment.

    On Python 3.14 with pycognito 2024.5.1 / PyJWT installed here,
    hashing a str access token raises TypeError, so the probe must
    report True (the shim is needed). This pins the probe to the actual
    installed library behaviour rather than a version string, so the
    shim self-disables the day pycognito ships NabuCasa/pycognito#339.
    """
    assert _at_hash_hashing_is_broken() is True


def test_unpatched_pycognito_fails_to_verify_at_hash_token(rsa_keypair) -> None:
    """Reproduces the bug: stock pycognito 2024.5.1 raises TypeError.

    This is the failure this whole module exists to work around. If this
    test starts failing (i.e. stock pycognito stops raising here), the
    shim has become unnecessary; see docs/decisions.md.
    """
    private_key, public_key = rsa_keypair
    access_token = "example-hosted-ui-access-token"
    id_token = _make_id_token(private_key, access_token)

    client = _cognito_client(Cognito, public_key, access_token)

    with pytest.raises(TypeError):
        client.verify_token(id_token, "id_token", "id")


def test_patched_cognito_verifies_correct_at_hash(rsa_keypair) -> None:
    """The shim accepts a token whose at_hash genuinely matches."""
    private_key, public_key = rsa_keypair
    access_token = "example-hosted-ui-access-token"
    id_token = _make_id_token(private_key, access_token)

    client = _cognito_client(_EmporiaCompatCognito, public_key, access_token)

    claims = client.verify_token(id_token, "id_token", "id")

    assert claims["token_use"] == "id"
    assert client.id_token == id_token


def test_patched_cognito_still_rejects_wrong_at_hash(rsa_keypair) -> None:
    """The shim must not weaken verification: a bad at_hash is still rejected."""
    private_key, public_key = rsa_keypair
    access_token = "example-hosted-ui-access-token"
    id_token = _make_id_token(private_key, access_token, correct_at_hash=False)

    client = _cognito_client(_EmporiaCompatCognito, public_key, access_token)

    with pytest.raises(
        TokenVerificationException, match="at_hash claim does not match"
    ):
        client.verify_token(id_token, "id_token", "id")


def test_apply_pycognito_compat_only_rebinds_pyemvue_auth_cognito() -> None:
    """The patch is scoped to pyemvue.auth.Cognito, not pycognito globally."""
    import pyemvue.auth as pyemvue_auth

    import custom_components.emporia_vue.pycognito_compat as compat_module

    original = pyemvue_auth.Cognito
    original_applied = compat_module._APPLIED
    # Other tests (e.g. config flow tests) may have already triggered the
    # module-level "apply once" guard; reset it so this test exercises a
    # real application regardless of run order.
    compat_module._APPLIED = False
    try:
        compat_module.apply_pycognito_compat()
        assert pyemvue_auth.Cognito is _EmporiaCompatCognito
        assert issubclass(pyemvue_auth.Cognito, Cognito)
    finally:
        pyemvue_auth.Cognito = original
        compat_module._APPLIED = original_applied

    # pycognito's own class is untouched by the scoped patch.
    assert Cognito.verify_token is not _EmporiaCompatCognito.verify_token
