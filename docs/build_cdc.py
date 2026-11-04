#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Construit la version HTML autonome du cahier des charges du HUB.

Usage :
    python3 docs/build_cdc.py            # docs/CAHIER_DES_CHARGES.md -> docs/CAHIER_DES_CHARGES.html
    python3 docs/build_cdc.py src dst    # chemins explicites

Le HTML produit est un fichier unique : styles intégrés, aucune ressource
externe (polices système, pas d'image), imprimable en PDF via le navigateur.
Dépendance : python-markdown (pip install markdown).
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import markdown  # noqa: PLC0415
except ImportError:
    sys.exit("Dépendance manquante : pip install markdown")

CSS = """
:root {
  --bg: #ffffff; --surface: #f6f7f9; --text: #1c2430; --muted: #5b6572;
  --accent: #1257a8; --accent-soft: #e8f0fa; --border: #d8dde4;
  --code-bg: #f0f2f5; --dark: #1f2733;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; color: var(--text); background: var(--bg);
  font: 11pt/1.65 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.page { max-width: 52rem; margin: 0 auto; padding: 2.5rem 3rem 4rem; }
header.doc {
  border-bottom: 3px solid var(--accent); margin-bottom: 2.5rem; padding-bottom: 1.5rem;
}
header.doc h1 { margin: 0 0 .25rem; font-size: 1.9rem; letter-spacing: -.01em; }
header.doc .subtitle { color: var(--muted); font-size: 1.05rem; margin: 0; }
h1, h2, h3, h4 { line-height: 1.25; color: var(--dark); }
h1 { font-size: 1.5rem; margin-top: 2.8rem; padding-top: .6rem; border-top: 1px solid var(--border); }
header.doc h1 { border: none; padding: 0; }
h2 { font-size: 1.25rem; margin-top: 2.2rem; padding-left: .55rem; border-left: 4px solid var(--accent); }
h3 { font-size: 1.05rem; margin-top: 1.8rem; }
h3 + p strong:first-child { color: var(--accent); }
p { margin: .7rem 0; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .92rem; }
th, td { border: 1px solid var(--border); padding: .45rem .65rem; text-align: left; vertical-align: top; }
th { background: var(--surface); color: var(--dark); }
tr:nth-child(even) td { background: var(--surface); }
code { font: .88em/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; background: var(--code-bg); padding: .1em .35em; border-radius: 3px; }
pre { background: var(--dark); color: #e6e9ee; padding: 1rem 1.1rem; border-radius: 6px; overflow-x: auto; page-break-inside: avoid; }
pre code { background: none; color: inherit; padding: 0; font-size: .8rem; line-height: 1.45; }
blockquote { margin: 1rem 0; padding: .6rem 1rem; border-left: 4px solid var(--border); color: var(--muted); background: var(--surface); }
hr { border: none; border-top: 1px solid var(--border); margin: 2.5rem 0; }
strong { color: var(--dark); }
@media print {
  body { font-size: 10pt; }
  .page { padding: 0; max-width: none; }
  h1, h2, h3 { break-after: avoid; }
  table, pre { break-inside: avoid; }
}
"""

HTML_SHELL = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="page">
<header class="doc">
  <h1>{title}</h1>
  <p class="subtitle">{subtitle}</p>
</header>
{body}
</div>
</body>
</html>
"""


def build(src: Path, dst: Path) -> None:
    md_text = src.read_text(encoding="utf-8")

    # Détache le titre H1 et le sous-titre (ligne en gras qui suit) pour l'en-tête.
    lines = md_text.splitlines()
    title = "Cahier des charges fonctionnel — Le HUB"
    subtitle = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            rest = [l for l in lines[i + 1:] if l.strip()]
            if rest and rest[0].startswith("**") and rest[0].endswith("**"):
                subtitle = rest[0].strip("*")
            body_start = i + 1
            break

    body_md = "\n".join(lines[body_start:])
    body_html = markdown.markdown(
        body_md,
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )

    dst.write_text(
        HTML_SHELL.format(
            title=title, subtitle=subtitle, css=CSS, body=body_html
        ),
        encoding="utf-8",
    )
    print(f"OK : {dst} ({dst.stat().st_size / 1024:.0f} Ko, autonome)")


def main() -> None:
    here = Path(__file__).resolve().parent
    src = Path(sys.argv[1]) if len(sys.argv) > 2 else here / "CAHIER_DES_CHARGES.md"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "CAHIER_DES_CHARGES.html"
    build(src, dst)


if __name__ == "__main__":
    main()
