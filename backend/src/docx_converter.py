"""
DOCX Converter — Converts Word documents to PDF.

Strategy:
  1. Primary: Microsoft Word via subprocess (spawns a helper process that
     uses Win32 COM — avoids COM apartment threading issues in async servers)
  2. Fallback: LibreOffice headless (if MS Word is not available)
"""

import os
import sys
import subprocess
import tempfile
import shutil
from . import config

# Inline Python script that does the COM conversion in its own process.
# This avoids all COM threading/apartment issues when called from an async
# server like FastAPI/uvicorn.
_WORD_COM_SCRIPT = r'''
import sys, os, time, subprocess

docx_path = sys.argv[1]
pdf_path  = sys.argv[2]

def convert_once():
    import win32com.client
    word = None
    doc = None
    try:
        word = win32com.client.gencache.EnsureDispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(os.path.abspath(docx_path), ReadOnly=True)
        doc.SaveAs2(os.path.abspath(pdf_path), FileFormat=17)
    finally:
        if doc:
            try: doc.Close(SaveChanges=0)
            except: pass
        if word:
            try: word.Quit()
            except: pass

try:
    convert_once()
except Exception as e:
    # Retry: kill any lingering Word and try again
    print(f"First attempt failed ({e}), retrying...", file=sys.stderr)
    try:
        subprocess.run(["taskkill", "/F", "/IM", "WINWORD.EXE"],
                       capture_output=True, timeout=10)
        time.sleep(2)
    except: pass
    convert_once()

if not os.path.exists(pdf_path):
    print("ERROR: No PDF produced", file=sys.stderr)
    sys.exit(1)
print("OK")
'''


def _convert_with_word_subprocess(docx_path, output_pdf):
    """Convert DOCX→PDF by spawning a separate Python process that uses
    Microsoft Word COM.  This avoids COM apartment model issues that occur
    when calling COM from asyncio background threads.
    """
    result = subprocess.run(
        [sys.executable, "-c", _WORD_COM_SCRIPT, docx_path, output_pdf],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 or not os.path.exists(output_pdf):
        raise RuntimeError(
            f"Word subprocess failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


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
            soffice, "--headless", "--convert-to", "pdf",
            "--outdir", tmp_dir, docx_path,
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

    Uses Microsoft Word (via subprocess COM) if available, otherwise falls
    back to LibreOffice headless.

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

    # Strategy 1: Microsoft Word via subprocess
    try:
        print("  DOCX→PDF: using Microsoft Word (subprocess)...")
        _convert_with_word_subprocess(docx_path, final_pdf)
        print(f"  DOCX→PDF: success → {final_pdf}")
        return final_pdf
    except Exception as e:
        print(f"  DOCX→PDF: Word failed ({e}), trying LibreOffice fallback...")

    # Strategy 2: LibreOffice headless
    return _convert_with_libreoffice(docx_path, output_dir)


def is_docx(file_path):
    """Check if a file is a DOCX document."""
    return file_path.lower().endswith((".docx", ".doc"))
