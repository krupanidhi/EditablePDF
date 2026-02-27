"""
Centralized configuration — loaded from .env, no hardcoding.
"""

import os
from dotenv import dotenv_values

# Load from project-level .env first, fall back to CEReviewTool .env
_project_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
_fallback_env = os.path.join(os.path.expanduser("~"), "CascadeProjects", "CEReviewTool", ".env")

_env_path = _project_env if os.path.exists(_project_env) else _fallback_env
_env = dotenv_values(_env_path)

# Azure OpenAI
AZURE_ENDPOINT = _env.get("VITE_AZURE_OPENAI_ENDPOINT", "")
AZURE_KEY = _env.get("VITE_AZURE_OPENAI_KEY", "")
AZURE_DEPLOYMENT = _env.get("VITE_AZURE_OPENAI_DEPLOYMENT", "gpt-4")
AZURE_API_VERSION = _env.get("AZURE_API_VERSION", "2024-08-01-preview")

# Vision model (for field detection — legacy, used as fallback)
AZURE_VISION_DEPLOYMENT = _env.get("AZURE_VISION_DEPLOYMENT", AZURE_DEPLOYMENT)

# Azure Document Intelligence (primary field detector)
AZURE_DOC_ENDPOINT = _env.get("VITE_AZURE_DOC_ENDPOINT", "")
AZURE_DOC_KEY = _env.get("VITE_AZURE_DOC_KEY", "")

# PDF rendering
RENDER_SCALE = float(_env.get("RENDER_SCALE", "2.0"))

# Snap algorithm
SNAP_TOLERANCE = float(_env.get("SNAP_TOLERANCE", "10.0"))

# Widget styling
WIDGET_BORDER_WIDTH = float(_env.get("WIDGET_BORDER_WIDTH", "0.5"))
WIDGET_INSET = float(_env.get("WIDGET_INSET", "2.0"))

# LibreOffice path (for DOCX conversion)
LIBREOFFICE_PATH = _env.get("LIBREOFFICE_PATH", r"C:\Program Files\LibreOffice\program\soffice.exe")

# Server
API_HOST = _env.get("API_HOST", "0.0.0.0")
API_PORT = int(_env.get("API_PORT", "8001"))

# Directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
INPUT_DIR = os.path.join(BASE_DIR, "input")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SCHEMAS_DIR = os.path.join(BASE_DIR, "schemas")

# Ensure directories exist
for d in [INPUT_DIR, OUTPUT_DIR, SCHEMAS_DIR]:
    os.makedirs(d, exist_ok=True)
