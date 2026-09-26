# PyEmVue to internal requests client: migration plan

Status: planning only, not started. Branch: `PyEmVue-migrate`.

## Goal

Replace the `pyemvue` pip dependency with an internal HTTP client
(`api_client.py`) so the integration owns its auth and API calls directly.

## What PyEmVue does today

- Authenticates against Emporia's AWS Cognito user pool using SRP
  (Secure Remote Password), not a plain username/password POST.
- Exchanges the resulting Cognito ID/access/refresh tokens as bearer auth
  on Emporia's REST API.
- Exposes device list, device list usage (day/month/minute intervals), and
  account/customer info as REST calls once authenticated.
- Internally retries certain calls (for example when channel usage comes
  back as `None`) before returning to the caller.

## Call sites to change

- `config_flow.py`: initial authentication during setup, currently
  constructs a `pyemvue.Vue` and calls its login method.
- `coordinator.py`: day/month/minute usage fetches, currently call methods
  on the shared `Vue` instance via `runtime`.
- Anywhere else importing `pyemvue` (grep `import pyemvue` / `from pyemvue`
  across the repo before starting).

## New module shape

- `api_client.py`: owns the Cognito-authenticated session and the REST
  calls (device list, device list usage, account info). Should expose an
  interface close enough to today's `Vue` usage that `coordinator.py` and
  `config_flow.py` changes stay mechanical.
- Preserve the existing "retry once on `None` channel usage" behavior
  explicitly in the new client; today it is implicit in PyEmVue.

## Highest-risk piece

Reimplementing Cognito SRP auth by hand is real cryptographic protocol
work, not a simple HTTP call. Recommendation: keep a small, dedicated
Cognito SRP helper library (for example `pycognito`) for just the auth
step rather than hand-rolling the math, and layer plain `requests` calls
on top of the tokens it returns. A subtle bug in a hand-rolled SRP flow
fails silently or intermittently, which is worse than today's dependency.

Whichever helper backs the new `api_client.py` auth step, it must carry the
fix in `custom_components/emporia_vue/pycognito_compat.py` (see
docs/decisions.md, 2026-09-26 entry): pycognito 2024.5.1 raises
`TypeError: Cannot convert str to buffer` on Python 3.14 when verifying a
Cognito Hosted-UI ID token's `at_hash` claim, which breaks Google/Apple
token-based login. If the new client keeps using `pycognito` directly, port
the same scoped subclass patch (or drop it once
NabuCasa/pycognito#339 ships). If the new client verifies tokens itself
instead of delegating to `pycognito`, implement `at_hash` verification
correctly from the start: hash the ASCII access token bytes with the ID
token's signing algorithm, take the left half of the digest, and base64url
encode it without padding, per the OIDC Core spec's `at_hash` definition.
Either way, keep the "reject a token whose at_hash doesn't match" test case
that `tests/test_pycognito_compat.py` exercises now.

## Testing impact

Existing tests mock `pyemvue.Vue`; all of those mocks need to be rewritten
against the new client's interface once it exists.

## Suggested sequencing

1. Land `api_client.py` with auth only, behind a feature-inert refactor
   (still calling the same PyEmVue-backed behavior) to validate the
   Cognito flow in isolation.
2. Port device list and usage endpoints one at a time, keeping tests green
   after each.
3. Remove the `pyemvue` requirement only once every call site is migrated
   and the full test suite passes against the new client.
4. Treat this as its own PR, separate from any other bug fix or feature
   work in this repo.
