# Oculus for Windows

A Windows desktop app that brings UniMelb coursework from Canvas, Ed
Discussion and Echo360 into a searchable local library. Built with Tauri 2,
React, Rust and a Python parsing service.

This repository maintains the Windows port of
[Tchanwangsa/oculus](https://github.com/Tchanwangsa/oculus). The imported branch
and commit are recorded in [upstream.json](upstream.json); Windows adaptations
are maintained here separately from the macOS source.

## Features

- **Coursework sync:** pages, downloads, modules, assignments, announcements
  and discussion threads, with incremental downloads and sync history.
- **Personal uploads:** add files to a subject using the native picker or
  drag and drop. Word, PowerPoint and Excel documents use LibreOffice to join
  the same PDF viewing, parsing and search pipeline.
- **Local document processing:** fast text extraction followed by background
  MinerU layout, formula and OCR processing. Optional MinerU cloud parsing is
  configured separately.
- **Page-image search:** Qwen3-VL embeddings preserve information in formulas,
  diagrams and slide layouts.
- **Agent chat:** native Codex CLI or Claude Code through WSL2, with streamed
  responses and access to coursework through the bundled `oculus` CLI.
- **Lectures and planning:** Echo360 playback, lecture chapters, a calendar,
  and project boards, tables and timelines.

Canvas sign-in uses the university's normal SSO flow. Optional automatic
sign-in stores credentials in Windows Credential Manager; a per-user Windows
scheduled task can keep the session alive while Oculus is closed.

## Build and run

### Prerequisites

- Windows x64 with the Microsoft Edge WebView2 runtime.
- [Bun](https://bun.sh), stable [Rust](https://rustup.rs) with the
  `x86_64-pc-windows-msvc` toolchain, and Visual Studio C++ Build Tools with
  the Windows SDK.
- [sccache](https://github.com/mozilla/sccache), required by the checked-in
  Cargo configuration, and [uv](https://docs.astral.sh/uv/) for Python.
- LibreOffice for Word, PowerPoint and Excel conversion. Oculus also accepts
  an `OCULUS_SOFFICE` environment variable pointing to the executable.
- Several GB of free space for Python dependencies and model weights.
  The Windows dependency lock uses CUDA 13 PyTorch wheels; local parsing was
  validated on an NVIDIA RTX 4070. See [validation](docs/windows-validation.md)
  for the tested configuration and limits.

Use Bun for this project; `app/bun.lock` is the frontend lockfile.

```powershell
git clone https://github.com/Tung-Phan-Dinh/oculus_window.git
cd oculus_window/app
bun install --frozen-lockfile
bun run ffmpeg
bun run prepare-sidecar
bun run stage-cli

cd ../sidecar
uv sync --frozen --python 3.12

cd ../app
bun run tauri dev
```

For an installable release, run from `app/`:

```powershell
bun run tauri build
```

The installer is written to
`app/src-tauri/target/release/bundle/nsis/Oculus_0.1.0_x64-setup.exe`.
It includes the desktop app, the `oculus.exe` CLI, ffmpeg, Python service
sources and uv. First launch prepares the managed Python environment;
model weights download when needed. Setup progress appears under
**Settings → Library**.

The desktop executable is `app.exe`; `oculus.exe` is the command-line tool.
Current builds are unsigned. Distribution to machines enforcing Smart App
Control or organizational application policies requires trusted code signing.

See [Windows setup and packaging](docs/windows.md) for details.

## Claude Code through WSL2

The desktop application stays native to Windows. Claude runs in a WSL2 Linux
distribution with its filesystem sandbox enabled; native Windows Claude is
not used by this port. Setup and sign-in instructions are in
[Claude WSL2 setup](docs/claude-wsl-setup.md).

After setup, use **Settings → AI → Recheck**. A ready bridge enables
**Claude Code via WSL2** in the model picker. Codex remains the Windows
default. Provider accounts and CLI authentication are separate from Canvas
sign-in.

## Data and network access

The library normally lives at `%APPDATA%\com.tchan.oculus`:

```text
com.tchan.oculus/
├── oculus.db                 # metadata, indexed pages, chats and projects
├── courses/<CODE>/           # synced coursework and personal uploads
├── lectures/                 # downloaded recordings and transcripts
├── agents/                   # agent workspace and memory
├── python-runtime/           # managed Python environment
└── file-manifest.json        # incremental download state
```

Session cookies are also stored in this directory. Keep library backups
private. Deleting it resets coursework, settings and local user content;
chats and projects cannot be recovered by syncing Canvas again. On systems
with MSIX filesystem virtualization, SQLite connections resolve one physical
database filename so the app and CLI share its journals and locks.

Parsing and embeddings run locally by default. Sync contacts university
services; setup downloads dependencies and model weights. Agent chat sends
requests and any coursework context it reads to the configured AI provider.
Enabling MinerU cloud parsing also sends selected documents to that service.

## Development and tests

```powershell
cd app
bun test tests
bun run build
cd src-tauri
cargo test --release
cd ../../sidecar
uv run --frozen python -m unittest discover
```

The automated suites cover sync persistence, term selection, uploads,
database paths, Windows process ownership and the agent bridges. Native UI,
real Office conversion, and real-model checks are documented in
[Windows validation](docs/windows-validation.md). The opt-in Office smoke
test requires LibreOffice and synthetic fixtures; live Canvas checks require
the user's university sign-in.

| Path | Purpose |
| --- | --- |
| `app/src/` | React routes, components, stores and database access |
| `app/src-tauri/src/` | Native backend, sync engine and agent bridges |
| `app/src-tauri/src/bin/oculus.rs` | Headless CLI |
| `app/src-tauri/vendor/libsqlite3-sys/` | Pinned SQLite engine and provenance |
| `sidecar/` | Python parsing, embeddings and worker management |
| `docs/` | Architecture, setup and feature documentation |
| `upstream.json` | Last imported macOS source revision |

Start with the [documentation index](docs/index.md) and
[development guide](docs/development.md). Follow [CLAUDE.md](CLAUDE.md) for
repository conventions. Build outputs, local libraries, credentials, model
weights and test artifacts are excluded from Git.

## Upstream updates and scope

Import new macOS changes into this checkout, preserve Windows adaptations,
run the relevant checks, then advance `upstream.json`. The macOS checkout
remains the source of truth and does not need Windows edits. See
[the update workflow](docs/windows.md#importing-later-macos-work).

Lecture recap has a backend but no finished player tab. The old BYOK API
bridge is dormant. Scheduled coursework sync, automations and Inbox are not
part of this version.

Oculus is a personal project and is not affiliated with the University of
Melbourne. It accesses course material through the user's own account.
