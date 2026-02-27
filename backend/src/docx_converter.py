"""
DOCX Converter — Converts Word documents to PDF using LibreOffice headless.

Requires LibreOffice installed. Path configured in .env via LIBREOFFICE_PATH.
"""

import os
import subprocess
import tempfile
import shutil
from . import config


def convert_docx_to_pdf(docx_path, output_dir=None):
    """Convert a DOCX file to PDF using LibreOffice headless.
    
    Args:
        docx_path: path to the .docx file
        output_dir: directory for the output PDF (default: same as input)
    
    Returns:
        path to the generated PDF file
    
    Raises:
        FileNotFoundError: if LibreOffice or the input file is not found
        RuntimeError: if conversion fails
    """
    if not os.path.exists(docx_path):
        raise FileNotFoundError(f"Input file not found: {docx_path}")
    
    soffice = config.LIBREOFFICE_PATH
    if not os.path.exists(soffice):
        # Try common alternative paths
        alternatives = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            shutil.which("soffice") or "",
        ]
        soffice = next((p for p in alternatives if p and os.path.exists(p)), "")
        if not soffice:
            raise FileNotFoundError(
                "LibreOffice not found. Install it or set LIBREOFFICE_PATH in .env"
            )
    
    if output_dir is None:
        output_dir = os.path.dirname(docx_path)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Use a temp dir for LibreOffice output to avoid conflicts
    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", tmp_dir,
            docx_path,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed (exit {result.returncode}): "
                f"{result.stderr}"
            )
        
        # Find the generated PDF
        base_name = os.path.splitext(os.path.basename(docx_path))[0]
        tmp_pdf = os.path.join(tmp_dir, f"{base_name}.pdf")
        
        if not os.path.exists(tmp_pdf):
            # LibreOffice may have generated with slightly different name
            pdfs = [f for f in os.listdir(tmp_dir) if f.endswith(".pdf")]
            if not pdfs:
                raise RuntimeError("LibreOffice produced no PDF output")
            tmp_pdf = os.path.join(tmp_dir, pdfs[0])
        
        # Move to final destination
        final_pdf = os.path.join(output_dir, f"{base_name}.pdf")
        shutil.move(tmp_pdf, final_pdf)
    
    return final_pdf


def is_docx(file_path):
    """Check if a file is a DOCX document."""
    return file_path.lower().endswith((".docx", ".doc"))
