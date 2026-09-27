#!/usr/bin/env python3
"""AndroidForge — Failure Diagnosis.

Reads the build log file produced by build_project.py and scans for known
error patterns. Emits a structured diagnosis to stdout (and to the GitHub
Step Summary via $GITHUB_STEP_SUMMARY) explaining the most likely cause and
a recommended fix.

The diagnosis is best-effort and never claims certainty — it merely ranks
the most probable causes by counting signature hits in the log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


# Each entry: (label, list of regex patterns, hint)
DIAGNOSTICS: list[dict[str, Any]] = [
    {
        "label": "Java incompatibility",
        "patterns": [
            r"unsupported class file major version",
            r"java\.lang\.UnsupportedClassVersionError",
            r"Caused by:.*java\.lang\.UnsupportedClassVersionError",
            r"has been compiled by a more recent version of the Java Runtime",
            r"could not be initialized.*java",
        ],
        "hint": "The JDK in use is older than the bytecode the project was compiled against. "
                "Try installing a newer JDK (e.g. JDK 17 instead of JDK 11). Update the toolchain "
                "rules in config/toolchain-rules.yaml if needed.",
    },
    {
        "label": "Gradle incompatibility",
        "patterns": [
            r"Gradle version [\d.]+ requires Java \d+",
            r"Minimum supported Gradle version",
            r"is too old for the Android Gradle Plugin",
            r"gradle version .* is not supported",
            r"The current Gradle version .* is not compatible with the Android Gradle Plugin",
        ],
        "hint": "Gradle version is incompatible with the AGP version. The setup script should pick "
                "a compatible Gradle version automatically — check config/toolchain-rules.yaml.",
    },
    {
        "label": "Android Gradle Plugin incompatibility",
        "patterns": [
            r"Android Gradle plugin version .* is too old",
            r"Android Gradle Plugin .* requires Gradle",
            r"Please use Android Gradle Plugin",
            r"androidGradlePlugin",
            r"could not find com\.android\.tools\.build:gradle:",
        ],
        "hint": "AGP version is incompatible with the runner environment. Update the project's "
                "AGP version, or check the toolchain rules for a compatible mapping.",
    },
    {
        "label": "Missing Android SDK platform",
        "patterns": [
            r"failed to find target with hash string",
            r"Could not find platform[' ]+android[-_]\d+",
            r"Installed at:",
            r"Please install the Android SDK",
            r"sdk.*not found",
            r"Android SDK not found",
            r"Failed to find Build Tools",
        ],
        "hint": "Required Android SDK platform or Build Tools are missing. Update the workflow's "
                "sdkmanager invocation, or extend config/toolchain-rules.yaml android_sdk.platforms.",
    },
    {
        "label": "Missing NDK",
        "patterns": [
            r"NDK not configured",
            r"Could not find NDK",
            r"Android NDK:.*not found",
            r"Failed to find CMake",
            r"No version of NDK matched",
        ],
        "hint": "A native build is configured but the NDK / CMake is not installed. The workflow "
                "must install the NDK version declared in build.gradle (or the default from config).",
    },
    {
        "label": "Dependency resolution failure",
        "patterns": [
            r"Could not resolve",
            r"Could not find .*:.*:.*\.",
            r"Could not GET 'https?://[^']+.*'",
            r"dependency .* not found",
            r"unresolved dependency",
            r"Connect to [^\s]+ timed out",
            r"Could not resolve dependencies",
        ],
        "hint": "A dependency could not be resolved. Check network/repo configuration, "
                "and ensure google()/mavenCentral() are declared in settings.gradle or build.gradle.",
    },
    {
        "label": "Kotlin compilation failure",
        "patterns": [
            r"e:.*\.kt:\(\d+,\d+\):",
            r"Compilation error.*Kotlin",
            r"kotlin compilation",
            r"kotlinc",
            r"Cannot inline bytecode built with",
        ],
        "hint": "Kotlin source failed to compile. Look for 'e: file.kt:' lines in the log for the "
                "exact location. May also indicate Kotlin/Java version mismatch.",
    },
    {
        "label": "Java compilation failure",
        "patterns": [
            r"error:.*cannot find symbol",
            r"error:.*';' expected",
            r"error:.*\.java:\d+:",
            r"javac.*error",
            r"Compilation failed",
            r"; expected",
        ],
        "hint": "Java compilation failed. Look for `error:` lines with file/line locations in the log.",
    },
    {
        "label": "Android resource error",
        "patterns": [
            r"ERROR:.*res[/\\]",
            r"resource .* not found",
            r"AAPT:? error",
            r"failure in manifestmerge",
            r"Manifest merger failed",
            r"Suggestion:.*'tools:replace=\"[^\"]+\"'",
        ],
        "hint": "An Android resource or manifest issue. Look for AAPT/manifest merger errors in the log "
                "and check res/ files for invalid XML, missing resources, or duplicate declarations.",
    },
    {
        "label": "Manifest error",
        "patterns": [
            r"AndroidManifest\.xml.*error",
            r"Attribute .* at .* requires a placeholder substitution",
            r"<application>.*is not allowed",
            r"MERGE FAILURE",
            r"Element .* at .* is duplicated",
        ],
        "hint": "AndroidManifest.xml contains an error. Inspect the manifest merger report at "
                "app/build/outputs/logs/manifest-merger-*.txt for details.",
    },
    {
        "label": "Corrupt or missing Gradle wrapper",
        "patterns": [
            r"Could not find or load main class",
            r"gradle-wrapper\.jar.*not found",
            r"org\.gradle\.wrapper\.GradleWrapperMain",
            r"Could not initialize class",
            r"Invalid Gradle JDK",
        ],
        "hint": "The Gradle wrapper jar is corrupt or missing. The setup script should have "
                "regenerated it via `gradle wrapper` — check that step's output.",
    },
    {
        "label": "Unsupported project structure",
        "patterns": [
            r"Project with path ':[^']+' could not be found",
            r"Could not find method [^\s]+\(\)",
            r"No such property:",
            r"Build file .* does not exist",
            r"settings\.gradle.*not found",
            r"What went wrong:.*A problem occurred evaluating",
        ],
        "hint": "The project structure is unusual or references modules that don't exist. Verify "
                "the project root was detected correctly and that all `include` declarations in "
                "settings.gradle match real sub-directories.",
    },
    {
        "label": "Network / dependency download failure",
        "patterns": [
            r"Could not GET 'https?://",
            r"Could not HEAD 'https?://",
            r"Read timed out",
            r"Connection timed out",
            r"Could not resolve .*\.gradle\.org",
            r"Connection refused",
            r"Network is unreachable",
        ],
        "hint": "Network issue while downloading dependencies. This may be transient — retry the build. "
                "If it persists, add mirrors in dependency_fallbacks in toolchain-rules.yaml.",
    },
    {
        "label": "Out of memory",
        "patterns": [
            r"java\.lang\.OutOfMemoryError",
            r"Exceeded .* budget",
            r"GC overhead limit exceeded",
            r"Could not allocate.*Metaspace",
        ],
        "hint": "The build ran out of memory. Increase org.gradle.jvmargs in gradle.properties "
                "(e.g. -Xmx4g) or move to a larger GitHub runner (ubuntu-latest has 7GB RAM).",
    },
    {
        "label": "Permission denied / file lock",
        "patterns": [
            r"Permission denied",
            r"being used by another process",
            r"Could not lock",
            r"Lock timeout",
        ],
        "hint": "A file is locked or inaccessible. Disable the Gradle daemon (--no-daemon) "
                "and clear any stale .gradle/ lock directories.",
    },
]


def diagnose(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        return {"found": False, "reason": f"log file not found: {log_path}"}

    content = log_path.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return {"found": False, "reason": "log file is empty"}

    hits: list[dict[str, Any]] = []
    for d in DIAGNOSTICS:
        score = 0
        snippets: list[str] = []
        for pat in d["patterns"]:
            for m in re.finditer(pat, content, re.IGNORECASE | re.MULTILINE):
                score += 1
                if len(snippets) < 3:
                    start = max(0, m.start() - 80)
                    end = min(len(content), m.end() + 200)
                    snippets.append(content[start:end].strip())
        if score:
            hits.append({
                "label": d["label"],
                "score": score,
                "hint": d["hint"],
                "snippets": snippets,
            })

    hits.sort(key=lambda h: h["score"], reverse=True)

    # Extract the "What went wrong" block, if present
    www_match = re.search(
        r"What went wrong:[\s\S]*?(?=\nTry:|\n>|\n\* |\nBUILD FAILED|\Z)",
        content,
    )
    what_went_wrong = www_match.group(0).strip() if www_match else None

    # Extract the last BUILD SUCCESSFUL / BUILD FAILED line
    last_status = None
    for m in re.finditer(r"BUILD (SUCCESSFUL|FAILED)", content):
        last_status = m.group(0)

    return {
        "found": True,
        "log_size_bytes": len(content),
        "what_went_wrong": what_went_wrong,
        "last_status": last_status,
        "ranked_causes": hits,
    }


def render_summary(diag: dict[str, Any], log_path: Path) -> str:
    lines: list[str] = []
    lines.append("## AndroidForge — Build Failure Diagnosis")
    lines.append("")
    if not diag.get("found"):
        lines.append(f"⚠️ Could not analyze log: {diag.get('reason', 'unknown reason')}")
        return "\n".join(lines)
    if diag.get("last_status"):
        lines.append(f"**Final status:** `{diag['last_status']}`")
        lines.append("")
    if diag.get("what_went_wrong"):
        lines.append("### What went wrong")
        lines.append("```")
        lines.append(diag["what_went_wrong"][:4000])
        lines.append("```")
        lines.append("")
    if diag.get("ranked_causes"):
        lines.append("### Likely causes (ranked)")
        lines.append("")
        for i, c in enumerate(diag["ranked_causes"], 1):
            lines.append(f"{i}. **{c['label']}** (score: {c['score']})")
            lines.append(f"   - Hint: {c['hint']}")
            if c.get("snippets"):
                lines.append("   - Sample:")
                for snip in c["snippets"][:2]:
                    snippet_clean = snip.replace("\n", " ")[:300]
                    lines.append(f"     - `{snippet_clean}`")
        lines.append("")
    else:
        lines.append("No known error signatures matched the build log. Please read it manually.")
        lines.append("")
    lines.append(f"### Build log")
    lines.append(f"Full log saved as a workflow artifact: `{log_path}`")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="AndroidForge failure diagnosis")
    parser.add_argument("--root", required=True, help="Project root path (used to find log dir)")
    parser.add_argument("--variant", default="auto")
    parser.add_argument("--log-file", default=None, help="Explicit path to build log")
    args = parser.parse_args()

    log_path: Path
    if args.log_file:
        log_path = Path(args.log_file)
    else:
        # Find the most recent build log
        log_dir = Path(args.root).parent / "androidforge-logs"
        candidates = sorted(log_dir.glob("build-*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.exists() else []
        if not candidates:
            print("ERROR: no build logs found", file=sys.stderr)
            return 1
        log_path = candidates[0]

    diag = diagnose(log_path)
    summary_md = render_summary(diag, log_path)

    print(summary_md)
    print("\n---\nJSON:\n")
    print(json.dumps(diag, indent=2))

    # Append to GITHUB_STEP_SUMMARY
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(summary_md)
            f.write("\n\n")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"diagnosis_found={'true' if diag.get('found') else 'false'}\n")
            top = diag.get("ranked_causes", [])
            top_label = top[0]["label"] if top else "unknown"
            f.write(f"primary_cause={top_label}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
