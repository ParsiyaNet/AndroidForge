#!/usr/bin/env python3
"""Apply WAPFire 4.3.3 adaptive VRM performance settings to an extracted project."""
import argparse
import os
import sys
import zipfile
from pathlib import Path

VERSION = "4.3.3"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"Missing {label}: {path}")
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Expected one {label} anchor, got {text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch(root: Path) -> None:
    js = root / "app/src/main/assets/assistant/vrm/vrm.js"
    old_renderer = """const canvas = $('stage');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setClearColor(0x000000, 0);
canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); if (!ready) fatal('WebGL context lost while starting.'); });"""
    new_renderer = """/* Adaptive quality: preserve full quality on capable phones and reduce GPU load on older ones. */
const deviceMemoryGB = Number(navigator.deviceMemory) || 0;
const cpuCores = Number(navigator.hardwareConcurrency) || 0;
const lowEndDevice = (deviceMemoryGB > 0 && deviceMemoryGB <= 3) ||
  (cpuCores > 0 && cpuCores <= 4 && (!deviceMemoryGB || deviceMemoryGB <= 4));
const highEndDevice = !lowEndDevice && (deviceMemoryGB >= 6 || cpuCores >= 8);
const QUALITY_LEVELS = [
  { name: 'low', maxPixelRatio: 1.00, targetFps: 30, antialias: false, powerPreference: 'low-power' },
  { name: 'balanced', maxPixelRatio: 1.50, targetFps: 60, antialias: false, powerPreference: 'low-power' },
  { name: 'high', maxPixelRatio: 2.00, targetFps: 60, antialias: true, powerPreference: 'high-performance' }
];
let qualityLevel = lowEndDevice ? 0 : (highEndDevice ? 2 : 1);
let targetFrameInterval = 1000 / QUALITY_LEVELS[qualityLevel].targetFps;
const canvas = $('stage');
const initialQuality = QUALITY_LEVELS[qualityLevel];
const renderer = new THREE.WebGLRenderer({ canvas, antialias: initialQuality.antialias, alpha: true, powerPreference: initialQuality.powerPreference });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, initialQuality.maxPixelRatio));
renderer.setClearColor(0x000000, 0);
canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); if (!ready) fatal('WebGL context lost while starting.'); });
let lastRenderedAt = 0;
let lastUpdatedAt = performance.now();
let perfWindowStartedAt = lastUpdatedAt;
let perfWindowFrames = 0;
let perfWindowSlowFrames = 0;
let perfWindowWorkMs = 0;
function resetPerformanceWindow(now = performance.now()) {
  perfWindowStartedAt = now; perfWindowFrames = 0; perfWindowSlowFrames = 0; perfWindowWorkMs = 0;
}
function applyQualityLevel(nextLevel) {
  const next = Math.max(0, Math.min(QUALITY_LEVELS.length - 1, nextLevel));
  if (next >= qualityLevel) return;
  qualityLevel = next;
  const profile = QUALITY_LEVELS[qualityLevel];
  targetFrameInterval = 1000 / profile.targetFps;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, profile.maxPixelRatio));
  fit();
  resetPerformanceWindow();
}
// Stop all 3D work while the assistant WebView is backgrounded.
document.addEventListener('visibilitychange', () => {
  lastRenderedAt = 0; lastUpdatedAt = performance.now(); resetPerformanceWindow(lastUpdatedAt);
});"""
    replace_once(js, old_renderer, new_renderer, "renderer setup")

    old_loop = """/* ---------- main loop ---------- */
let last = performance.now();
function tick() {
  const nowMs = performance.now();
  const dt = Math.min(0.1, Math.max(0.001, (nowMs - last) / 1000));
  last = nowMs;
  if (vrm) {
    applyExpressions(poseEngine.update(nowMs / 1000, dt));
    if (mixer) mixer.update(dt);
    vrm.update(dt);
  }
  renderer.render(scene, camera);
}"""
    new_loop = """/* ---------- adaptive main loop ---------- */
function tick() {
  const nowMs = performance.now();
  if (document.visibilityState === 'hidden') { lastRenderedAt = 0; lastUpdatedAt = nowMs; return; }
  const elapsedMs = lastRenderedAt ? nowMs - lastRenderedAt : 0;
  // Cap rendering even on 90/120/144 Hz displays.
  if (lastRenderedAt && elapsedMs < targetFrameInterval - 1) return;
  const dt = Math.min(0.05, Math.max(0.001, (nowMs - lastUpdatedAt) / 1000));
  lastUpdatedAt = nowMs; lastRenderedAt = nowMs;
  const workStartedAt = performance.now();
  if (vrm) {
    applyExpressions(poseEngine.update(nowMs / 1000, dt));
    if (mixer) mixer.update(dt);
    vrm.update(dt);
  }
  renderer.render(scene, camera);
  const workMs = performance.now() - workStartedAt;
  perfWindowFrames++; perfWindowWorkMs += workMs;
  if (elapsedMs > targetFrameInterval * 1.45) perfWindowSlowFrames++;
  // Every 5 seconds, lower quality once if frame delivery is consistently below budget.
  if (nowMs - perfWindowStartedAt >= 5000 && perfWindowFrames >= 30) {
    const averageInterval = (nowMs - perfWindowStartedAt) / perfWindowFrames;
    const averageWorkMs = perfWindowWorkMs / perfWindowFrames;
    const slowFrameRatio = perfWindowSlowFrames / perfWindowFrames;
    if (slowFrameRatio > 0.20 || averageInterval > targetFrameInterval * 1.45 || averageWorkMs > 18) {
      applyQualityLevel(qualityLevel - 1);
    }
    resetPerformanceWindow(nowMs);
  }
}"""
    replace_once(js, old_loop, new_loop, "animation loop")

    old_culling = "  vrm.scene.traverse((o) => { o.frustumCulled = false; });"
    new_culling = """  // Keep animated skinned meshes safe from culling; static meshes use normal Three.js culling.
  vrm.scene.traverse((o) => { if (o.isSkinnedMesh) o.frustumCulled = false; });"""
    replace_once(js, old_culling, new_culling, "mesh culling")

    old_info = """  expressions: Array.from(exprNames),
  surprised: surprisedName
});"""
    new_info = """  expressions: Array.from(exprNames),
  surprised: surprisedName,
  qualityMode: QUALITY_LEVELS[qualityLevel].name,
  targetFps: QUALITY_LEVELS[qualityLevel].targetFps,
  pixelRatio: renderer.getPixelRatio()
});"""
    replace_once(js, old_info, new_info, "renderer diagnostics")

    replace_once(js, "const BUILD = '4.3.2';", "const BUILD = '4.3.3';", "renderer version")
    replace_once(root / "app/src/main/assets/assistant/vrm/index.html", 'content="4.3.2"', 'content="4.3.3"', "HTML version")
    replace_once(root / "app/src/main/java/io/wapfire/mobile/ui/screens/LivLivAssistantView.kt", 'private const val VRM_BUILD = "4.3.2"', 'private const val VRM_BUILD = "4.3.3"', "native version")
    gradle = root / "app/build.gradle.kts"
    replace_once(gradle, "versionCode = 106", "versionCode = 107", "versionCode")
    replace_once(gradle, 'versionName = "4.3.2"', 'versionName = "4.3.3"', "versionName")
    replace_once(gradle, "WAPFire-VrmLibs/4.3.2", "WAPFire-VrmLibs/4.3.3", "runtime user agent")

    (root / "CHANGELOG-4.3.3.md").write_text(
        "# WAPFire Mobile 4.3.3 — Adaptive VRM performance\n\n"
        "- Low-end phones: 30 FPS, reduced pixel ratio and no MSAA; midrange phones retain 60 FPS at a moderate resolution; capable phones retain the original high-resolution renderer.\n"
        "- Runtime frame-budget monitor steps down one tier if frame delivery remains poor.\n"
        "- 90/120/144 Hz screens do not render extra frames above the device profile.\n"
        "- Pose, spring-bone, mixer and WebGL work pauses while the WebView is hidden.\n"
        "- Original Nara VRM, VRMA motions, look, animation mapping and native bridge are preserved.\n",
        encoding="utf-8",
    )
    (root / "VALIDATION-4.3.3-PERFORMANCE.txt").write_text(
        "WAPFire 4.3.3 VRM performance patch\n"
        "====================================\n"
        "[x] Hardware-aware low/balanced/high renderer profiles\n"
        "[x] 30 FPS cap for low profile; balanced/high profiles remain 60 FPS\n"
        "[x] Reduced pixel ratio and antialiasing on lower profiles\n"
        "[x] Adaptive frame-budget downgrade and hidden-WebView pause\n"
        "[x] Original VRM/VRMA files are not modified\n"
        "[x] Native bridge API names are not modified\n"
        "Physical-device FPS measurements are not claimed.\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-zip", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    js = root / "app/src/main/assets/assistant/vrm/vrm.js"
    if not js.is_file():
        raise RuntimeError(f"Not a WAPFire VRM project: {root}")
    patch(root)

    source = js.read_text(encoding="utf-8")
    for key in ("QUALITY_LEVELS", "targetFrameInterval", "document.visibilityState", "applyQualityLevel", "renderer.getPixelRatio()"):
        if key not in source:
            raise RuntimeError(f"Performance validation failed; missing {key}")

    output = Path(args.output_zip).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    prefix = f"WAPFire-Mobile-{VERSION}-VRM-Performance/"
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                archive.write(file, prefix + file.relative_to(root).as_posix())
    temp.replace(output)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("optimized=true\n")
            handle.write(f"source_zip={output}\n")
    print(f"WAPFire 4.3.3 source prepared: {output} ({output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
