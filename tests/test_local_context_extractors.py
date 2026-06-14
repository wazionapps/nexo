"""Tests for local_context content extraction (Release A / A3 + A4).

A3: clean_text must drop the CONTENT of <style>/<script>/<head> blocks, not just
    the tags, or CSS/JS boilerplate survives as text and poisons chunks, embeddings,
    NER and facts (real noise seen: 'mso-table-lspace', 'font-family', CSS as facts).
A4: the optional parsers that extractors imports lazily (pypdf, openpyxl,
    extract_msg) must be declared in requirements.txt, or a clean bundle imports
    them, hits the try/except and returns '' — every PDF/XLSX/MSG indexed EMPTY,
    silently.
"""

from pathlib import Path

from local_context import extractors

ROOT = Path(__file__).resolve().parents[1]


def test_clean_text_drops_style_and_script_content_not_just_tags():
    html_doc = (
        "<html><head>"
        "<style>.x{font-family:Arial;color:#fff;mso-table-lspace:0pt !important}</style>"
        "</head><body>"
        "<script>var a=1;function track(){return 2;}</script>"
        "<p>Factura del proveedor X importe 1234,56 EUR</p>"
        "</body></html>"
    )
    out = extractors.clean_text(html_doc)
    # The real content survives...
    assert "Factura del proveedor X importe 1234,56 EUR" in out
    # ...but the CSS/JS boilerplate is gone.
    assert "font-family" not in out
    assert "mso-table-lspace" not in out
    assert "function" not in out
    assert "var a" not in out


def test_optional_parsers_are_declared_in_requirements():
    req = (ROOT / "src" / "requirements.txt").read_text(encoding="utf-8").lower()
    # extractors.py imports these lazily; without declaration a clean bundle
    # silently indexes every PDF/XLSX/MSG as empty.
    for package in ("pypdf", "openpyxl", "extract-msg"):
        assert package in req, f"{package} no declarado en src/requirements.txt"
