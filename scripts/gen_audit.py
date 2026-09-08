"""Extract UI elements from templates for audit."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates"
SKIP = {"physique_OLD", "physique_clean", "physique_backup", "feed.html"}

def extract(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    buttons = re.findall(r'<button[^>]*>(.*?)</button>', text, re.DOTALL | re.IGNORECASE)
    btn_clean = []
    for b in buttons[:40]:
        t = re.sub(r'<[^>]+>', ' ', b).strip()
        t = re.sub(r'\s+', ' ', t)[:120]
        if t:
            btn_clean.append(t)
    links = re.findall(r'href="([^"]+)"', text)
    urls = re.findall(r"url ['\"](\w+)['\"]", text)
    fetches = re.findall(r"fetch\(['\"`]([^'\"`]+)['\"`]", text)
    onclicks = re.findall(r'onclick="([^"]{1,80})"', text)
    inputs = re.findall(r'<input[^>]+name="([^"]+)"', text)
    return {
        "lines": len(text.splitlines()),
        "buttons": btn_clean,
        "links": list(dict.fromkeys(links))[:30],
        "url_names": list(dict.fromkeys(urls)),
        "fetches": list(dict.fromkeys(fetches))[:25],
        "onclicks": onclicks[:20],
        "inputs": list(dict.fromkeys(inputs)),
    }

for p in sorted(TPL.rglob("*.html")):
    if any(s in p.name for s in SKIP):
        continue
    d = extract(p)
    print("===", p.relative_to(ROOT), f"({d['lines']} lines) ===")
    if d["url_names"]:
        print("URL names:", ", ".join(d["url_names"]))
    if d["inputs"]:
        print("Form fields:", ", ".join(d["inputs"]))
    if d["fetches"]:
        print("Fetch/API:", ", ".join(d["fetches"][:15]))
    print("Buttons:", len(d["buttons"]))
    for b in d["buttons"][:12]:
        print("  -", b)
    print()
