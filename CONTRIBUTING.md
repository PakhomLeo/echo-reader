# Contributing to Echo Reader

Thanks for your interest in improving Echo Reader. Bug reports, documentation fixes, new site
adapters and tests are all welcome.

## Before you start

- For anything larger than a small fix, open an
  [issue](https://github.com/PakhomLeo/echo-reader/issues) first so we can agree on the approach.
- Security problems must **not** be reported in public issues; see [SECURITY.md](SECURITY.md).
- By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).

## Development setup

Tools: Python 3.12 + [uv](https://docs.astral.sh/uv/), stable Rust with `rustfmt`/`clippy`,
[Bun](https://bun.sh/), Node.js ≥ 22 (for Vitest) and FFmpeg. `mise install` installs the
versions pinned in `mise.toml`.

```bash
uv sync                                         # Python workspace (root + components/medialab)
(cd components/echo/frontend && bun install --frozen-lockfile)
CARGO_BUILD_JOBS=2 bash scripts/verify.sh       # the full gate; scripts/verify.ps1 on Windows
```

See [docs/development.md](docs/development.md) for running the services and building the Windows
package.

## Repository rules

- **Respect the component boundary.** `components/medialab` (producer) and `components/echo`
  (reader) never import each other's code. They communicate only through the library file format
  and the HTTP API. Integration code belongs in `src/echo_reader`.
- **Never commit runtime data**: real library content, media, cookies, `secrets.toml`, `.env`
  files, databases, model weights, virtual environments, `node_modules` or build output. Use
  `components/echo/library.example` and temporary directories in tests.
- **Do not add real URLs, account names or personal data** to tests, fixtures or docs. Use
  `example.test`-style hosts and synthetic IDs.
- **Keep optional features optional.** Heavy or copyleft dependencies (FunASR/PyTorch,
  Kokoro/eSpeak NG, RapidOCR, OpenVINO) stay behind extras, are imported lazily, and must never be
  bundled into the Windows package. A missing optional dependency must degrade to
  `503 feature_disabled`, not break other features.
- **No remote code by default.** Do not enable `trust_remote_code`, `--extra-index-url`, or
  unpinned model downloads. Pin model revisions or SHA-256 digests.
- **Keep the local security boundary.** Services listen on loopback only and must keep the Host,
  Origin and token checks described in [docs/architecture.md](docs/architecture.md).
- Before changing dependencies, update the matching lockfile (`uv.lock`, `Cargo.lock`, `bun.lock`)
  and check the new license against [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Coding style

- Python: type hints, small functions, `pytest` tests next to the component
  (`tests/`, `components/medialab/tests/`).
- Rust: `cargo fmt` and `cargo clippy --all-targets -- -D warnings` must be clean.
- TypeScript: `bun run typecheck` and `bun run lint` (oxlint) must be clean; add Vitest tests for
  UI behavior.
- Code comments and identifiers are in English; user-facing UI text is currently Chinese.

## Pull requests

1. Create a branch from `main` and keep the change focused.
2. Add or update tests; run `scripts/verify.sh` (or `verify.ps1`) and make sure it passes.
3. Update the relevant docs (`README.md` / `README.zh-CN.md`, `docs/`, component docs).
4. Fill in the pull request template, including how you tested the change.

Site adapters depend on third-party page structures. When you fix one, include a minimal,
anonymized fixture rather than a saved copy of a real page.
