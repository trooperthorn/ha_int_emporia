# Documentation index

This directory holds the explanations that used to live as comments in the
integration's code. Each file owns one kind of fact so a reader (human or
agent) knows where to look.

- [design.md](design.md): architecture and rationale for how entities are
  built, including the virtual and synthetic channels the integration adds
  on top of what the Emporia API reports.
- [protocol.md](protocol.md): Emporia API and device facts, verified versus
  unverified, including the derived Mains Import/Export split and the
  charger's eventual-consistency behavior.
- [api-reference.md](api-reference.md): the full Emporia cloud API surface —
  every known endpoint on both the legacy and modern hosts, what it returns,
  and whether this integration uses it. Each row is labelled with how it was
  verified.
- [reverse-engineering.md](reverse-engineering.md): how to investigate the
  Emporia API further — where to look, what the app binary can and cannot
  tell you, how to capture a write, and the traps that have already cost
  time. Read this before doing new API archaeology.
- [decisions.md](decisions.md): dated decisions, including the alternative
  rejected and why.
- [operations.md](operations.md): running the test suite locally, including
  the install order it depends on.
