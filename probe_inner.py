"""Check inner rects at y=139.8 (Grant Number row)."""
import fitz, sys
sys.path.insert(0, r"C:\temp")
from PDFEditableConverterAI import extract_grid, GRID_TOL

doc = fitz.open(r"C:\temp\equipment-list.pdf")
page = doc[0]
h, v, cells = extract_grid(page, tol=GRID_TOL)

print("=== Cells at y~139 (Grant Number row) ===")
for c in sorted(cells, key=lambda r: r.x0):
    if abs(c.y0 - 139.8) < 3:
        print(f"  [{c.x0:.1f},{c.y0:.1f},{c.x1:.1f},{c.y1:.1f}] w={c.width:.1f}")

print("\n=== Cells at y~248 (Table header row) ===")
for c in sorted(cells, key=lambda r: r.x0):
    if abs(c.y0 - 248) < 3:
        print(f"  [{c.x0:.1f},{c.y0:.1f},{c.x1:.1f},{c.y1:.1f}] w={c.width:.1f}")

print("\n=== Cells at y~276 (First body row) ===")
for c in sorted(cells, key=lambda r: r.x0):
    if abs(c.y0 - 276) < 3:
        print(f"  [{c.x0:.1f},{c.y0:.1f},{c.x1:.1f},{c.y1:.1f}] w={c.width:.1f}")

doc.close()
