#!/usr/bin/env python3
"""AndroidForge — Build Summary.

Collects the JSON outputs from each stage (detect / toolchain / build /
collect) and emits a single Markdown summary to $GITHUB_STEP_SUMMARY
and stdout. The summary includes:

  • Project type and structure
  • Detected toolchain (JDK / Gradle / AGP / Kotlin / NDK / Flutter)
  • Build result and which command succeeded
  • Generated APK(s) and their sizes
  • Build duration (if provided)
  • Warnings (legacy mode applied, missing wrapper, etc.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def load_json(s: str) -> dict[str, Any]:
    if not s:
        return {}
    if Path(s).exists():
        return json.loads(Path(s).read_text())
    return json.loads(s)


def main() -> int:
    parser = argparse.ArgumentParser(description="AndroidForge build summary")
    parser.add_argument("--detect", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--build", default="")
    parser.add_argument("--collect", default="")
    args = parser.parse_args()

    detect = load_json(args.detect)
    toolchain = load_json(args.toolchain)
    build = load_json(args.build) if args.build else {}
    collect = load_json(args.collect) if args.collect else {}

    lines: list[str] = []
    lines.append("## AndroidForge — Build Summary")
    lines.append("")

    # Project
    lines.append("### Project")
    lines.append(f"- **Type:** `{detect.get('project_type', 'unknown')}`")
    if detect.get("sub_type"):
        lines.append(f"- **Sub-type:** `{detect['sub_type']}`")
    lines.append(f"- **Structure:** `{detect.get('structure', 'unknown')}`")
    lines.append(f"- **Modules:** {', '.join(detect.get('modules') or ['(none)'])}")
    if detect.get("is_legacy"):
        lines.append(f"- **Legacy mode:** ✅ ({'; '.join(detect.get('legacy_reasons', []))})")
    else:
        lines.append("- **Legacy mode:** no")
    if detect.get("indicator_files"):
        files = list(detect["indicator_files"].keys())[:8]
        lines.append(f"- **Key files detected:** {', '.join(files)}")
    lines.append("")

    # Toolchain
    lines.append("### Toolchain")
    lines.append(f"- **JDK:** `{toolchain.get('jdk_version', 'unknown')}`")
    lines.append(f"- **Gradle:** `{toolchain.get('gradle_version', 'unknown')}`")
    if toolchain.get("agp_version"):
        lines.append(f"- **Android Gradle Plugin:** `{toolchain['agp_version']}`")
    if toolchain.get("kotlin_version"):
        lines.append(f"- **Kotlin:** `{toolchain['kotlin_version']}`")
    if toolchain.get("ndk_version"):
        lines.append(f"- **NDK:** `{toolchain['ndk_version']}`")
    if toolchain.get("flutter_version"):
        lines.append(f"- **Flutter:** `{toolchain['flutter_version']}`")
    lines.append(f"- **Use Gradle wrapper:** `{toolchain.get('use_gradle_wrapper', False)}`")
    if toolchain.get("legacy_fixes_applied"):
        lines.append("- **Compatibility fixes applied:**")
        for f in toolchain["legacy_fixes_applied"]:
            lines.append(f"  - {f}")
    lines.append("")

    # Build
    lines.append("### Build")
    if build:
        lines.append(f"- **Succeeded:** `{build.get('build_succeeded', False)}`")
        if build.get("successful_command"):
            cmd = " ".join(build["successful_command"])
            lines.append(f"- **Successful command:** `{cmd}`")
        lines.append(f"- **Exit code:** `{build.get('exit_code', '?')}`")
        if build.get("log_file"):
            lines.append(f"- **Log file:** `{build['log_file']}`")
    else:
        lines.append("- Build not run.")
    lines.append("")

    # APKs
    lines.append("### Output")
    if collect:
        count = collect.get("apk_count", 0)
        lines.append(f"- **APKs produced:** `{count}`")
        if collect.get("primary"):
            p = collect["primary"]
            lines.append(f"- **Primary APK:** `{p.get('output_name', '?')}` ({p.get('size_kb', '?')} KB)")
            lines.append(f"- **Output directory:** `{collect.get('output_directory', '?')}`")
        if collect.get("all_apks"):
            lines.append("")
            lines.append("| APK | Size |")
            lines.append("|-----|------|")
            for apk in collect["all_apks"]:
                marker = " ⭐" if apk.get("is_primary") else ""
                lines.append(f"| {apk['output_name']}{marker} | {apk['size_kb']} KB |")
    else:
        lines.append("- No APKs collected.")
    lines.append("")

    summary_md = "\n".join(lines)
    print(summary_md)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(summary_md)
            f.write("\n\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
