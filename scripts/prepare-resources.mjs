import { cp, mkdir, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "src-tauri", "resources", "python");

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

await cp(join(root, "gdictate.py"), join(out, "gdictate.py"));
await cp(join(root, "speech-proxy.html"), join(out, "speech-proxy.html"));
await cp(join(root, "requirements.txt"), join(out, "requirements.txt"));
await cp(join(root, "gdictate_core"), join(out, "gdictate_core"), {
  recursive: true,
  filter: (source) => {
    const normalized = source.replaceAll("\\", "/");
    return !normalized.includes("/__pycache__/") && !normalized.endsWith(".pyc");
  }
});

if (process.env.GDICTATE_VENDOR_PY_DEPS === "1") {
  const python = process.env.GDICTATE_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const vendor = join(out, "vendor");
  await rm(vendor, { recursive: true, force: true });
  await mkdir(vendor, { recursive: true });
  const result = spawnSync(
    python,
    ["-m", "pip", "install", "--upgrade", "--target", vendor, "-r", join(root, "requirements.txt")],
    { stdio: "inherit" }
  );
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}
