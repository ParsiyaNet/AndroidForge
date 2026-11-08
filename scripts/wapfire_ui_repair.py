#!/usr/bin/env python3
import argparse, re, zipfile
from pathlib import Path

SRC = "input/web-build-1794143587783-WAPFire-Mobile-4.1.50-Assistant-Navigation-Polish-Source.zip"
TERMS = [
    "Idle", "Monitoring", "OPEN", "darkMode", "themeToggle", "theme-toggle",
    "assistantStatus", "assistant-status", "assistant-container", "assistant-frame",
    "nav-item", "bottom-nav", "Overview", "Clients", "Events", "Settings",
    "LivLivAssistantView", "object-fit", "object-position", "canvas"
]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inspect", action="store_true")
    p.add_argument("--zip", default=SRC)
    a = p.parse_args()
    with zipfile.ZipFile(a.zip) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        print("=== ARCHIVE FILES ===")
        for n in names:
            if any(x in n.lower() for x in ("assistant", "overview", "theme", "nav", "webview", "index")) or n.lower().endswith((".css", ".html", ".js", ".kt")):
                info = z.getinfo(n)
                print(f"{n} ({info.file_size} bytes)")
        print("=== MATCH CONTEXTS ===")
        count = 0
        for n in names:
            if not n.lower().endswith((".css", ".html", ".js", ".kt", ".xml")):
                continue
            try:
                s = z.read(n).decode("utf-8")
            except Exception:
                continue
            for term in TERMS:
                start = 0
                hits = 0
                while True:
                    i = s.lower().find(term.lower(), start)
                    if i < 0: break
                    left, right = max(0, i-220), min(len(s), i+340)
                    snippet = re.sub(r"\\s+", " ", s[left:right])
                    print(f"--- {n} :: {term} @ {i} ---")
                    print(snippet[:650])
                    count += 1
                    hits += 1
                    start = i + len(term)
                    if hits >= 3 or count >= 100: break
                if count >= 100: break
            if count >= 100: break
        print(f"=== MATCHES PRINTED: {count} ===")

if __name__ == "__main__":
    main()
