"""Tests for login-error handling in __init__.py: retry vs reauth.

Only a genuine Cognito NotAuthorizedException (a botocore ClientError with
that code) or an explicit `False` login result should force reauth. Anything
else raised while logging in -- a connect timeout to Cognito, an HTTP 400/5xx
from Emporia's own /customers endpoint, or some other botocore ClientError
code -- should raise ConfigEntryNotReady instead, so Home Assistant retries
automatically rather than pushing every user into reauth (see the
2026-09-16 Emporia outage referenced in docs/decisions.md, and upstream
issue #466 / PR #467).
"""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
import pytest
import requests

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.emporia_vue import _is_cognito_not_authorized
from custom_components.emporia_vue.const import DOMAIN


def _not_authorized_error() -> ClientError:
    return ClientError({"Error": {"Code": "NotAuthorizedException"}}, "InitiateAuth")


def _other_client_error() -> ClientError:
    return ClientError({"Error": {"Code": "TooManyRequestsException"}}, "InitiateAuth")


# --- _is_cognito_not_authorized (pure logic) -------------------------------


def test_is_cognito_not_authorized_true_for_not_authorized_code():
    """A ClientError with the NotAuthorizedException code is bad credentials."""
    assert _is_cognito_not_authorized(_not_authorized_error()) is True


def test_is_cognito_not_authorized_false_for_other_client_error_code():
    """A ClientError with any other code is treated as transient, not auth."""
    assert _is_cognito_not_authorized(_other_client_error()) is False


def test_is_cognito_not_authorized_false_for_non_client_error():
    """A non-ClientError (e.g. a plain timeout) is never treated as auth."""
    assert _is_cognito_not_authorized(TimeoutError("connect timeout")) is False


# --- async_setup_entry integration (retry vs reauth) ------------------------


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "hunter2"},
    )


async def test_not_authorized_exception_forces_reauth(hass: HomeAssistant) -> None:
    """A genuine Cognito NotAuthorizedException requires reauth, not a retry."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch("pyemvue.PyEmVue") as mock_cls,
        patch("custom_components.emporia_vue.PyEmVue", new=mock_cls),
    ):
        mock_cls.return_value.login.side_effect = _not_authorized_error()
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_false_login_result_forces_reauth(hass: HomeAssistant) -> None:
    """An explicit False login result also requires reauth."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch("pyemvue.PyEmVue") as mock_cls,
        patch("custom_components.emporia_vue.PyEmVue", new=mock_cls),
    ):
        mock_cls.return_value.login.return_value = False
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_other_client_error_retries_instead_of_reauth(hass: HomeAssistant) -> None:
    """A non-NotAuthorizedException ClientError retries instead of forcing reauth."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch("pyemvue.PyEmVue") as mock_cls,
        patch("custom_components.emporia_vue.PyEmVue", new=mock_cls),
    ):
        mock_cls.return_value.login.side_effect = _other_client_error()
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_connect_timeout_retries_instead_of_reauth(hass: HomeAssistant) -> None:
    """A connect timeout during login (the 2026-09-25 evidence) retries.

    This is the scenario from the outage evidence: a Cognito connect
    timeout previously turned into ConfigEntryAuthFailed via the old
    blanket `except Exception`, forcing reauth for an outage that had
    nothing to do with credentials.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch("pyemvue.PyEmVue") as mock_cls,
        patch("custom_components.emporia_vue.PyEmVue", new=mock_cls),
    ):
        mock_cls.return_value.login.side_effect = requests.exceptions.ConnectTimeout(
            "Connect timeout on endpoint URL"
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_http_400_after_login_retries_instead_of_reauth(hass: HomeAssistant) -> None:
    """An HTTP 400 from /customers after a successful Cognito login retries.

    This is the 2026-09-16 outage: Emporia's /customers endpoint returned
    HTTP 400 for ~2h40m, which the old code turned into ConfigEntryAuthFailed
    for every user.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    response = MagicMock()
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "400 Client Error", response=response
    )

    with (
        patch("pyemvue.PyEmVue") as mock_cls,
        patch("custom_components.emporia_vue.PyEmVue", new=mock_cls),
    ):
        mock_cls.return_value.login.side_effect = requests.exceptions.HTTPError(
            "400 Client Error", response=response
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
