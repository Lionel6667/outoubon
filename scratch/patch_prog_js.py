from pathlib import Path
p = Path(__file__).resolve().parents[1] / "templates/core/progression.html"
t = p.read_text(encoding="utf-8")
old = """  document.getElementById('mistakesSummary').innerHTML =
    '<div style="text-align:center;padding:1rem;color:var(--t3)"><i class="fas fa-spinner fa-spin"></i> Analyse…</div>';
"""
if old in t:
    t = t.replace(old, "")
t = t.replace("  window._coachingAdvice = '';\n", "")
p.write_text(t, encoding="utf-8")
print("patched")
