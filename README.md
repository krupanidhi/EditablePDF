# EditablePDF — Document → Editable Form Converter

Production-critical system that converts static PDF/DOCX documents into editable PDF forms with:
- **Intelligent field detection** — GPT-4o vision reads labels like a human to determine field types
- **Radio buttons** — Mutually exclusive choices (Yes/No) detected from context
- **Text field validations** — Required, numeric, currency, date, email, phone (JS embedded in PDF)
- **JSON extraction** — Read all field values from filled PDFs into structured JSON
- **Rule engine** — Cross-field, conditional, and aggregate validation rules
- **Web UI** — React app for upload, convert, extract, and validate

## Architecture

```
Vision-First + Structural Snap

PDF → GPT-4o Vision → Detect Fields → Snap to Drawn Edges → Create Widgets + JS Validation
                                                                    ↓
                                                    editable.pdf + schema.json

Filled PDF → Extract Widgets → form_data.json → Rule Engine → validation_report.json
```

## Quick Start

### 1. Configure
Copy `.env.example` to `.env` and set your Azure OpenAI credentials:
```
VITE_AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
VITE_AZURE_OPENAI_KEY=your-key
VITE_AZURE_OPENAI_DEPLOYMENT=gpt-4
```

### 2. Backend
```bash
cd backend
py -m pip install -r requirements.txt
py -m uvicorn server:app --port 8000
```

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Project Structure

```
EditablePDF/
├── .env                          # Azure OpenAI credentials (not committed)
├── .env.example                  # Template
├── README.md
├── backend/
│   ├── server.py                 # FastAPI server
│   ├── requirements.txt
│   └── src/
│       ├── config.py             # Centralized config from .env
│       ├── structural_extractor.py  # Collect drawn edges as snap targets
│       ├── vision_detector.py    # GPT-4o vision field detection
│       ├── snap_algorithm.py     # Align vision coords to PDF edges
│       ├── widget_creator.py     # Create PDF widgets with JS validation
│       ├── form_extractor.py     # Read filled PDF → JSON
│       ├── rule_engine.py        # Validate JSON against business rules
│       ├── docx_converter.py     # DOCX → PDF via LibreOffice
│       └── converter.py          # Main orchestrator
├── frontend/
│   ├── src/
│   │   ├── App.tsx               # Main app with Convert/Extract/Validate tabs
│   │   ├── api.ts                # API client
│   │   ├── types.ts              # TypeScript interfaces
│   │   └── components/
│   │       ├── FileUploader.tsx   # Drag & drop file upload
│   │       ├── JobTracker.tsx     # Real-time job status polling
│   │       ├── SchemaViewer.tsx   # Field schema visualization
│   │       ├── ExtractedDataViewer.tsx  # Extracted data table
│   │       └── ValidationViewer.tsx     # Validation results display
│   └── ...
├── input/                        # Source documents
├── output/                       # Generated editable PDFs
└── schemas/                      # Generated form schemas + rules
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/convert` | Convert single PDF/DOCX (multipart file upload) |
| POST | `/api/convert-folder` | Convert all files in a folder path |
| GET | `/api/jobs/{job_id}` | Poll job status |
| POST | `/api/extract` | Extract field values from filled PDF |
| POST | `/api/validate` | Validate extracted data against rules JSON |
| GET | `/api/download/{filename}` | Download output file |
