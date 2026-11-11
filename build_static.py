"""Build one self-contained page with the report embedded.

Usage: python build_static.py web/report.json dist/index.html
"""
import json
import sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
report = json.loads(src.read_text())
data = json.dumps(report, separators=(",", ":")).replace("</", "<\\/")   # safe inside a script tag

page = Path("web/index.html").read_text()
marker = '<script src="https://cdnjs.cloudflare.com'
assert marker in page, "could not find where to embed the report"
page = page.replace(marker, f"<script>window.REPORT = {data};</script>\n{marker}", 1)

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(page)
print(f"static: wrote {out} ({out.stat().st_size // 1024} KB), report from {src}")
