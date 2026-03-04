"""
DOCX Converter — Converts Word documents to PDF.

Strategy:
  1. Primary: Microsoft Word via Win32 COM automation (best fidelity)
  2. Fallback: LibreOffice headless (if MS Word is not available)
"""

import os
import subprocess
import tempfile
import shutil
from . import config


def _word_com_convert(docx_path, output_pdf):
    """Single attempt to convert using Word COM. Raises on failure."""
    import win32com.client

    word = None
    doc = None
    try:
        word = win32com.client.gencache.EnsureDispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone

        abs_docx = os.path.abspath(docx_path)
        abs_pdf = os.path.abspath(output_pdf)

        doc = word.Documents.Open(abs_docx, ReadOnly=True)
        # wdFormatPDF = 17
        doc.SaveAs2(abs_pdf, FileFormat=17)
    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=0)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass

    if not os.path.exists(output_pdf):
        raise RuntimeError("Microsoft Word produced no PDF output")


def _convert_with_word_com(docx_path, output_pdf):
    """Convert using Microsoft Word via Win32 COM, with retry.

    If Word is already open it may reject COM calls ('Call was rejected
    by callee').  In that case we kill the running Word process and retry
    once.
    """
    import time

    try:
        _word_com_convert(docx_path, output_pdf)
    except Exception as first_err:
        # If Word was busy / already open, kill it and retry
        print(f"  DOCX→PDF: first attempt failed ({first_err}), killing Word and retrying...")
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "WINWORD.EXE"],
                capture_output=True, timeout=10,
            )
            time.sleep(2)
        except Exception:
            pass
        _word_com_convert(docx_path, output_pdf)


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

    # Strategy 1: Microsoft Word via COM automation
    try:
        print("  DOCX→PDF: using Microsoft Word (COM)...")
        _convert_with_word_com(docx_path, final_pdf)
        print(f"  DOCX→PDF: success → {final_pdf}")
        return final_pdf
    except Exception as e:
        print(f"  DOCX→PDF: Word COM failed ({e}), trying LibreOffice fallback...")

    # Strategy 2: LibreOffice headless
    return _convert_with_libreoffice(docx_path, output_dir)


def is_docx(file_path):
    """Check if a file is a DOCX document."""
    return file_path.lower().endswith((".docx", ".doc"))
