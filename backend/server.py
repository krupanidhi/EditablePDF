"""
FastAPI Backend Server for EditablePDF.

Endpoints:
  POST /api/convert          — Convert a single PDF/DOCX to editable PDF
  POST /api/convert-folder   — Convert all files in a folder
  POST /api/extract          — Extract form data from a filled PDF
  POST /api/extract-fields   — Extract field metadata as clean JSON
  POST /api/apply-required   — Apply required flags to PDF from fields JSON
  POST /api/validate         — Validate extracted data against rules
  GET  /api/jobs/{job_id}    — Get job status and results
  GET  /api/health           — Health check
"""

import os
import sys
import uuid
import json
import shutil
import asyncio
import traceback
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from src import config
from src.converter import convert
from src.form_extractor import extract_form_data
from src.extract_fields import extract_fields
from src.apply_required import apply_required
from src.rule_engine import RuleEngine
from src.rules_generator import generate_rules, generate_rules_for_all
from src.dynamic_rows import add_dynamic_rows

app = FastAPI(
    title="EditablePDF API",
    description="Convert PDFs and DOCX files to editable forms with validation",
    version="2.0.0",
)

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:3000", "http://127.0.0.1:5173", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store (for async processing)
jobs = {}

# Serve output files
os.makedirs(config.OUTPUT_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=config.OUTPUT_DIR), name="files")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "azure_configured": bool(config.AZURE_ENDPOINT and config.AZURE_KEY),
    }


@app.post("/api/convert")
async def convert_file(
    file: UploadFile = File(...),
):
    """Convert a single PDF or DOCX to an editable PDF.
    
    Returns job_id for async processing.
    """
    # Validate file type
    filename = file.filename or "upload.pdf"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".pdf", ".docx", ".doc"):
        raise HTTPException(400, f"Unsupported file type: {ext}. Use .pdf or .docx")
    
    # Save uploaded file
    job_id = str(uuid.uuid4())[:8]
    upload_dir = os.path.join(config.INPUT_DIR, job_id)
    os.makedirs(upload_dir, exist_ok=True)
    
    input_path = os.path.join(upload_dir, filename)
    with open(input_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # Create job
    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "input_file": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }
    
    # Process in background
    asyncio.create_task(_process_convert(job_id, input_path))
    
    return {"job_id": job_id, "status": "processing"}


async def _process_convert(job_id, input_path):
    """Background task to convert a file."""
    try:
        result = await asyncio.to_thread(convert, input_path)
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = result
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        traceback.print_exc()


@app.post("/api/convert-folder")
async def convert_folder(
    folder_path: str = Form(...),
):
    """Convert all PDF/DOCX files in a folder.
    
    Returns job_id for async processing.
    """
    if not os.path.isdir(folder_path):
        raise HTTPException(400, f"Folder not found: {folder_path}")
    
    # Find all convertible files
    files = []
    for fname in os.listdir(folder_path):
        ext = os.path.splitext(fname)[1].lower()
        if ext in (".pdf", ".docx", ".doc"):
            files.append(os.path.join(folder_path, fname))
    
    if not files:
        raise HTTPException(400, f"No PDF or DOCX files found in: {folder_path}")
    
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "input_folder": folder_path,
        "file_count": len(files),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "results": [],
        "errors": [],
        "completed": 0,
    }
    
    asyncio.create_task(_process_folder(job_id, files))
    
    return {"job_id": job_id, "status": "processing", "file_count": len(files)}


async def _process_folder(job_id, files):
    """Background task to convert multiple files."""
    for fpath in files:
        try:
            result = await asyncio.to_thread(convert, fpath)
            jobs[job_id]["results"].append({
                "file": os.path.basename(fpath),
                "result": result,
            })
        except Exception as e:
            jobs[job_id]["errors"].append({
                "file": os.path.basename(fpath),
                "error": str(e),
            })
            traceback.print_exc()
        jobs[job_id]["completed"] += 1
    
    jobs[job_id]["status"] = "completed"


@app.post("/api/extract")
async def extract_data(
    file: UploadFile = File(...),
    schema_file: Optional[UploadFile] = File(None),
):
    """Extract form field values from a filled PDF.
    
    Optionally provide the form schema JSON for field metadata enrichment.
    """
    filename = file.filename or "filled.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files can be extracted")
    
    # Save uploaded file
    tmp_dir = os.path.join(config.INPUT_DIR, f"extract_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)
    
    pdf_path = os.path.join(tmp_dir, filename)
    with open(pdf_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    schema_path = None
    if schema_file:
        schema_path = os.path.join(tmp_dir, "schema.json")
        with open(schema_path, "wb") as f:
            content = await schema_file.read()
            f.write(content)
    
    try:
        data = extract_form_data(pdf_path, schema_path)
    except Exception as e:
        raise HTTPException(500, f"Extraction failed: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    
    return data


@app.post("/api/extract-fields")
async def extract_fields_endpoint(
    file: UploadFile = File(...),
):
    """Extract field metadata from an editable PDF as clean JSON.
    
    Returns labels, field_ids, field types, values, page numbers,
    required status, data types, and readonly flags.
    """
    filename = file.filename or "editable.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    
    tmp_dir = os.path.join(config.INPUT_DIR, f"extfields_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)
    pdf_path = os.path.join(tmp_dir, filename)
    with open(pdf_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    try:
        data = extract_fields(pdf_path)
    except Exception as e:
        raise HTTPException(500, f"Field extraction failed: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    
    return data


@app.post("/api/apply-required")
async def apply_required_endpoint(
    file: UploadFile = File(...),
    fields_json: UploadFile = File(...),
):
    """Apply required flags to an editable PDF based on a fields JSON.
    
    Accepts:
      - file: The editable PDF
      - fields_json: The fields JSON with required flags set
    
    Returns the modified PDF as a download.
    """
    filename = file.filename or "editable.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    
    # Parse JSON
    try:
        fields_data = json.loads(await fields_json.read())
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid fields JSON: {e}")
    
    fields_list = fields_data.get("fields", fields_data) if isinstance(fields_data, dict) else fields_data
    
    # Debug logging
    req_count = sum(1 for f in fields_list if f.get("required"))
    int_count = sum(1 for f in fields_list if f.get("data_type") == "integer" and not f.get("readonly"))
    ro_count = sum(1 for f in fields_list if f.get("readonly"))
    print(f"[apply-required] {filename}: {len(fields_list)} fields, {req_count} required, {int_count} integer, {ro_count} readonly")
    
    # Save uploaded PDF
    tmp_dir = os.path.join(config.INPUT_DIR, f"required_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)
    pdf_path = os.path.join(tmp_dir, filename)
    with open(pdf_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    try:
        out_name = os.path.splitext(filename)[0] + f"_required_{uuid.uuid4().hex[:6]}.pdf"
        out_path = os.path.join(config.OUTPUT_DIR, out_name)
        result = apply_required(pdf_path, fields_list, out_path)
        print(f"[apply-required] Result: {result}")
        result["download_url"] = f"/api/download/{out_name}"
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Failed to apply required flags: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/api/validate")
async def validate_data(
    form_data_file: UploadFile = File(...),
    rules_file: UploadFile = File(...),
):
    """Validate extracted form data against business rules.
    
    Accepts:
      - form_data_file: JSON from /api/extract
      - rules_file: rules JSON defining validation rules
    """
    try:
        form_data = json.loads(await form_data_file.read())
        rules_config = json.loads(await rules_file.read())
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON: {e}")
    
    engine = RuleEngine(rules_config)
    result = engine.validate(form_data)
    return result


@app.post("/api/generate-rules")
async def generate_rules_endpoint(
    schema_file: Optional[UploadFile] = File(None),
    generate_all: Optional[str] = Form(None),
):
    """Generate validation rules from a schema JSON.
    
    Either:
      - Upload a schema_file to generate rules for that specific schema
      - Set generate_all=true to generate rules for ALL schemas in schemas/
    """
    if generate_all and generate_all.lower() == "true":
        try:
            results = generate_rules_for_all()
            return {
                "status": "ok",
                "generated": len(results),
                "results": results,
            }
        except Exception as e:
            raise HTTPException(500, f"Rule generation failed: {e}")
    
    if not schema_file:
        raise HTTPException(400, "Provide schema_file or set generate_all=true")
    
    try:
        schema_data = json.loads(await schema_file.read())
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON: {e}")
    
    # Write temp schema, generate rules, clean up
    tmp_path = os.path.join(config.SCHEMAS_DIR, f"_tmp_{uuid.uuid4().hex[:8]}_schema.json")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(schema_data, f)
        result = generate_rules(tmp_path)
        return result
    except Exception as e:
        raise HTTPException(500, f"Rule generation failed: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/add-rows")
async def add_rows_to_pdf(
    file: UploadFile = File(...),
    max_rows: int = Form(20),
):
    """Add dynamic row support to a table-based editable PDF.
    
    Embeds a '+ Add Row' button directly inside the PDF.
    The PDF starts with 1 visible row; clicking the button reveals
    pre-created hidden rows one at a time (up to max_rows total).
    Works in Adobe Acrobat and Foxit Reader.
    """
    filename = file.filename or "form.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    if max_rows < 2 or max_rows > 50:
        raise HTTPException(400, "max_rows must be between 2 and 50")
    
    # Save uploaded file
    tmp_dir = os.path.join(config.INPUT_DIR, f"addrows_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)
    pdf_path = os.path.join(tmp_dir, filename)
    with open(pdf_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    try:
        out_name = os.path.splitext(filename)[0] + "_dynamic.pdf"
        out_path = os.path.join(config.OUTPUT_DIR, out_name)
        result = add_dynamic_rows(pdf_path, out_path, max_rows)
        
        if "error" in result:
            raise HTTPException(400, result["error"])
        
        result["output_file"] = out_name
        result["download_url"] = f"/api/download/{out_name}"
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to add dynamic rows: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """Get job status and results."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job not found: {job_id}")
    return job


@app.get("/api/download/{filename:path}")
async def download_file(filename: str):
    """Download an output or schema file."""
    # Check output/ first, then schemas/
    for directory in [config.OUTPUT_DIR, config.SCHEMAS_DIR]:
        file_path = os.path.join(directory, filename)
        if os.path.exists(file_path):
            return FileResponse(
                file_path,
                filename=os.path.basename(file_path),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    raise HTTPException(404, f"File not found: {filename}")


# Serve frontend production build (catch-all MUST be after all API routes)
_frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.isdir(_frontend_dist):
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets")), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve frontend SPA — all non-API routes return index.html."""
        file_path = os.path.join(_frontend_dist, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_frontend_dist, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True,
    )
