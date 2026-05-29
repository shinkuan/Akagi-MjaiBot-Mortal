# Mortal

Deep-RL mahjong bot for Akagi v3. Wraps the precompiled `libriichi`
extension and a PyTorch policy. Optional online-inference mode delegates
`react_batch` to a remote API server (`http://server.akagiot.org/` by
default) and falls back to the local model on request failure.

Upstream: [Equim-chan/Mortal](https://github.com/Equim-chan/Mortal)
(AGPL-3.0). This directory contains a thin Akagi adapter (`bot.py`) and
the network/inference glue (`model.py`); both link against `libriichi`
in-process. Akagi runs the whole thing as a separate OS subprocess so
the AGPL boundary stops at the pipe — see `src/bot/README.md`.

---

## Layout

```
mjai_bot/mortal/
├── bot.py              # mjai JSONL stdin → stdout adapter
├── model.py            # network + inference + online-server client
├── pyproject.toml      # uv-resolved deps (torch, numpy, requests)
├── manifest.toml       # Akagi v3 settings schema (form definition)
├── settings.toml       # current values; written by Akagi, gitignored
├── mortal.pth          # model weights
├── libriichi.so        # current-OS Linux extension (used at import time)
├── libriichi.pyd       # current-OS Windows extension (used at import time)
├── libriichi/          # per-platform/per-Python-minor builds (manual swap)
│   ├── libriichi-3.10-x86_64-unknown-linux-gnu.so
│   ├── libriichi-3.11-x86_64-pc-windows-msvc.pyd
│   ├── libriichi-3.12-aarch64-apple-darwin.so
│   └── …
└── .akagi/             # runtime venv + resolved-settings JSON (managed by Akagi)
```

Python's import machinery picks `libriichi.{so,pyd}` from the bot's
working directory. The `libriichi/` subdirectory ships the same binary
built against multiple Python ABIs and CPU targets — copy the matching
file over `libriichi.so` / `libriichi.pyd` when running on a different
interpreter or arch. The bot does not auto-select.

---

## Settings

The manifest declares three knobs the frontend renders as a form. Edit
them via the **Bot settings** panel; values land in `settings.toml`.

| Key       | Type   | Default                       | Notes |
|-----------|--------|-------------------------------|-------|
| `online`  | bool   | `false`                       | Delegate `react_batch` to the API server below |
| `server`  | string | `http://server.akagiot.org/`  | Endpoint exposing `/react_batch` |
| `api_key` | string | `""` (secret)                 | Sent as the `Authorization` header on every request |

`api_key` is rendered as a password input and substituted with `***` in
Akagi's tracing logs. The value is still stored in plaintext under
`settings.toml`, which is gitignored — treat the file as a credential.

### Bot-side resolution order

`model.py::online_settings_init()` resolves at import time and again on
every `Bot.__init__()` (i.e. every `start_game`):

1. `AKAGI_BOT_CONFIG` env var → JSON dict written by Akagi from the
   manifest defaults merged with `settings.toml`. Recognised keys
   (`online`, `server`, `api_key`) override the in-module defaults;
   unknown keys are ignored.
2. `ot_settings.json` next to `model.py` — pre-v3 legacy fallback.
3. Hard-coded defaults at the top of `model.py`.

Source 1 wins when set, so the **Bot settings** panel is the single
source of truth post-v3. Failures on either source are logged to stderr
and the function continues with the prior values rather than crashing.

### When changes take effect

Editing settings does **not** restart the running subprocess. New values
are picked up on the next `start_game` event (and again at every
`start_game` after that).

### Custom meta

When `online = true`, Mortal attaches `{"online": <bool>}` to the
`meta` field of every `BotResponse`. The frontend can render this as a
status indicator. `online: false` here means "request failed, fell back
to local inference" — distinct from the *setting* `online: false` which
means "always use local". Other custom keys can be added without any
backend change since `meta` is a free-form JSON object.

### Connection-status toasts

On top of the per-response `meta.online` flag, Mortal pushes a toast to
the Akagi UI when the online-server connection *changes state* (via the
host's `@@AKAGI_NOTIFY@@` stderr notification protocol):

- **offline → online** — a *success* toast ("Connected to online
  server"). Fires on the first successful server call and again after any
  reconnect.
- **online → offline** — a *warn* toast ("Disconnected from online
  server" / falling back to the local model).

A steady state emits nothing — in particular, a first-ever *failed*
connection is silent. The transition logic and the notification helper
live in [`online_status.py`](online_status.py) (kept import-light so it's
unit-tested by `test_online_status.py` without loading torch).

---

## Install

### Option A — install from a release zip

The standard Akagi v3 path. Publishers produce a zip with `mortal/` as
the single top-level directory; the installer's `strip_single_top_level`
collapses it. Layout matches the section above (minus `.akagi/` and
`settings.toml`).

```ts
await invoke('install_bot_from_github', {
  repo: 'owner/mortal-release',
  assetGlob: 'mortal-v*.zip',
  name: 'mortal',
});
```

### Option B — manual

```
mjai_bot/mortal/        # create this directory
├── bot.py
├── model.py
├── pyproject.toml
├── manifest.toml
├── libriichi.so        # MacOS/Linux extension
├── libriichi.pyd       # Windows extension
├── mortal.pth
└── libriichi/          # optional, only if you need to swap ABIs
```

First spawn runs `uv sync` against `pyproject.toml`. The venv lives at
`.akagi/venv/`. Subsequent spawns are fast — sync is gated by an
`mtime:size` stamp at `.akagi/synced.stamp`.
