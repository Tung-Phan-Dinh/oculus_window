// Stage source and the uv bootstrapper, never a machine-specific Python venv.
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

if (process.platform === "win32") {
  const app = dirname(dirname(fileURLToPath(import.meta.url)));
  const source = join(dirname(app), "sidecar");
  const resources = join(app, "src-tauri", "resources");
  const staged = join(resources, "sidecar");
  mkdirSync(staged, { recursive: true });
  const isSource = (name) => !/[\\/:]/.test(name) && (name.endsWith(".py") || ["pyproject.toml", "uv.lock", ".python-version", "README.md"].includes(name));
  const files = readdirSync(source).filter(isSource).sort();
  const manifest = join(staged, "oculus-source-manifest.json");
  const previous = existsSync(manifest) ? JSON.parse(readFileSync(manifest, "utf8")) : readdirSync(staged).filter(isSource);
  for (const name of previous) {
    if (typeof name === "string" && isSource(name) && !files.includes(name)) rmSync(join(staged, name), { force: true });
  }
  for (const name of files) copyFileSync(join(source, name), join(staged, name));
  writeFileSync(manifest, JSON.stringify(files, null, 2) + "\n");
  let uv = process.env.OCULUS_UV_BIN;
  if (!uv) {
    try { uv = execFileSync("where.exe", ["uv.exe"], { encoding: "utf8", windowsHide: true }).trim().split(/\r?\n/)[0]; }
    catch { throw new Error("uv is required to package the Python service. Install uv or set OCULUS_UV_BIN."); }
  }
  if (!existsSync(uv)) throw new Error(`uv executable not found: ${uv}`);
  mkdirSync(join(resources, "tools"), { recursive: true });
  copyFileSync(uv, join(resources, "tools", "uv.exe"));
  console.log("[sidecar] source and uv staged for Windows installation");
}
