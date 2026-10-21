# Security Policy

## Supported versions

Security fixes are provided for the default branch and the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
private vulnerability reporting ("Security" tab → "Report a vulnerability").
Include reproduction steps, affected versions or commits, impact, and any
suggested mitigation.

You should receive an acknowledgement within seven days. Please allow time for
investigation and a coordinated fix before public disclosure.

## Deployment guidance

- All services bind to loopback (`127.0.0.1`) by default. Do not expose the
  Echo API (3000), the import service (3100) or the SessionBridge (19876) to a
  network; they are designed for a single local user.
- The installed app protects internal routes with a per-install token
  (`ECHO_INTERNAL_TOKEN`); never share or log it.
- Browser-session cookies stay in process memory and are never written to the
  content library. Never commit `config/secrets.toml`, `.env` files, exported
  cookie files, `.runtime/` databases, or library content containing private data.
- Model weights and caches are downloaded at runtime into `.runtime/` or the user
  cache and must not be committed.
- Treat captured web content, transcripts and model output as untrusted data.
