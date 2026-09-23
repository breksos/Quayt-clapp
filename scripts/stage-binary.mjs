import { chmod, copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const executable = process.platform === "win32" ? "quayt.exe" : "quayt";
const source = resolve(root, "src-tauri", "target", "release", executable);
const destination = resolve(root, "bin", executable);

await mkdir(dirname(destination), { recursive: true });
await copyFile(source, destination);
if (process.platform !== "win32") await chmod(destination, 0o755);
console.log(`staged ${destination}`);
