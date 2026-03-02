# EditablePDF — Architecture

## Overview

EditablePDF is a full-stack application that converts static PDF documents into editable PDF forms, extracts form data, validates it against business rules, and applies field-level validation rules (required, integer-only, readonly, scroll). It uses Azure Document Intelligence for layout detection, PyMuPDF (fitz) for PDF manipulation, and a React/TypeScript frontend.

---

## Project Structure

```
EditablePDF/
├── backend/
│   ├── server.py                 # FastAPI app — all API endpoints
│   ├── requirements.txt          # Python dependencies
│   └── src/
│       ├── config.py             # App config (dirs, Azure keys)
│       ├── converter.py          # PDF → editable PDF conversion engine
│       ├── doc_intelligence_detector.py  # Azure DI field detection
│       ├── docx_converter.py     # DOCX → PDF via LibreOffice
│       ├── dynamic_rows.py       # Add rows to table-based PDFs
│       ├── extract_fields.py     # Extract form fields (AcroForm + XFA)
│       ├── apply_required.py     # Apply validation rules to PDF fields
│       ├── form_extractor.py     # Extract filled form data
│       ├── rule_engine.py        # Business rule validation engine
│       ├── rules_generator.py    # Auto-generate validation rules
│       ├── snap_algorithm.py     # Snap detected fields to grid
│       ├── structural_extractor.py  # Structural PDF analysis
│       ├── vision_detector.py    # Vision-based field detection
│       ├── widget_creator.py     # PyMuPDF widget creation helpers
│       └── xfa_equipment_list.py # XFA form handling
├── frontend/
│   ├── vite.config.ts            # Vite dev server + proxy to backend
│   ├── package.json
│   └── src/
│       ├── App.tsx               # Main app with tabbed UI
│       ├── api.ts                # API client (axios)
│       ├── types.ts              # TypeScript type definitions
│       └── components/
│           ├── RequiredFieldsTab.tsx   # Field Validation tab UI
│           ├── FileUploader.tsx        # Drag-and-drop file upload
│           ├── ExtractedDataViewer.tsx # View extracted form data
│           ├── JobTracker.tsx          # Track conversion jobs
│           ├── SchemaViewer.tsx        # View/edit field schemas
│           └── ValidationViewer.tsx    # View validation results
├── editable pdfs/          # Source editable PDFs for testing
├── input/                  # Uploaded files (temp)
├── output/                 # Generated output PDFs
├── schemas/                # Field schemas (JSON)
├── rules/                  # Validation rules (JSON)
└── di_cache/               # Azure DI response cache
```

---

## Tech Stack

| Layer      | Technology                                    |
|------------|-----------------------------------------------|
| Frontend   | React 18, TypeScript, Vite, TailwindCSS       |
| Icons      | Lucide React                                  |
| HTTP       | Axios                                         |
| Backend    | Python 3.11+, FastAPI, Uvicorn                |
| PDF Engine | PyMuPDF (fitz) ≥1.24.0                        |
| AI/OCR     | Azure Document Intelligence (prebuilt-layout) |
| AI/Vision  | OpenAI GPT-4 Vision (field detection)         |

---

## Backend API Endpoints

### PDF Conversion
| Method | Path                | Description                          |
|--------|---------------------|--------------------------------------|
| POST   | `/api/convert`      | Convert a single PDF to editable     |
| POST   | `/api/convert-folder` | Batch convert all PDFs in a folder |
| GET    | `/api/jobs/{id}`    | Get job status                       |

### Data Extraction & Validation
| Method | Path                | Description                          |
|--------|---------------------|--------------------------------------|
| POST   | `/api/extract`      | Extract filled form data from PDF    |
| POST   | `/api/validate`     | Validate extracted data against rules|

### Field Validation (New)
| Method | Path                  | Description                          |
|--------|-----------------------|--------------------------------------|
| POST   | `/api/extract-fields` | Extract field metadata from editable PDF |
| POST   | `/api/apply-required` | Apply validation rules and regenerate PDF |

### Utility
| Method | Path                      | Description                  |
|--------|---------------------------|------------------------------|
| GET    | `/api/health`             | Health check                 |
| GET    | `/api/download/{filename}`| Download generated files     |
| POST   | `/api/add-rows`           | Add rows to table-based PDFs |

---

## Key Modules

### `extract_fields.py`

Extracts field metadata from editable PDFs. Supports both **AcroForm** (standard) and **XFA** (XML Forms Architecture) PDFs.

**AcroForm extraction**: Iterates `page.widgets()` via PyMuPDF, captures field name, label, type, page, position, flags (required, readonly), and options for dropdowns/radios.

**XFA extraction**: Detects XFA via `/AcroForm` → `/XFA` key. Parses the XFA XML template stream using `xml.etree.ElementTree` to extract field names, types, and properties.

**Output**: Returns `{ metadata, fields[] }` where each field has:
- `field_id` — normalized snake_case identifier
- `field_name` — internal PDF field name
- `label` — display label
- `type` — text, checkbox, radio, dropdown, listbox
- `page` — 1-indexed page number
- `required` — boolean
- `readonly` — boolean
- `data_type` — "text" (default), can be set to "integer" by user

### `apply_required.py`

Applies user-configured validation rules to an editable PDF. This is the most complex module.

**Features implemented:**
1. **Required fields** — Sets `PDF_FIELD_IS_REQUIRED` flag
2. **Red border on empty** — OpenAction JS highlights empty required fields on open
3. **Border clearing on blur** — `widget.script_blur` JS restores original border when field is filled (WIP — Adobe trigger reliability issue)
4. **Save/Print blocking** — WillSave/WillPrint JS blocks if required fields are empty
5. **Close warning** — WillClose JS warns about empty required fields
6. **Integer-only input** — `AFNumber_Keystroke(0,0,0,0,"",true)` via `widget.script_stroke`
7. **Readonly enforcement** — Sets `PDF_FIELD_IS_READ_ONLY`, skips all validation
8. **Multiline scroll** — Converts single-line text fields to multiline with fixed font for visible scrollbar
9. **Tab order** — Reorders `/Annots` array by widget position (top-to-bottom, left-to-right), sets `/Tabs /R`

**Architecture of validation JS injection:**

```
Document Level:
  /Names/JavaScript  → app.setInterval to keep doc.dirty=true
  /OpenAction        → Check all required fields, set red/gray borders
  /AA /WS            → WillSave: block save + alert if empty
  /AA /WP            → WillPrint: block print + alert if empty  
  /AA /DC            → WillClose: warn about empty fields

Per-Widget Level (via PyMuPDF widget API):
  widget.script_blur   → /AA /Bl: clear red border on exit if filled
  widget.script_stroke → /AA /K:  AFNumber_Keystroke for integer fields
  widget.script_format → /AA /F:  AFNumber_Format for integer fields
```

**Known issue:** Adobe Acrobat does not reliably fire `/Bl` (blur) annotation actions on all field types, particularly multiline text fields. The `/K` (keystroke) with `event.willCommit` check may be a more reliable alternative. Investigation ongoing.

**Execution order in `apply_required()`:**
1. Iterate all widgets, resolve field metadata from user config
2. Set readonly/required flags
3. `_prepare_text_scroll(widget)` — set multiline + DoNotScroll flags via widget API
4. Set `widget.script_blur`, `widget.script_stroke`, `widget.script_format`
5. `widget.update()` — atomically writes all changes including `/AA`
6. `_fix_font_for_scroll(doc, widget)` — surgical `/DA` font fix (preserves `/AA`)
7. `_inject_catalog_actions()` — document-level JS
8. `_fix_tab_order(doc)` — reorder annotations per page

### `converter.py`

Main PDF conversion engine. Uses Azure Document Intelligence to detect fields in a static PDF, then creates editable form widgets using PyMuPDF. Handles text fields, checkboxes, radio buttons, dropdowns, and table structures.

### `server.py`

FastAPI application with CORS support. Proxied by Vite dev server at `localhost:5182` → `localhost:8001`. Uses `StatReload` for auto-restart on file changes. Output filenames include a unique hash to prevent browser caching.

---

## Frontend Architecture

### Tab Structure (`App.tsx`)
1. **Convert PDF** — Upload static PDF, convert to editable
2. **Extract Data** — Extract filled form data from editable PDF
3. **Validate** — Validate extracted data against rules
4. **Field Validation** — Configure required/integer/readonly per field, regenerate PDF
5. **Add Rows** — Add rows to table-based PDFs

### Field Validation Tab (`RequiredFieldsTab.tsx`)
- **Step 1**: Upload editable PDF → calls `/api/extract-fields`
- **Step 2**: Configure fields in a table:
  - Toggle **Required** (checkbox)
  - Set **Data Type** (text/integer dropdown)
  - Toggle **Readonly** (checkbox, mutually exclusive with required)
  - Page filter, Select All/Deselect All
- **Step 3**: Apply → calls `/api/apply-required` with PDF + fields JSON
- **Step 4**: Download regenerated PDF

### State Management
- Local `useState` hooks per tab (no global state)
- `editedFields[]` tracks user modifications to field config
- API calls via `api.ts` using axios with `/api` base URL

---

## PDF JavaScript Actions Reference

| Trigger   | PDF Key | When it Fires                        | Set Via                  |
|-----------|---------|--------------------------------------|--------------------------|
| Open      | OpenAction | Document opens                    | Catalog `/OpenAction`    |
| WillSave  | /AA /WS | Before save (can block)              | Catalog `/AA`            |
| WillPrint | /AA /WP | Before print (can block)             | Catalog `/AA`            |
| WillClose | /AA /DC | Before close (warning only)          | Catalog `/AA`            |
| Keystroke | /AA /K  | Each keystroke in field              | `widget.script_stroke`   |
| Format    | /AA /F  | After value committed (display fmt)  | `widget.script_format`   |
| Validate  | /AA /V  | Field loses focus (validate value)   | `widget.script`          |
| Calculate | /AA /C  | Any field value changes (if in /CO)  | `widget.script_calc`     |
| Blur      | /AA /Bl | Field loses focus                    | `widget.script_blur`     |
| Focus     | /AA /Fo | Field gains focus                    | `widget.script_focus`    |

---

## Configuration

### Environment Variables (`.env`)
```
AZURE_DI_ENDPOINT=https://...cognitiveservices.azure.com/
AZURE_DI_KEY=...
OPENAI_API_KEY=...
```

### Directories (`config.py`)
```
INPUT_DIR   = ./input
OUTPUT_DIR  = ./output
SCHEMAS_DIR = ./schemas
DI_CACHE_DIR = ./di_cache
```

---

## Running the Application

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev    # Starts on http://localhost:5182
```

The Vite dev server proxies `/api/*` requests to `http://localhost:8001`.

---

## Troubleshooting

### Stale Backend Process
If PDF generation is instant and no changes appear, check for **duplicate backend processes** on port 8001:
```powershell
netstat -ano | findstr "LISTENING" | findstr ":8001"
```
Kill any old processes and ensure only one backend is running.

### Browser Caching
Output filenames include a UUID hash (e.g., `_required_a1b2c3.pdf`) and the download endpoint sends `Cache-Control: no-cache` headers. If stale PDFs persist, hard-refresh the browser.

### Adobe Acrobat JS Debugging
Open the PDF in Adobe Acrobat, press `Ctrl+J` to open the JavaScript console. Check for errors in the console output.
