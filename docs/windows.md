# Windows port

`../oculus` remains the macOS source of truth. All Windows changes live in
this checkout; `MRI` is outside the project and must never be read or changed.
`upstream.json` records the imported commit. Git `origin` points at
`https://github.com/Tung-Phan-Dinh/oculus_window.git`. The development checkout
keeps the original local source remote as `macos-source`; that remote is local
configuration and is not required to build a clone of this repository.

## Build from source

Install Bun, stable Rust for `x86_64-pc-windows-msvc`, Visual Studio 2022 C++
Build Tools and the Windows SDK, sccache, and uv. Ensure they are on PATH in
a fresh terminal. WebView2 is the Windows web runtime; the installer can
install it if absent.

```powershell
cd app
bun install --frozen-lockfile
bun run ffmpeg
bun run prepare-sidecar
bun run stage-cli
cd ../sidecar
uv sync --frozen --python 3.12
cd ../app
bun run tauri dev
```

`bun run tauri build` creates
`app/src-tauri/target/release/bundle/nsis/Oculus_0.1.0_x64-setup.exe`.
The per-user installer includes the app, `oculus.exe`, ffmpeg, the Python
source/lockfile and uv. No macOS venv, developer path or model cache is shipped.
`app/src-tauri/tauri.windows.conf.json` owns the native Windows title bar,
NSIS install mode and resource mapping.

## First installed launch

The app opens immediately and prepares Python 3.12 plus the locked dependencies
in `%APPDATA%\com.tchan.oculus\python-runtime\.venv`. This requires internet
and several GB of disk space. uv's cache is reused on later launches; the
packaged source manifest removes only retired app-managed Python files while
preserving the venv. Settings → Library displays setup status. Diagnostics are
in `sidecar-setup.log` and `sidecar.log` in the same application data folder.
Restart after correcting a failed download to retry setup.

The bootstrap validates and uses the full Python patch-version executable.
This avoids uv's Windows minor-version junction failure without deleting an
interpreter or changing machine-wide Python registrations.

MinerU and Qwen weights download separately on first use into the normal
Hugging Face cache. Windows uses CUDA 13 wheels; the embedder selects CUDA
when supported and CPU otherwise. Local parsing remains the default. MinerU
cloud is opt-in and is never required to start or use the application.

The installed app uses its packaged sources even if the original checkout
still exists. Development builds use `sidecar/.venv`. Windows Job Objects own
the entire Python process tree, including nested model/render workers. The
app is single-instance, and a conflict on port 9547 is reported without killing
an unrelated process. Worker startup is gated until process ownership exists.

## Platform behavior

- Library and database: `%APPDATA%\com.tchan.oculus`; schema and relative
  library paths remain compatible with the upstream app. All database
  connections resolve the same physical file before opening SQLite, including
  under MSIX AppData virtualization. Existing journals at a conflicting alias
  require recovery before the app opens them. See [architecture.md](./architecture.md).
- Credentials: Windows Credential Manager through keyring's native backend.
- Sign-in: shared WebView2 profile for Canvas login and remote browser tabs;
  persisted Canvas cookies are restored with WebView2's native cookie API.
- Keepalive: a per-user Windows Task Scheduler job runs `oculus auth tick`
  while the user is logged in, even when Oculus is closed. It does not store
  a Windows password or request elevated privileges.
- Shortcuts: Ctrl replaces Command; F11 toggles fullscreen. Escape leaves
  fullscreen when a dialog or other control has not consumed it.
- Codex: native executable and standard npm installations are supported;
  the installed Codex desktop CLI is discovered when absent from PATH.
- Claude: Claude Code runs in a WSL2 Linux distribution with its filesystem
  sandbox enabled. Settings → AI reports bridge readiness and setup errors;
  its Recheck updates all composers and job pickers. Native Windows Claude
  is not used. Existing threads and drafts remain, and Codex stays the
  Windows default.
- Office conversion: install LibreOffice, or set `OCULUS_SOFFICE` to its
  executable. Word, PowerPoint and Excel use derived PDF siblings for the
  viewer, parsing and search. Original Office files remain available without
  the converter. Uploads lets students add their own subject files through
  the native picker or drag/drop, with deletion limited to that upload folder.

The existing product boundary remains: lecture recap has a backend but no
finished player tab; the BYOK API bridge is dormant; automations, Inbox and
scheduled coursework sync are not reintroduced.

## Claude Code through WSL2

The Windows frontend remains native. Only Claude and its filesystem sandbox
run in Linux. `app/scripts/setup-claude-wsl.ps1` prepares the dedicated
`Oculus` WSL2 distribution and its non-root `oculus` user, installs the Linux
Claude CLI and sandbox dependencies, and leaves other distributions alone.
See [Claude WSL2 setup](./claude-wsl-setup.md) for prerequisites and the setup
command.

Discovery prefers `Oculus`, then the default eligible WSL2 distribution, then
the first eligible distribution. Docker Desktop's internal distributions and
WSL1 are excluded. Set `OCULUS_CLAUDE_WSL_DISTRO` before launching Oculus to
choose another user distribution. It must have a non-root default user, the
native Linux Claude executable, Python 3, bubblewrap, socat and libseccomp;
health verifies that a sandbox can actually start, not only that its tools
are installed.

Claude's Linux installation has its own sign-in. Complete it in an interactive
terminal:

```powershell
wsl.exe --distribution Oculus --user oculus --cd /home/oculus --exec /home/oculus/.local/bin/claude auth login
```

Then use **Settings → AI → Recheck**. A ready bridge enables **Claude Code
via WSL2** in the model picker for new chats and job settings, and restores
sending in existing Claude threads. Setup failures appear inline and do not
discard drafts, change saved providers, or fall back to unsandboxed native
Claude. Codex remains the initial selection for fresh Windows chats.

## Verification

```powershell
cd app
bun test tests
bun run build
cd src-tauri
cargo test --release
cd ../../sidecar
uv run --frozen python -m unittest discover
```

Rust tests include isolated Windows credential and scheduler round trips,
Unicode media/range requests, file boundaries, paths, provider discovery and
the upstream suites. Python tests cover real worker trees, forced owner exit,
memory caps, Unicode pipes, download-cache behavior and parser routing.
Run the desktop as well: screenshots and UI interactions are necessary for
WebView2 layout, tabs, viewers, fullscreen and authentication windows. A real
Canvas sync additionally requires the user's university sign-in.

Release binaries are currently unsigned. Windows Smart App Control or an
organization's application-control policy may reject a newly compiled binary.
Distribution to machines enforcing that policy requires trusted code signing;
the build does not disable or modify Windows security settings.

## Importing later macOS work

1. Keep Windows changes committed separately from imported source commits.
2. Fetch the `macos-source` remote and compare its master with the commit in
   `upstream.json`, or fetch the explicitly requested upstream branch into this
   Windows checkout. Fetching must not change the macOS source checkout.
   On a fresh clone, add that source remote explicitly, pointing at the local
   macOS checkout or its GitHub repository; do not replace the Windows `origin`.
3. Merge the source changes, preserving these Windows platform boundaries,
   and update affected documentation in the same change.
4. Run the checks above and the installer smoke test, then advance
   `lastImportedCommit`. Never copy macOS `.venv`, build outputs, local data,
   credentials or model weights over the Windows checkout.

Key port seams are `app/src-tauri/src/platform.rs`,
`app/src-tauri/src/python_runtime.rs`, `app/src-tauri/src/sidecar.rs`,
`app/src-tauri/src/harness/discover.rs`, `sidecar/windows_process.py`, and
`app/src/lib/platform.ts`.

The Windows build pins a vendored `libsqlite3-sys` 0.30.1 with the SQLite
3.53.4 engine so the app and CLI share the updated WAL implementation without
changing SQLx. Source hashes and update instructions are in
`app/src-tauri/vendor/libsqlite3-sys/OCULUS-PROVENANCE.md`.
