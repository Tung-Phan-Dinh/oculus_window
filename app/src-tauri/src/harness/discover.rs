//! Finding the provider CLIs from inside a GUI app.
//!
//! A Tauri app launched from the Dock inherits launchd's PATH — `/usr/bin`,
//! `/bin` and little else — so neither `~/.local/bin/claude` nor
//! `/opt/homebrew/bin/codex` resolves the way it does in a terminal. Same
//! class of problem as the sidecar's venv, same answer: an explicit override,
//! then the places the installers actually put things, then a login shell's
//! opinion as the last resort. Results are cached for the life of the
//! process; the binaries do not move mid-session.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use serde::Serialize;

use super::event::Provider;

/// `OCULUS_CLAUDE_BIN` / `OCULUS_CODEX_BIN` — bb's `BB_CLAUDE_CODE_EXECUTABLE`,
/// for a build that lives somewhere unusual.
pub fn override_env(provider: Provider) -> &'static str {
    match provider {
        Provider::Claude => "OCULUS_CLAUDE_BIN",
        Provider::Codex => "OCULUS_CODEX_BIN",
    }
}

fn binary_name(provider: Provider) -> &'static str {
    match provider {
        Provider::Claude => "claude",
        Provider::Codex => "codex",
    }
}

fn home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

/// Where the installers put things, in the order worth trying. `~/.claude/local`
/// is Claude's own migrate-installer target; the rest are npm-global, bun and
/// Homebrew defaults.
fn well_known_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Some(h) = home() {
        dirs.push(h.join(".local/bin"));
        dirs.push(h.join(".claude/local"));
        dirs.push(h.join(".bun/bin"));
        dirs.push(h.join(".npm-global/bin"));
        dirs.push(h.join(".volta/bin"));
        dirs.push(h.join(".cargo/bin"));
        // nvm keeps one bin dir per node version; take any that has it.
        if let Ok(rd) = std::fs::read_dir(h.join(".nvm/versions/node")) {
            for e in rd.flatten() {
                dirs.push(e.path().join("bin"));
            }
        }
    }
    #[cfg(windows)]
    {
        if let Some(appdata) = std::env::var_os("APPDATA") {
            dirs.push(PathBuf::from(appdata).join("npm"));
        }
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            let local = PathBuf::from(local);
            dirs.push(local.join("Programs/nodejs"));
            // Codex Desktop publishes its native CLI in versioned directories.
            // Prefer the most recently updated installation, without depending
            // on a Microsoft Store package version or reading credentials.
            if let Ok(entries) = std::fs::read_dir(local.join("OpenAI/Codex/bin")) {
                let mut installs: Vec<_> = entries.flatten()
                    .filter(|e| e.path().join("codex.exe").is_file()).collect();
                installs.sort_by_key(|e| std::cmp::Reverse(e.metadata().and_then(|m| m.modified()).ok()));
                dirs.extend(installs.into_iter().map(|e| e.path()));
            }
        }
        if let Some(programs) = std::env::var_os("ProgramFiles") {
            dirs.push(PathBuf::from(programs).join("nodejs"));
        }
    }
    #[cfg(not(windows))]
    {
        dirs.push(PathBuf::from("/opt/homebrew/bin"));
        dirs.push(PathBuf::from("/usr/local/bin"));
    }
    dirs
}

fn is_executable(p: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(p)
            .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        p.is_file()
    }
}

fn search_path_env(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .flat_map(|d| executable_candidates(&d, name))
        .find(|p| is_executable(p))
}

fn executable_candidates(dir: &Path, name: &str) -> Vec<PathBuf> {
    #[cfg(windows)]
    {
        // Prefer native executables over npm's extensionless shell script.
        [".exe", ".com", ".cmd", ".bat"]
            .iter().map(|ext| dir.join(format!("{name}{ext}"))).collect()
    }
    #[cfg(not(windows))]
    vec![dir.join(name)]
}

/// Run standard npm shims through Node directly. Sending a system prompt
/// through cmd.exe would turn model/user text into shell syntax.
pub fn provider_command(path: &Path) -> Result<std::process::Command, String> {
    #[cfg(windows)]
    if path.extension().and_then(|e| e.to_str()).is_some_and(|e|
        e.eq_ignore_ascii_case("cmd") || e.eq_ignore_ascii_case("bat")) {
        let dir = path.parent().ok_or("CLI shim has no parent directory")?;
        let package = match path.file_stem().and_then(|s| s.to_str()) {
            Some("codex") => "node_modules/@openai/codex/bin/codex.js",
            Some("claude") => "node_modules/@anthropic-ai/claude-code/cli.js",
            _ => return Err("Use the provider's native .exe or standard npm installation, not a custom batch wrapper.".into()),
        };
        let script = dir.join(package);
        if !script.is_file() {
            return Err(format!("Cannot resolve {} safely; set the CLI override to its native .exe.", path.display()));
        }
        let node = std::iter::once(dir.to_path_buf())
            .chain(std::env::var_os("PATH").into_iter().flat_map(|p| std::env::split_paths(&p).collect::<Vec<_>>()))
            .map(|d| d.join("node.exe")).find(|p| p.is_file())
            .ok_or("Node.js is required for the installed npm CLI")?;
        let mut command = crate::platform::command(node);
        command.arg(script);
        return Ok(command);
    }
    Ok(crate::platform::command(path))
}

/// Ask a login shell, which sources the user's profile and therefore has the
/// PATH a terminal would. Slow (a few hundred ms), so it is the last step and
/// the result is cached.
fn ask_login_shell(name: &str) -> Option<PathBuf> {
    #[cfg(windows)]
    {
        let _ = name;
        return None;
    }
    #[cfg(not(windows))]
    {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
    let out = std::process::Command::new(shell)
        .args(["-lc", &format!("command -v {name}")])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let p = PathBuf::from(s);
    is_executable(&p).then_some(p)
    }
}

fn locate(provider: Provider) -> Result<PathBuf, String> {
    let name = binary_name(provider);
    if let Some(p) = std::env::var_os(override_env(provider)) {
        let p = PathBuf::from(p);
        return if is_executable(&p) {
            Ok(p)
        } else {
            Err(format!(
                "{} points at {}, which is not an executable",
                override_env(provider),
                p.display()
            ))
        };
    }
    if let Some(p) = search_path_env(name) {
        return Ok(p);
    }
    if let Some(p) = well_known_dirs().into_iter().flat_map(|d| executable_candidates(&d, name)).find(|p| is_executable(p)) {
        return Ok(p);
    }
    if let Some(p) = ask_login_shell(name) {
        return Ok(p);
    }
    Err(format!(
        "`{name}` is not installed, or not on PATH — set {} to its full path",
        override_env(provider)
    ))
}

fn cache() -> &'static Mutex<std::collections::HashMap<Provider, Result<PathBuf, String>>> {
    static CACHE: OnceLock<Mutex<std::collections::HashMap<Provider, Result<PathBuf, String>>>> =
        OnceLock::new();
    CACHE.get_or_init(Default::default)
}

/// The provider's binary, or why it cannot be found. Cached per provider;
/// a failure is cached too, since re-probing a login shell on every send
/// would make a missing CLI slow as well as absent. [`forget`] clears it.
pub fn binary(provider: Provider) -> Result<PathBuf, String> {
    #[cfg(windows)]
    if provider == Provider::Claude { return super::wsl::bridge().map(|b| b.launcher); }
    let mut c = cache().lock().unwrap();
    c.entry(provider).or_insert_with(|| locate(provider)).clone()
}

/// Drop the cached lookups, for a health check after the user installs one.
pub fn forget() {
    cache().lock().unwrap().clear();
    #[cfg(windows)]
    super::wsl::forget();
}

/// The `oculus` binary the child should find on its PATH. In a bundle it is
/// a sibling of the app executable; in `tauri dev` it is whatever the last
/// `bun run cli` built, or the `~/.local/bin` symlink `cli:install` leaves.
pub fn oculus_cli() -> Option<PathBuf> {
    let name = format!("oculus{}", std::env::consts::EXE_SUFFIX);
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for c in [dir.join(&name), dir.join("../release").join(&name)] {
                if is_executable(&c) {
                    return c.canonicalize().ok();
                }
            }
        }
    }
    if let Some(h) = home() {
        let c = h.join(".local/bin").join(&name);
        if is_executable(&c) {
            return Some(c);
        }
    }
    search_path_env("oculus")
}

/// What Settings shows: found where, which version, and if not, why.
#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct BridgeHealth {
    pub provider: Provider,
    pub label: &'static str,
    pub path: Option<String>,
    pub version: Option<String>,
    pub error: Option<String>,
    /// Which env var overrides discovery, for the Settings hint.
    pub override_env: &'static str,
}

pub fn health(provider: Provider) -> BridgeHealth {
    let mut h = BridgeHealth {
        provider,
        label: provider.label(),
        path: None,
        version: None,
        error: None,
        override_env: override_env(provider),
    };
    #[cfg(windows)]
    if provider == Provider::Claude {
        h.label = "Claude Code via WSL2";
        h.override_env = super::wsl::DISTRO_ENV;
        match super::wsl::bridge() {
            Ok(bridge) => {
                h.path = Some(format!("WSL2 {}: {}", bridge.distro, bridge.claude));
                h.version = Some(bridge.version.split_whitespace().next().unwrap_or(&bridge.version).to_string());
                h.error = bridge.auth_error();
            }
            Err(error) => h.error = Some(error),
        }
        return h;
    }
    match binary(provider) {
        Ok(p) => {
            h.path = Some(p.display().to_string());
            match provider_command(&p).and_then(|mut cmd| cmd.arg("--version").output().map_err(|e| e.to_string())) {
                Ok(out) if out.status.success() => {
                    let v = String::from_utf8_lossy(&out.stdout).trim().to_string();
                    // `2.1.267 (Claude Code)` / `codex-cli 0.153.4` — keep the
                    // number, drop the branding.
                    let num = v
                        .split_whitespace()
                        .find(|w| w.chars().next().map_or(false, |c| c.is_ascii_digit()))
                        .unwrap_or(&v);
                    h.version = Some(num.to_string());
                }
                Ok(out) => {
                    h.error = Some(format!(
                        "`--version` failed: {}",
                        String::from_utf8_lossy(&out.stderr).trim()
                    ))
                }
                Err(e) => h.error = Some(format!("cannot run {}: {e}", p.display())),
            }
        }
        Err(e) => h.error = Some(e),
    }
    h
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;

    #[test]
    fn executable_lookup_prefers_native_over_batch_shims() {
        let paths = executable_candidates(Path::new(r"C:\\tools"), "codex");
        assert_eq!(paths[0].file_name().unwrap(), "codex.exe");
        assert_eq!(paths[2].file_name().unwrap(), "codex.cmd");
        assert!(!paths.iter().any(|p| p.extension().is_none()));
    }

    #[test]
    fn unknown_batch_wrappers_are_never_sent_to_cmd() {
        assert!(provider_command(Path::new(r"C:\\tools\\custom.cmd")).is_err());
    }

    #[test]
    fn npm_shims_run_javascript_without_shell_interpolation() {
        let root = std::env::temp_dir().join(format!("oculus-shim-{}", std::process::id()));
        let script = root.join("node_modules/@openai/codex/bin/codex.js");
        std::fs::create_dir_all(script.parent().unwrap()).unwrap();
        std::fs::write(&script, "").unwrap();
        std::fs::write(root.join("node.exe"), "").unwrap();
        let command = provider_command(&root.join("codex.cmd")).unwrap();
        assert_eq!(command.get_program(), root.join("node.exe").as_os_str());
        assert_eq!(command.get_args().collect::<Vec<_>>(), vec![script.as_os_str()]);
        std::fs::remove_dir_all(root).unwrap();
    }
}

/// The environment a provider child gets.
///
/// Two deliberate edits to the inherited env. The API keys are stripped so
/// that a `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the user's shell cannot
/// silently move a subscription session onto per-token billing — the entire
/// reason this layer drives the CLIs rather than the APIs. And the `oculus`
/// binary's directory is put in front of PATH, because `AGENTS.md` tells the
/// agent to run `oculus grep`, and advice that resolves to "command not
/// found" is worse than none.
pub fn child_env() -> Vec<(String, String)> {
    let mut env: Vec<(String, String)> = std::env::vars()
        .filter(|(k, _)| {
            !matches!(
                k.to_ascii_uppercase().as_str(),
                "ANTHROPIC_API_KEY"
                    | "OPENAI_API_KEY"
                    | "CLAUDE_AGENT_SDK_CLIENT_APP"
                    // Set when *this* process is itself a Claude Code child
                    // (a dev session run from inside one); the CLI refuses
                    // to nest and would exit immediately.
                    | "CLAUDECODE"
                    | "CLAUDE_CODE_ENTRYPOINT"
            )
        })
        .collect();

    let mut path_parts: Vec<PathBuf> = Vec::new();
    if let Some(cli) = oculus_cli() {
        if let Some(d) = cli.parent() {
            path_parts.push(d.to_path_buf());
        }
    }
    for p in [Provider::Claude, Provider::Codex] {
        // Windows Claude runs in a separate Linux environment. Its launcher
        // directory is neither a native provider dependency nor useful PATH.
        if cfg!(windows) && p == Provider::Claude { continue; }
        if let Ok(b) = binary(p) {
            if let Some(d) = b.parent() {
                path_parts.push(d.to_path_buf());
            }
        }
    }
    if let Some(cur) = std::env::var_os("PATH") {
        path_parts.extend(std::env::split_paths(&cur));
    } else {
        path_parts.extend(well_known_dirs());
        path_parts.push(PathBuf::from("/usr/bin"));
        path_parts.push(PathBuf::from("/bin"));
    }
    let mut seen = std::collections::HashSet::new();
    path_parts.retain(|p| seen.insert(p.clone()));
    if let Ok(joined) = std::env::join_paths(&path_parts) {
        env.retain(|(k, _)| !k.eq_ignore_ascii_case("PATH"));
        env.push(("PATH".into(), joined.to_string_lossy().into_owned()));
    }
    env
}
