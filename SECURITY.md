# Security Policy

## Supported versions

Security fixes are provided for the default branch and the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
private vulnerability reporting ("Security" tab → "Report a vulnerability"):
<https://github.com/PakhomLeo/echo-reader/security/advisories/new>.
Include reproduction steps, affected versions or commits, impact, and any
suggested mitigation.

You should receive an acknowledgement within seven days. Please allow time for
investigation and a coordinated fix before public disclosure.

## Threat model

Echo Reader is a **single-user, local** application. It is not designed to be exposed to a
network or shared between users.

- The Rust gateway (default `127.0.0.1:3000`), the import service (default `127.0.0.1:3100`) and
  the SessionBridge (`127.0.0.1:19876`) bind to loopback. The gateway rejects a non-loopback
  `ECHO_BIND` and the import service rejects a non-loopback `--host`.
- Both HTTP services reject requests whose `Host` header is not a loopback address (DNS
  rebinding) and reject state-changing requests with `Sec-Fetch-Site: cross-site` or an `Origin`
  that does not match the host.
- The installed desktop app additionally requires a random per-launch session cookie
  (HttpOnly, SameSite=Strict) for every page, asset and API call, and a separate internal token
  (`ECHO_INTERNAL_TOKEN`) between the gateway and the private import service. Never share or log
  these tokens.
- In **source/development mode there is no per-request token**: any local process of the same
  user can call the APIs. Do not run development mode on a shared multi-user machine.

## Data handling

- Browser-session cookies stay in process memory and are never written to the content library.
  Never commit `config/secrets.toml`, `.env` files, exported cookie files, `.runtime/` databases,
  or library content containing private data.
- Cloud features are opt-in: online ASR uploads audio to your OSS bucket, cloud OCR uploads
  item images (`[ocr] allow_cloud = true`, HTTPS endpoint only), and LLM clean-up sends transcript
  text to the configured provider.
- Model weights are downloaded at runtime into `.runtime/` or the user cache, pinned by revision
  or SHA-256, and never committed. Remote model code (`trust_remote_code`) is disabled by default.
- Treat captured web content, transcripts and model output as untrusted data.
