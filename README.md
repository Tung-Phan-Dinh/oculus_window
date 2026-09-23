# Oculus for Windows

A Windows desktop app that brings UniMelb coursework from Canvas, Ed
Discussion and Echo360 into a searchable local library. Built with Tauri 2,
React and Rust.

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
- **Document parsing:** choose MinerU cloud or a separately running local
  MinerU server. Parsing failures remain visible and never silently switch engines.
- **Search:** search parsed text from the command palette, or index page images
  with Voyage for semantic search over formulas, diagrams and slide layouts.
- **Agent chat:** Codex, Claude Code through WSL2, opencode and Antigravity,
  with provider setup, streamed responses, file mentions and image attachments.
- **Lectures and planning:** Echo360 playback, chapters, a reading copy of the
  transcript, editable calendar events, and unified projects and tasks.
- **Workspace:** split panes, reopen closed tabs, browser history and find,
  an updated PDF viewer, and Mermaid diagrams with full-size previews.

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
  Cargo configuration.
- LibreOffice for Word, PowerPoint and Excel conversion. Oculus also accepts
  an `OCULUS_SOFFICE` environment variable pointing to the executable.

Python is no longer bundled or required to build the app. Local parsing is
an optional external service; see [parsing setup](docs/parsing.md). Cloud
parsing needs a MinerU token, and semantic indexing needs a Voyage key.
Windows retains Local as its initial parser choice; select Cloud explicitly
to send documents there. An existing explicit cloud choice is retained.

Use Bun for this project; `app/bun.lock` is the frontend lockfile.

```powershell
git clone https://github.com/Tung-Phan-Dinh/oculus_window.git
cd oculus_window/app
bun install --frozen-lockfile
bun run tauri dev
```

For an installable release, run from `app/`:

```powershell
bun run tauri build
```

The installer is written to
`app/src-tauri/target/release/bundle/nsis/Oculus_0.1.0_x64-setup.exe`.
It includes the desktop app, the `oculus.exe` CLI, ffmpeg and PDFium. The dev
preflight fetches the native dependencies and builds a current CLI automatically.
Configure parsing and indexing under **Settings → Library**.

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
└── file-manifest.json        # incremental download state
```

Session cookies are also stored in this directory. Keep library backups
private. Deleting it resets coursework, settings and local user content;
chats and projects cannot be recovered by syncing Canvas again. On systems
with MSIX filesystem virtualization, SQLite connections resolve one physical
database filename so the app and CLI share its journals and locks.

Sync contacts university services. MinerU cloud parsing sends documents to
MinerU; choosing Local sends them to the configured loopback MinerU server.
Semantic indexing sends page images to Voyage, and semantic queries send
query text there. Agent chat sends requests and coursework context it reads
to the configured AI provider. Existing parsed text and lexical search do
not require a Voyage key. Settings show estimates before starting a bulk
indexing run; opening Settings does not start one. When Voyage is configured,
newly completed parses can also be indexed automatically.

When upgrading from the Python-based version, existing coursework and parsed
artifacts are retained. Old Qwen vectors cannot be searched using Voyage's
model; rebuilding the semantic index is a separate step. Old Python runtimes
and model caches are left on disk and are no longer started by Oculus.

## Development and tests

```powershell
cd app
bun test tests
bun run build
cd src-tauri
cargo test --release
```

The automated suites cover sync persistence, term selection, uploads,
database paths, parser and embedding protocols, and the agent bridges. Native UI,
Office conversion and version-specific validation are documented in
[Windows validation](docs/windows-validation.md). The opt-in Office smoke
test requires LibreOffice and synthetic fixtures; live Canvas checks require
the user's university sign-in.

| Path | Purpose |
| --- | --- |
| `app/src/` | React routes, components, stores and database access |
| `app/src-tauri/src/` | Native backend, sync engine and agent bridges |
| `app/src-tauri/src/bin/oculus.rs` | Headless CLI |
| `app/src-tauri/vendor/libsqlite3-sys/` | Pinned SQLite engine and provenance |
| `app/src-tauri/src/parse/` and `embed/` | Rust parsing and indexing clients |
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

The old recap was replaced by a transcript reading copy, and the dormant
BYOK layer was removed. Scheduled coursework sync, automations and Inbox
are not part of this version.

Oculus is a personal project and is not affiliated with the University of
Melbourne. It accesses course material through the user's own account.
