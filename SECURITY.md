# Security Policy

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, private
addresses, or logs. Use GitHub's private vulnerability-reporting feature for
this repository. If private reporting is unavailable, open a minimal issue
asking the maintainer to establish a private channel; omit technical details.

Include the affected version/commit, prerequisites, impact, a minimal
reproduction, and suggested remediation. Remove tokens, API keys, cookies,
camera images, usernames, and private network details.

## Response targets

These are project targets, not an SLA: acknowledge critical/high reports in
three business days, establish severity and containment in seven, and publish
a coordinated fix/advisory as soon as safely validated. Lower-severity issues
are prioritized by exploitability and impact.

## Supported version

Only the latest published release and the default branch receive security
fixes. Operators should update Home Assistant and this integration promptly
and retain a tested rollback/backup.

## Security boundaries

This integration stores an Emporia account's email/password or OAuth tokens
in the Home Assistant config entry, and polls the Emporia cloud API on the
account's behalf. It has no elevated access to Home Assistant beyond a
normal custom integration and cannot isolate itself from other integrations
running in the same Python process. A compromise of the Home Assistant
instance exposes the stored credentials the same way it would for any other
integration's config entry.

