# Conventions

_Moved from the old CLAUDE.md ("Conventions")._

- Type hints everywhere; `pydantic` models for config and API payloads.
- Format with `ruff format`, lint with `ruff check`. Run both before
  finishing any change.
- Async I/O for the HTTP client and poller (`httpx.AsyncClient`).
- Log with the stdlib `logging` module to stdout; systemd captures it.
  Never log an API key or full raw responses at INFO.
- Frontend JS: ES modules, no bundler, no dependencies. Any web font is
  self-hosted in `web/static/fonts`, never a CDN, so the board works with no
  internet; the others fall back to system fonts (`fonts/README.md`).
- Keep the Pi in mind: avoid heavy Python deps (no pandas, no numpy),
  and avoid CSS filters that force full-screen repaints every frame. Animate
  transform/opacity only, or draw on a canvas.
- Every config option has a sensible default in `config.example.yaml`
  and a one-line comment explaining it.
- `/static` and both pages answer with `Cache-Control: no-cache`. The kiosk is
  never hard-refreshed, so nothing may sit in its cache without being checked
  with us first; the ETag makes that a 304.
- `sources`, `schedule`, `updates`, `weather` and `display.resolution` are
  plumbing: never profile-overridable.
- A feature lands with its docs: `config.example.yaml`, README (user-facing),
  this file (if it changes the current state or adds a rule) and a new note in
  `docs/design/` (the why, what was measured, what was verified by hand).
