"""Build documentation/*.pdf from documentation/source/*.md (includes, TOC, images) with headless Chrome.
usage (from the repository root): pip install markdown==3.7; python documentation/source/build_pdfs.py .
Needs Google Chrome; set CHROME to its path on Windows/Linux."""
import re
import subprocess
import sys
from pathlib import Path

import markdown

H = Path(sys.argv[1]).resolve()
SRC = H / "documentation" / "source"
OUT = H / "documentation"
CHROME = __import__("os").environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm; }
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 10pt; line-height: 1.45;
       color: #1d2733; }
h1 { font-size: 21pt; color: #1F5F8B; border-bottom: 3px solid #1F5F8B; padding-bottom: 6px; margin: 0 0 6px; }
h2 { font-size: 14pt; color: #1F5F8B; margin-top: 22px; border-bottom: 1px solid #d5dde5; padding-bottom: 3px;
     page-break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 16px; page-break-after: avoid; }
h4 { font-size: 10.5pt; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 8.6pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #1F5F8B; color: #fff; text-align: left; padding: 4px 6px; }
td { border-bottom: 1px solid #dfe5ea; padding: 3.5px 6px; vertical-align: top; }
tr:nth-child(even) td { background: #f6f8fa; }
code { font-family: Menlo, Consolas, monospace; font-size: 8.4pt; background: #eef2f5; padding: 0 3px; border-radius: 3px; }
pre { background: #f2f5f8; border: 1px solid #dfe5ea; padding: 8px 10px; border-radius: 4px; white-space: pre-wrap;
      font-size: 8.4pt; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { margin: 8px 0; padding: 6px 12px; border-left: 4px solid #c9a227; background: #fbf7e9; color: #4a3f12; }
img { max-width: 100%; max-height: 225mm; object-fit: cover; object-position: top; border: 1px solid #ccd5de;
      border-radius: 4px; display: block; margin: 8px auto 14px; page-break-inside: avoid; }
.toc { background: #f6f8fa; border: 1px solid #dfe5ea; padding: 6px 14px; border-radius: 4px; margin: 10px 0 16px; }
.toc ul { margin: 2px 0; padding-left: 18px; } .toc > ul > li > ul { display: none; }
.note { color: #5b6773; font-style: italic; }
.footer { margin-top: 24px; color: #7a8792; font-size: 8pt; border-top: 1px solid #dfe5ea; padding-top: 6px; }
"""


def section(text, anchor):
    if anchor == "integrations":
        m = re.search(r"## External integrations\n(.*?)(?=\n## )", text, re.S)
        return m.group(1) if m else text
    return text


def expand(md, base):
    def inc(m):
        ref = m.group(1).strip()
        path, _, anchor = ref.partition("#")
        p = (base / path).resolve()
        text = section(p.read_text(), anchor)
        text = re.sub(r"^(#{1,5}) ", lambda h: "#" + h.group(1) + " ", text, flags=re.M)   # demote headings
        return expand(text, p.parent)
    md = re.sub(r"\{\{include:\s*([^}]+)\}\}", inc, md)
    md = re.sub(r"```mermaid.*?```", "<p class='note'>Diagram: see the Mermaid source in documentation/supporting "
                "(renders on GitHub).</p>", md, flags=re.S)
    # images and links relative to the file they appear in → absolute file URIs for printing
    md = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: f"![{m.group(1)}]({(base / m.group(2)).resolve().as_uri()})", md)
    return md


for src in sorted(SRC.glob("0*.md")):
    md = expand(src.read_text(), src.parent)
    lines = md.split("\n")
    # TOC after the title block
    insert_at = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    lines.insert(insert_at, "[TOC]\n")
    html_body = markdown.markdown("\n".join(lines), extensions=["tables", "fenced_code", "toc", "sane_lists"],
                                  extension_configs={"toc": {"toc_depth": "2-3", "title": "Contents"}})
    title = src.stem.split("-", 1)[1].replace("-", " ")
    html = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>ShiftSense — {title}</title>"
            f"<style>{CSS}</style></head><body>{html_body}"
            f"<div class='footer'>ShiftSense · {src.stem} · synthetic data · generated from "
            f"documentation/source/{src.name}</div></body></html>")
    tmp = src.with_suffix(".html")
    tmp.write_text(html)
    pdf = OUT / (src.stem + ".pdf")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--allow-file-access-from-files",
                    f"--print-to-pdf={pdf}", tmp.as_uri()], check=True, capture_output=True, timeout=180)
    tmp.unlink()
    print(pdf.name, pdf.stat().st_size // 1024, "KB")
