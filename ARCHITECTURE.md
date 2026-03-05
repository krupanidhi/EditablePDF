# AI Based Universal 1-Tier Application Submission Assistant — Architecture

## Overview

AI Based Universal 1-Tier Application Submission Assistant is a full-stack application that converts static PDF/DOCX documents into editable PDF forms, extracts form data, validates it against business rules, and applies **Digitalization Workflow** rules (required, integer-only, max length, delete fields, readonly, scroll). It uses Azure Document Intelligence for layout detection, PyMuPDF (fitz) for PDF manipulation, a structural edge-aware snap algorithm for pixel-perfect widget placement, and a React/TypeScript frontend with an HRSA-branded UI.

---

## Project Structure

```
EditablePDF/
├── backend/
│   ├── server.py                 # FastAPI app — all API endpoints + SPA serving
│   ├── requirements.txt          # Python dependencies
│   └── src/
│       ├── __init__.py
│       ├── config.py             # App config (dirs, Azure keys, env vars)
│       ├── converter.py          # PDF → editable PDF conversion orchestrator
│       ├── doc_intelligence_detector.py  # Azure DI field detection (primary)
│       ├── docx_converter.py     # DOCX → PDF (MS Word COM / LibreOffice)
│       ├── structural_extractor.py  # Extract drawn edges, rects, major edges
│       ├── snap_algorithm.py     # Snap detected fields to structural grid
│       ├── widget_creator.py     # PyMuPDF widget creation (text, radio, checkbox)
│       ├── quality_audit.py      # Post-conversion 508 & widget quality audit
│       ├── accessibility.py      # Section 508 / PDF/UA accessibility helpers
│       ├── extract_fields.py     # Extract field metadata (AcroForm + XFA)
│       ├── apply_required.py     # Apply Digitalization Workflow rules to PDF
│       ├── form_extractor.py     # Extract filled form data values
│       ├── dynamic_rows.py       # Add/remove rows for table-based PDFs
│       ├── rule_engine.py        # Business rule validation engine
│       ├── rules_generator.py    # Auto-generate validation rules from schemas
│       ├── vision_detector.py    # GPT-4o Vision field detection (fallback)
│       └── xfa_equipment_list.py # XFA form handling
├── frontend/
│   ├── vite.config.ts            # Vite dev server + proxy to backend
│   ├── package.json              # Node dependencies
│   ├── tsconfig.json
│   └── src/
│       ├── App.tsx               # Main app — HRSA-branded tabbed UI
│       ├── api.ts                # API client (axios, all endpoints)
│       ├── types.ts              # TypeScript type definitions
│       ├── main.tsx              # React entry point
│       ├── index.css             # Tailwind CSS imports
│       └── components/
│           ├── RequiredFieldsTab.tsx   # Digitalization Workflow tab
│           ├── FileUploader.tsx        # Drag-and-drop file upload (react-dropzone)
│           ├── ExtractedDataViewer.tsx # View extracted form data as table
│           ├── JobTracker.tsx          # Conversion job tracker (polling, audit, download)
│           ├── SchemaViewer.tsx        # View/edit field schemas
│           └── ValidationViewer.tsx    # View validation results
├── editable pdfs/          # Source editable PDFs for testing
├── input/                  # Uploaded files (temp, per-job subdirs)
├── output/                 # Generated output PDFs + schemas
├── schemas/                # Field schemas (JSON)
├── rules/                  # Validation rules (JSON)
├── di_cache/               # Azure DI response cache (hash-keyed)
├── .env                    # Environment config (not committed)
├── .env.example            # Template for .env
└── .gitignore
```

---

## Tech Stack

| Layer      | Technology                                              |
|------------|---------------------------------------------------------|
| Frontend   | React 19, TypeScript 5.9, Vite 8, TailwindCSS 4        |
| UI/UX      | Lucide React (icons), react-hot-toast, react-dropzone   |
| HTTP       | Axios (5-min timeout for large PDFs)                    |
| Backend    | Python 3.11+, FastAPI, Uvicorn                          |
| PDF Engine | PyMuPDF (fitz) ≥1.24.0                                  |
| AI/OCR     | Azure Document Intelligence (prebuilt-layout) — primary |
| AI/Vision  | Azure OpenAI GPT-4 Vision — fallback field detection    |
| DOCX→PDF   | Microsoft Word (Win32 COM subprocess) or LibreOffice    |

---

## Backend API Endpoints

### PDF Conversion
| Method | Path                  | Description                               |
|--------|-----------------------|-------------------------------------------|
| POST   | `/api/convert`        | Convert a single PDF/DOCX to editable PDF (async job) |
| POST   | `/api/convert-folder` | Batch convert all PDFs/DOCX in a folder (async job) |
| GET    | `/api/jobs/{id}`      | Get job status, results, audit report     |

### Data Extraction & Validation
| Method | Path                  | Description                                |
|--------|-----------------------|--------------------------------------------|
| POST   | `/api/extract`        | Extract filled form data from editable PDF |
| POST   | `/api/validate`       | Validate extracted data against rules JSON |
| POST   | `/api/generate-rules` | Auto-generate validation rules from schema |

### Digitalization Workflow
| Method | Path                  | Description                                           |
|--------|-----------------------|-------------------------------------------------------|
| POST   | `/api/extract-fields` | Extract field metadata from editable PDF as clean JSON |
| POST   | `/api/apply-required` | Apply Digitalization Workflow rules and regenerate PDF |

### Utility
| Method | Path                       | Description                                         |
|--------|----------------------------|-----------------------------------------------------|
| GET    | `/api/health`              | Health check (status, version, Azure config status) |
| GET    | `/api/download/{filename}` | Download generated PDF or schema file               |
| POST   | `/api/add-rows`            | Add dynamic row support to table-based PDFs         |

---

## Key Modules

### `converter.py` — Conversion Orchestrator

Main pipeline that converts static PDF/DOCX to editable PDF forms.

**Pipeline steps:**
1. Accept PDF or DOCX input; if DOCX, convert to PDF via `docx_converter.py`
2. Detect fields using Azure Document Intelligence (primary) or GPT-4 Vision (fallback)
3. For each page:
   - Extract structural snap targets (drawn edges, rects, text positions)
   - Pre-detect bracket-pattern fields (`[ ]` radio buttons, `[_]` checkboxes)
   - Snap & merge DI-detected table cells to structural rects (`_snap_and_merge_di_cells`)
   - Detect structural gap fields (empty areas between labels)
   - Create widgets (text fields, radio buttons, checkboxes) via `widget_creator.py`
4. Apply Section 508 accessibility tags
5. Output: editable PDF + `form_schema.json`

**Key internal functions:**
- **`_detect_bracket_fields()`** — Regex-based detection of bracket patterns in text spans. Groups consecutive brackets into radio button groups. Handles split bracket spans across multiple text elements and inline Yes/No patterns.
- **`_snap_and_merge_di_cells()`** — Two-tier snapping of DI cell bboxes to actual drawn boundaries:
  1. First tries matching to structural rects (height ≥ 18pt, preferring empty rects over label-containing rects)
  2. Falls back to reconstructing cell boundaries from **range-aware major edges** (h/v edges with total segment span ≥ 20pt)
  - Merges adjacent DI cells in the same row that share boundaries, readonly status, and compatible labels
  - Sets `_no_inset = True` so widgets fill 100% of the cell area
  - Prevents merging readonly cells (e.g., HRSA "Grant Number" vs "Application Tracking Number" stay separate)
- **`_detect_structural_gap_fields()`** — Finds empty rectangular areas between text labels and page edges that likely represent input fields
- **`_detect_signature_date_fields()`** — Detects "Signature:", "Date:", "Printed Name", "Title", "Witness" labels near horizontal drawn lines or inside containing rects. Creates text/date fields on the line area. Handles both Strategy 1 (label above a drawn rule) and Strategy 2 (label inside a rect with field extending to the right edge).
- **`_detect_numbered_list_fields()`** — Detects numbered/lettered list items with trailing blanks: `1. Organization Name: ______`, `a) Contact Person: ______`. Extends fields to containing rect edges.
- **`_detect_dropdown_fields()`** — Detects parenthetical option lists: `Type (Owned/Leased/Rented):`, `Status (Active, Inactive, Pending)`. Creates dropdown (combobox) widgets with the parsed options. Supports `/` and `,` separators; rejects single options, long instruction text, and missing separators.
- **`_detect_freeform_blank_areas()`** — Detects large vertical gaps (>50pt) between text lines where the text above is a prompt (ends with `:` or contains keywords like "describe", "explain", "provide"). Creates textarea fields in the white space. Runs last to avoid overlapping previously detected fields.
- **`_detect_checkbox_grid()`** — Detects checkbox grid/matrix patterns where Unicode ballot-box characters (☐, □, ○, etc.) are arranged in rows of 3+. Matches column positions to header text above and row labels to the left. Creates individually-named checkboxes with combined `row: column` labels.

### `doc_intelligence_detector.py` — Azure DI Field Detection

Primary field detection engine using Azure Document Intelligence's `prebuilt-layout` model.

**Capabilities:**
- Table structure detection with cell-level bounding boxes and row/column headers
- Key-value pair detection (label → value relationships)
- Selection mark detection (checkboxes, radio buttons with selected/unselected state)
- Deterministic results (same input → same output, cached by content hash)

**DI result caching:** Results are cached in `di_cache/` keyed by SHA-256 hash of the PDF content. Subsequent runs skip the Azure API call.

**Table cell processing:**
- Identifies header cells (`_is_header_cell`) and skips them
- Detects "FOR HRSA USE ONLY" internal-use sections and marks their cells as `_readonly`
- Assigns labels from column headers or row labels (`_label_source: "col_header" | "row_label"`)
- Tracks `_row_label` for merge-time label resolution

### `structural_extractor.py` — PDF Structural Analysis

Extracts all drawn edges, rectangles, and text positions from a PDF page as snap targets.

**Output dict:**
- `h_edges` — Sorted list of all horizontal edge y-coordinates (from lines, rects, thin border rects)
- `v_edges` — Sorted list of all vertical edge x-coordinates
- `major_h_edges` — List of `(y, x_min, x_max)` tuples for border-grade horizontal edges (total segment span ≥ 20pt). These represent real cell/table borders, not inner shading fills.
- `major_v_edges` — List of `(x, y_min, y_max)` tuples for border-grade vertical edges
- `rects` — List of `fitz.Rect` objects (all drawn rectangles ≥ 5×5pt)
- `text_positions` — List of `{x0, y0, x1, y1, text}` dicts for all text spans

**Major edge filtering:** Segments at the same coordinate are grouped. Only edges whose total span ≥ 20pt qualify as "major." This distinguishes real cell borders (spanning the full column/row height) from inner shading/fill edges (short segments). Each major edge carries its range (y_min/y_max for v_edges, x_min/x_max for h_edges) so consumers can filter by page region.

### `docx_converter.py` — DOCX → PDF Conversion

Converts Word documents to PDF using a two-strategy approach:

1. **Primary: Microsoft Word** — Spawns a separate Python subprocess that uses Win32 COM (`win32com.client`) to open the DOCX in Word and export as PDF. Running in a subprocess avoids COM apartment threading issues with async servers. Auto-retries once if the first attempt fails (kills lingering WINWORD.EXE).
2. **Fallback: LibreOffice** — Uses `soffice --headless --convert-to pdf` if Microsoft Word is not available. Searches multiple installation paths.

### `quality_audit.py` — Post-Conversion Quality Audit

Runs comprehensive checks on generated editable PDFs. Called automatically after conversion and results are included in the `JobTracker` UI.

**Section 508 checks:** Document language, tagged PDF, document title, display title, structure tree, tab order, widget tooltips, bookmarks.

**Widget property checks:** Font size range, scroll enablement, border styling, background fill.

**Output:** Returns `{ checks[], summary: { passed, failed, warnings, total, score }, fields_summary, total_widgets }`. The `score` is a percentage (passed / total checks).

### `widget_creator.py` — PDF Widget Creation

Creates PDF form widgets during conversion using PyMuPDF.

**Widget types:**
- **Text fields** — With configurable max length, border, fill color, font size
- **Textareas** — With character counter widgets ("X of N max"), keystroke scripts for live counter updates
- **Checkboxes** — With proper on/off values
- **Radio buttons** — Grouped by question with individual option buttons
- **Dropdowns (Combobox)** — With predefined option lists, detected from parenthetical patterns

**Key features:**
- `_no_inset` flag support: when set, widgets fill 100% of the cell bbox without padding (used for DI-snapped cells)
- `_readonly` flag: grey background, read-only field flag
- Automatic tooltip (`/TU`) generation for accessibility
- Font size auto-calculation based on cell height

### `extract_fields.py`

Extracts field metadata from editable PDFs. Supports both **AcroForm** (standard) and **XFA** (XML Forms Architecture) PDFs.

**AcroForm extraction**: Iterates `page.widgets()` via PyMuPDF, captures field name, label, type, page, position, flags (required, readonly), and options for dropdowns/radios.

**XFA extraction**: Detects XFA via `/AcroForm` → `/XFA` key. Parses the XFA XML template stream using `xml.etree.ElementTree` to extract field names, types, and properties.

**Output**: Returns `{ metadata, fields[] }` where each field has:
- `field_id` — normalized snake_case identifier
- `field_name` — internal PDF field name
- `label` — display label
- `type` — text, textarea, checkbox, radio
- `page` — 1-indexed page number
- `required` — boolean
- `readonly` — boolean
- `data_type` — "text" (default), "integer", "number", "date", etc.
- `max_length` — number or null (character limit)
- `deleted` — boolean (mark for removal)

### `apply_required.py`

Applies user-configured **Doc Digitalization** rules to an editable PDF. This is the most complex module.

**Features implemented:**
1. **Required fields** — Sets `PDF_FIELD_IS_REQUIRED` flag
2. **Red border on open** — OpenAction JS highlights empty required fields when document opens
3. **Save/Print blocking** — WillSave/WillPrint JS blocks if required fields are empty, shows alert listing missing fields
4. **Close warning** — WillClose JS warns about empty required fields
5. **Integer-only input** — `AFNumber_Keystroke(0,0,0,0,"",true)` via `widget.script_stroke`
6. **Max length** — Sets PDF `/MaxLen` property + JS keystroke guard; for textareas with counters, updates both the keystroke script and counter display label dynamically (e.g. "0 of 2000 max")
7. **Delete fields** — Removes widget annotations from page `/Annots` array (including all radio group children)
8. **Readonly enforcement** — Sets `PDF_FIELD_IS_READ_ONLY`, skips all rules
9. **Multiline scroll** — Converts single-line text fields to multiline with fixed font for visible scrollbar
10. **Tab order** — Reorders `/Annots` array by widget position (top-to-bottom, left-to-right), sets `/Tabs /R`

**Known limitation:** Adobe Acrobat does not repaint widget `strokeColor` changes from JavaScript event handlers. Red borders only appear on document open (via OpenAction); they do not dynamically clear on blur/keystroke. Save and print are still blocked with alerts.

**Architecture of JS injection:**

```
Document Level:
  /Names/JavaScript  → app.setInterval to keep doc.dirty=true
  /OpenAction        → Check all required fields, set red/gray borders
  /AA /WS            → WillSave: block save + alert listing missing fields
  /AA /WP            → WillPrint: block print + alert listing missing fields
  /AA /DC            → WillClose: warn about empty fields

Per-Widget Level (via PyMuPDF widget API):
  widget.script_stroke → /AA /K: AFNumber_Keystroke (integer), max length guard,
                                  textarea counter update
  widget.script_format → /AA /F: AFNumber_Format for integer fields
```

**Execution order in `apply_required()`:**
1. Build field_id → metadata lookup from user JSON
2. Iterate all widgets, resolve field metadata (type-aware for shared labels)
3. Collect xrefs of deleted fields
4. Set readonly/required flags
5. `_prepare_text_scroll(widget)` — set multiline + DoNotScroll flags
6. Set `widget.script_stroke`, `widget.script_format` (integer, max length, counter)
7. `widget.update()` — atomically writes all changes including `/AA`
8. `_fix_font_for_scroll(doc, widget)` — surgical `/DA` font fix (preserves `/AA`)
9. Set `/MaxLen` on widget xref (after update to avoid overwrite)
10. Remove deleted xrefs from page `/Annots` arrays
11. `_inject_catalog_actions()` — document-level JS for required fields
12. `_fix_tab_order(doc, exclude_xrefs)` — reorder annotations, skip deleted

**Field resolution (`_resolve_field`):** Tries exact `field_id` match first, then suffixed variants (`_2`, `_3`, ...). Accepts optional `widget_type` to disambiguate when a text field and radio group share the same label.

### `snap_algorithm.py` — Grid Snap Algorithm

Aligns vision-detected field bounding boxes to exact PDF coordinates using structural targets. Uses `SNAP_TOLERANCE` (default 10pt) to find the nearest drawn edge. Called after field detection and before widget creation.

### `dynamic_rows.py` — Dynamic Table Row Support

Post-processes table-based editable PDFs to embed Add Row / Remove Row buttons:
- Uses existing row slots on page 1; hides text widgets for rows 2–5
- Creates new pages for rows beyond 5 (up to configurable max, default 20)
- Buttons stay at fixed position in the table header row
- All visibility flags set in one final pass after widget creation
- Compatible with Adobe Acrobat and Foxit Reader

### `server.py` — FastAPI Backend Server

FastAPI application (v2.0.0) with:
- **CORS** configured for React dev server ports (3000, 5173, 5174)
- **Async job processing** — `POST /api/convert` and `/api/convert-folder` return immediately with a `job_id`; background tasks process files via `asyncio.create_task`
- **In-memory job store** — tracks status, results, errors per job
- **Static file serving** — output directory mounted at `/files`
- **SPA serving** — if `frontend/dist/` exists, serves the production React build as a catch-all route (all non-API paths return `index.html`)
- **Cache-busting** — output filenames include UUID hashes; download endpoint sends `Cache-Control: no-cache`
- **Auto-reload** — `uvicorn --reload` for development

---

## Frontend Architecture

### UI Design

HRSA-branded interface with:
- **Header** — Navy blue (`#0B4778`) banner with HRSA logo, app title, and live API status indicator (green/red dot)
- **Card layout** — White card with red top accent (`#990000`), rounded corners, subtle shadow
- **Tab bar** — Pill-style tabs with active state highlighting
- **Background** — Light blue (`#EFF6FB`) page background

### Tab Structure (`App.tsx`)

Two-tier navigation: **Process Group tabs** (top-level) with **Sub-tabs** within each group.

**Digitalization Process:**
| Sub-Tab | Label | Icon | Purpose |
|---------|-------|------|---------|
| `convert` | Generate Editable PDF | FileUp | Upload static PDF/DOCX, convert to editable form |
| `required` | Apply Validation Rules | ListChecks | Configure field rules, regenerate PDF |

**Validation Process:**
| Sub-Tab | Label | Icon | Purpose |
|---------|-------|------|---------|
| `extract` | Extract Data | FileSearch | Extract field data from editable PDF as JSON |
| `validate` | Validate Data | ShieldCheck | Validate extracted data against rules |

### Component Details

#### `FileUploader.tsx`
Reusable drag-and-drop file upload component built on `react-dropzone`. Accepts configurable file types, supports single/multiple file selection, and shows visual drop zone with instructions.

#### `JobTracker.tsx`
Tracks async conversion jobs by polling `GET /api/jobs/{id}`. Displays:
- **Progress** — Processing/completed/failed status with animated indicators
- **Results** — Field count breakdown by type, processing time
- **Quality Audit** — Expandable Section 508 compliance report with pass/fail/warn badges and score percentage
- **Download links** — Direct download buttons for the editable PDF and schema JSON
- **Batch support** — For folder conversions, shows per-file results with individual error reporting

#### `RequiredFieldsTab.tsx`
Full 4-step Digitalization Workflow:
- **Step 1**: Upload editable PDF → calls `POST /api/extract-fields`
- **Step 2**: Configure fields in a table:
  - Toggle **Required** (checkbox)
  - Set **Data Type** (text / integer / number / date / email / phone / currency / boolean / selection)
  - Set **Max Length** (number input, text/textarea only)
  - Toggle **Readonly** (checkbox, mutually exclusive with required)
  - **Delete** field (trash icon toggle, removes widget from PDF)
  - Page filter dropdown, Select All / Deselect All buttons
- **Step 3**: Apply & Regenerate PDF → calls `POST /api/apply-required` with PDF + fields JSON
- **Step 4**: Download regenerated PDF with all rules applied

#### `ExtractedDataViewer.tsx`
Displays extracted field data in a structured table. Shows field ID, label, type, page, value, and fill status. Used by the Extract tab.

#### `SchemaViewer.tsx`
Read-only viewer for field schema JSON. Shows field definitions with bounding boxes, validation rules, groups, options, and dependencies.

#### `ValidationViewer.tsx`
Displays validation results with color-coded sections: passed rules (green), failed rules (red), warnings (yellow), and skipped rules (grey).

### State Management
- Local `useState` hooks per tab (no global state library)
- `editedFields[]` in RequiredFieldsTab tracks user modifications to field config
- API calls via `api.ts` using axios with `/api` base URL and 5-minute timeout
- Toast notifications via `react-hot-toast` for success/error feedback

### API Client (`api.ts`)

All backend communication goes through typed API functions:
- `healthCheck()` → `GET /api/health`
- `convertFile(file)` → `POST /api/convert`
- `convertFolder(path)` → `POST /api/convert-folder`
- `getJob(id)` → `GET /api/jobs/{id}`
- `extractData(file, schemaFile?)` → `POST /api/extract`
- `extractFields(file)` → `POST /api/extract-fields`
- `applyRequired(file, fieldsJson)` → `POST /api/apply-required`
- `validateData(formFile, rulesFile)` → `POST /api/validate`
- `addRows(file, count)` → `POST /api/add-rows`
- `getDownloadUrl(filename)` → URL string for download links

### Type System (`types.ts`)

All API response shapes are defined as TypeScript interfaces:
`ConvertResponse`, `FolderConvertResponse`, `Job`, `JobResult`, `AuditResult`, `AuditCheck`, `FieldSchema`, `FormSchema`, `ExtractedField`, `ExtractedData`, `ExtractedFieldClean`, `ExtractFieldsResponse`, `ApplyRequiredResponse`, `ValidationResult`, `AddRowsResponse`, `HealthCheck`

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

## Local Developer Setup

### Prerequisites

| Requirement               | Details                                                                 |
|---------------------------|-------------------------------------------------------------------------|
| **Python 3.11+**          | Required for backend. Verify: `python --version`                        |
| **Node.js 18+**           | Required for frontend. Verify: `node --version`                         |
| **npm 9+**                | Comes with Node.js. Verify: `npm --version`                             |
| **Microsoft Word** (Windows) | Primary DOCX→PDF converter. Falls back to LibreOffice if unavailable |
| **LibreOffice** (optional)| Fallback for DOCX→PDF on non-Windows or if Word is not installed        |
| **Azure subscription**    | For Document Intelligence and OpenAI services                           |
| **Git**                   | Version control. Verify: `git --version`                                |

### Step 1: Clone the Repository

```powershell
git clone https://github.com/krupanidhi/EditablePDF.git
cd EditablePDF
```

### Step 2: Configure Environment Variables

Copy the example and fill in your credentials:

```powershell
copy .env.example .env
```

Edit `.env` with your values:

```ini
# Azure OpenAI (for GPT-4 Vision fallback field detection)
VITE_AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
VITE_AZURE_OPENAI_KEY=your-openai-key
VITE_AZURE_OPENAI_DEPLOYMENT=gpt-4
AZURE_API_VERSION=2024-08-01-preview

# Azure Document Intelligence (primary field detection — REQUIRED)
VITE_AZURE_DOC_ENDPOINT=https://your-di-resource.cognitiveservices.azure.com/
VITE_AZURE_DOC_KEY=your-doc-intelligence-key

# PDF Rendering
RENDER_SCALE=2.0

# Snap Algorithm
SNAP_TOLERANCE=10.0

# Widget Styling
WIDGET_BORDER_WIDTH=0.5
WIDGET_INSET=2.0

# LibreOffice (fallback for DOCX conversion if MS Word unavailable)
LIBREOFFICE_PATH=C:\Program Files\LibreOffice\program\soffice.exe

# Server
API_HOST=0.0.0.0
API_PORT=8001
```

> **Note**: Azure Document Intelligence credentials (`VITE_AZURE_DOC_ENDPOINT`, `VITE_AZURE_DOC_KEY`) are **required** for PDF conversion. The OpenAI credentials are only used as a fallback detector.

### Step 3: Install Backend Dependencies

```powershell
cd backend
pip install -r requirements.txt
pip install azure-ai-documentintelligence   # Azure DI SDK
pip install pywin32                          # Windows only — MS Word COM
```

**Python packages** (from `requirements.txt`):
- `PyMuPDF>=1.24.0` — PDF reading/writing/widget creation
- `openai>=1.12.0` — Azure OpenAI client (Vision fallback)
- `python-dotenv>=1.0.0` — `.env` file loading
- `fastapi>=0.110.0` — REST API framework
- `uvicorn>=0.27.0` — ASGI server
- `python-multipart>=0.0.9` — File upload handling

**Additional (install manually):**
- `azure-ai-documentintelligence` — Document Intelligence client
- `azure-core` — Azure credential management (installed as DI dependency)
- `pywin32` — MS Word COM automation (Windows only, for DOCX→PDF)

### Step 4: Install Frontend Dependencies

```powershell
cd frontend
npm install
```

**Key frontend packages:**
- `react` / `react-dom` — UI framework
- `axios` — HTTP client
- `tailwindcss` / `@tailwindcss/vite` — Utility-first CSS
- `lucide-react` — Icon library
- `react-dropzone` — Drag-and-drop file upload
- `react-hot-toast` — Toast notifications

### Step 5: Create Required Directories

These are created automatically on first run, but you can pre-create them:

```powershell
mkdir input, output, schemas, rules, di_cache -Force
```

### Step 6: Start the Application

**Terminal 1 — Backend:**
```powershell
cd backend
python server.py
```
Backend runs on **http://localhost:8001**.

**Terminal 2 — Frontend (dev mode):**
```powershell
cd frontend
npm run dev
```
Frontend runs on **http://localhost:5182**.

The Vite dev server proxies `/api/*` and `/files/*` requests to `http://localhost:8001`.

### Step 7: Open the Application

Navigate to **http://localhost:5182** in your browser. The API status indicator in the header should show green.

### Production Build (Optional)

```powershell
cd frontend
npm run build
```

The built static files are placed in `frontend/dist/`. The backend's `server.py` automatically serves this directory as a SPA when it exists, so you can access everything via **http://localhost:8001** without the Vite dev server.

### Configuration Reference (`config.py`)

All config is loaded from `.env` via `python-dotenv`. Key directories:

| Variable    | Default     | Description                    |
|-------------|-------------|--------------------------------|
| `INPUT_DIR` | `./input`   | Uploaded files (temp, per-job) |
| `OUTPUT_DIR`| `./output`  | Generated PDFs and schemas     |
| `SCHEMAS_DIR`| `./schemas`| Field schema JSON files        |
| `di_cache/` | `./di_cache`| Azure DI response cache        |

---

## Section 508 Accessibility Compliance

Generated PDFs meet federal Section 508 accessibility standards (aligned with WCAG 2.0 AA and PDF/UA). All accessibility logic lives in `backend/src/accessibility.py` and is applied automatically during both PDF conversion (`converter.py`) and Digitalization Workflow (`apply_required.py`).

### What is applied

| Attribute | AcroForm | XFA | Purpose |
|---|---|---|---|
| `/Lang (en-US)` | ✅ | ✅ | Screen readers know the document language |
| `/MarkInfo << /Marked true >>` | ✅ | ✅ | Declares the PDF as tagged |
| Document title + `/DisplayDocTitle` | ✅ | ✅ | Title bar shows document name, not filename |
| `/StructTreeRoot` tag tree | ✅ | Skipped¹ | Semantic structure for assistive technology |
| `/RoleMap` | ✅ | Skipped¹ | Maps custom structure types to standard PDF types |
| `/Tabs /S` (Structure order) | ✅ | ✅ | Tab key follows logical structure order |
| `/StructParent` on widgets | ✅ | Skipped¹ | Links each widget to its structure element |
| `/TU` tooltip on all widgets | ✅ | N/A² | Screen readers announce field purpose |
| `(required)` in tooltips | ✅ | ✅³ | Required status conveyed beyond color alone |
| WCAG 4.5:1 contrast | ✅ | N/A | Counter text meets minimum contrast ratio |

¹ XFA forms: Adobe Acrobat generates the structure tree dynamically from the XFA XML template at render time. The `<assist>` elements (`<toolTip>`, `<speak>`) in the template provide accessibility info.

² AcroForm widgets get `/TU` (tooltip) set at creation time in `widget_creator.py`. Counter widgets get tooltips added by `set_counter_tooltips()`.

³ XFA required fields get `(required)` appended to `<assist><toolTip>` in the XML template.

### Structure tree layout (AcroForm)

```
/StructTreeRoot
  /RoleMap { /Document /Document /Form /Form /Sect /Sect }
  /ParentTree (number tree mapping StructParent → structure elements)
  <Document>
    <Form>
      <Sect>  (per page)
        <Form> → widget annotation (via /OBJR)
        <Form> → widget annotation
        ...
```

### Verification

Run the built-in audit to check compliance on any generated PDF:
```bash
py test_pdfua_audit.py
```

For production validation:
- **Adobe Acrobat Pro**: Edit → Accessibility → Full Check
- **PAC (PDF Accessibility Checker)**: Free tool from PDF/UA Foundation
- **Screen readers**: Test with NVDA (free) or JAWS

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
