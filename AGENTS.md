# Echo Reader repository rules

This repository is a monorepo containing two independently runnable components:

- `components/medialab`: produces normalized library items.
- `components/echo`: reads and edits normalized library items.

Keep their boundary explicit. The shared filesystem contract under `LIBRARY_ROOT` is the data plane; Echo's `/api/v1` HTTP API is the control plane. Do not import Echo internals from MediaLab or MediaLab internals from Echo.

Project-wide integration code belongs in `src/echo_reader`. Component-specific changes belong in that component and must keep its standalone commands working.

Never commit runtime media, real library data, cookies, secrets, databases, virtual environments, dependency directories, or build outputs. Use the checked-in examples and temporary test directories instead.

Before changing dependencies, inspect the relevant manifest and lockfile. Use mise for pinned tool versions, uv for Python, Bun for JavaScript/TypeScript, and Cargo for Rust. Run the focused tests first and `scripts/verify.ps1` before handoff when practical.

The additional rules in `components/medialab/AGENTS.md` apply within that component.

Use `docs/HANDOFF.md` as the current cross-project handoff and `docs/README.md` as the
documentation authority map. Component `HANDOFF`, `DELIVERY`, `ROADMAP`, `LESSONS`,
`DECISIONS`, and `PROJECT_PLAN` files contain historical evidence; do not delete their
problem/solution history or treat old snapshots as current workspace status.

Never run broad ignored-file cleanup such as `git clean -X`: the ignored root `library/`
contains user data. Runtime and tests must use their configured temporary directory and
must clean up any Cookie/session files they create.
