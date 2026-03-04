"""
DOCX Converter — Converts Word documents to PDF.

Strategy:
  1. Primary: docx2pdf (uses Microsoft Word via COM automation — best fidelity)
  2. Fallback: LibreOffice headless (if MS Word is not available)
"""

import os
import subprocess
import tempfile
import shutil
from . import config


def _convert_with_docx2pdf(docx_path, output_pdf):
    """Convert using docx2pdf (Microsoft Word COM automation)."""
    from docx2pdf import convert as _docx2pdf_convert
    _docx2pdf_convert(docx_path, output_pdf)
    if not os.path.exists(output_pdf):
        raise RuntimeError("docx2pdf produced no output")


def _convert_with_libreoffice(docx_path, output_dir):
    """Convert using LibreOffice headless (fallback)."""
    soffice = config.LIBREOFFICE_PATH
    if not os.path.exists(soffice):
        alternatives = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            shutil.which("soffice") or "",
        ]
        soffice = next((p for p in alternatives if p and os.path.exists(p)), "")
        if not soffice:
            raise FileNotFoundError(
                "Neither Microsoft Word nor LibreOffice found. "
                "Install one of them or set LIBREOFFICE_PATH in .env"
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", tmp_dir,
            docx_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed (exit {result.returncode}): "
                f"{result.stderr}"
            )
        base_name = os.path.splitext(os.path.basename(docx_path))[0]
        tmp_pdf = os.path.join(tmp_dir, f"{base_name}.pdf")
        if not os.path.exists(tmp_pdf):
            pdfs = [f for f in os.listdir(tmp_dir) if f.endswith(".pdf")]
            if not pdfs:
                raise RuntimeError("LibreOffice produced no PDF output")
            tmp_pdf = os.path.join(tmp_dir, pdfs[0])
        final_pdf = os.path.join(output_dir, f"{base_name}.pdf")
        shutil.move(tmp_pdf, final_pdf)
    return final_pdf


def convert_docx_to_pdf(docx_path, output_dir=None):
    """Convert a DOCX file to PDF.
    
    Uses Microsoft Word (via docx2pdf) if available, otherwise falls back
    to LibreOffice headless.
    
    Args:
        docx_path: path to the .docx file
        output_dir: directory for the output PDF (default: same as input)
    
    Returns:
        path to the generated PDF file
    """
    if not os.path.exists(docx_path):
        raise FileNotFoundError(f"Input file not found: {docx_path}")

    if output_dir is None:
        output_dir = os.path.dirname(docx_path)
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(docx_path))[0]
    final_pdf = os.path.join(output_dir, f"{base_name}.pdf")

    # Strategy 1: docx2pdf (Microsoft Word)
    try:
        print("  DOCX→PDF: using Microsoft Word (docx2pdf)...")
        _convert_with_docx2pdf(docx_path, final_pdf)
        print(f"  DOCX→PDF: success → {final_pdf}")
        return final_pdf
    except Exception as e:
        print(f"  DOCX→PDF: docx2pdf failed ({e}), trying LibreOffice fallback...")

    # Strategy 2: LibreOffice headless
    return _convert_with_libreoffice(docx_path, output_dir)


def is_docx(file_path):
    """Check if a file is a DOCX document."""
    return file_path.lower().endswith((".docx", ".doc"))
