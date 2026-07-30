"""
build_manual.py — Render resources/docs/User_Manual.md to User_Manual.pdf.

The About dialog offers both the markdown and the PDF from the same tab, so the
two drift apart the moment the guide is edited without the PDF being rebuilt.
Run this after any edit to the guide:

    python tools/build_manual.py

Requires weasyprint, which is a development dependency only — the PDF ships
prebuilt and the application never renders it at runtime.
"""
import os
import sys

import markdown

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOURCE = os.path.join(_ROOT, "resources", "docs", "User_Manual.md")
_OUTPUT = os.path.join(_ROOT, "resources", "docs", "User_Manual.pdf")

# Print styling only. The on-screen rendering comes from the QSS theme, so
# nothing here needs to agree with the application's appearance.
_CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 20pt; margin: 0 0 4pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #cccccc;
     padding-bottom: 3pt; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; page-break-after: avoid; }
p, li { orphans: 2; widows: 2; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0;
        page-break-inside: avoid; }
th, td { border: 1px solid #bbbbbb; padding: 4pt 6pt; text-align: left;
         vertical-align: top; font-size: 9.5pt; }
th { background: #f0f0f0; }
blockquote { margin: 8pt 0; padding: 4pt 10pt; border-left: 3px solid #999999;
             color: #444444; }
code { font-family: "SF Mono", Consolas, monospace; font-size: 9.5pt; }
hr { border: none; border-top: 1px solid #dddddd; margin: 14pt 0; }
"""


def main() -> int:
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        print("weasyprint is not installed. Install it with: pip install weasyprint",
              file=sys.stderr)
        return 1

    with open(_SOURCE, "r", encoding="utf-8") as f:
        body = markdown.markdown(f.read(), extensions=["tables", "fenced_code"])

    html = f"<html><head><meta charset='utf-8'></head><body>{body}</body></html>"
    HTML(string=html).write_pdf(_OUTPUT, stylesheets=[CSS(string=_CSS)])
    print(f"Wrote {_OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
