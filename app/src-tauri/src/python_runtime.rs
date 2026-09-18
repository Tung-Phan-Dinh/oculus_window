//! Installed Windows builds prepare Python outside the application's resources.
//! The venv is local state, while every release supplies current source + lock.

#[cfg(windows)]
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

static STATUS: OnceLock<Mutex<Option<String>>> = OnceLock::new();

pub fn status() -> Option<String> {
    STATUS.get_or_init(Default::default).lock().unwrap().clone()
}

pub fn set_status(message: Option<String>) {
    *STATUS.get_or_init(Default::default).lock().unwrap() = message;
}

#[cfg(windows)]
pub fn prepare(source: &Path, uv: &Path) -> Result<PathBuf, String> {
    use std::process::Stdio;
    let data = crate::paths::data_dir();
    let runtime = data.join("python-runtime");
    std::fs::create_dir_all(&runtime).map_err(|e| e.to_string())?;
    const MANIFEST: &str = "oculus-source-manifest.json";
    let manifest = std::fs::read(source.join(MANIFEST)).map_err(|e| e.to_string())?;
    let files: Vec<String> = serde_json::from_slice(&manifest).map_err(|e| e.to_string())?;
    let is_source = |name: &str| {
        !name.contains(['/', '\\', ':']) && (name.ends_with(".py") || matches!(name, "pyproject.toml" | "uv.lock" | ".python-version" | "README.md"))
    };
    if files.iter().any(|name| !is_source(name)) {
        return Err("The packaged Python source manifest contains an invalid filename.".into());
    }
    let previous: Vec<String> = std::fs::read(runtime.join(MANIFEST)).ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
    for name in previous {
        if is_source(&name) && !files.contains(&name) {
            match std::fs::remove_file(runtime.join(&name)) {
                Ok(()) => {},
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {},
                Err(e) => return Err(format!("Cannot remove retired Python module {name}: {e}")),
            }
        }
    }
    for name in files {
        let dest = runtime.join(&name);
        let bytes = std::fs::read(source.join(name)).map_err(|e| e.to_string())?;
        if std::fs::read(&dest).ok().as_deref() != Some(bytes.as_slice()) {
            std::fs::write(dest, bytes).map_err(|e| e.to_string())?;
        }
    }
    std::fs::write(runtime.join(MANIFEST), manifest).map_err(|e| e.to_string())?;
    let log_path = data.join("sidecar-setup.log");
    let log = std::fs::File::create(&log_path).map_err(|e| e.to_string())?;
    set_status(Some(format!("Preparing the local Python service. First launch downloads Python and model dependencies. Progress: {}", log_path.display())));
    let system = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    let powershell = PathBuf::from(system).join("System32/WindowsPowerShell/v1.0/powershell.exe");
    // This fixed command waits until its Job Object exists before launching uv.
    // The executable path travels as data, never interpolated PowerShell source.
    let mut child = crate::platform::command(powershell)
        .args(["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", include_str!("python_setup.ps1")])
        .env("OCULUS_BOOTSTRAP_UV", uv)
        .current_dir(&runtime)
        .env_remove("VIRTUAL_ENV")
        .env_remove("UV_PYTHON")
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .env("UV_PROJECT_ENVIRONMENT", runtime.join(".venv"))
        .env("UV_PYTHON_PREFERENCE", "only-managed")
        .env("UV_PYTHON_INSTALL_DIR", data.join("python"))
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .spawn()
        .map_err(|e| format!("Cannot start Python setup: {e}"))?;
    let _job = match crate::platform::ProcessJob::assign(&child) {
        Ok(job) => job,
        Err(error) => {
            child.kill().ok(); child.wait().ok();
            return Err(format!("Cannot own Python setup process: {error}"));
        }
    };
    use std::io::Write;
    child.stdin.take().ok_or("Python setup has no startup pipe")?
        .write_all(b"start\n").map_err(|e| format!("Cannot release Python setup: {e}"))?;
    let status = child.wait().map_err(|e| format!("Python setup failed: {e}"))?;
    if !status.success() {
        return Err(format!("Python setup failed ({status}). See {}. Restart Oculus to retry.", log_path.display()));
    }
    set_status(Some("Starting the local Python service…".into()));
    Ok(runtime)
}
