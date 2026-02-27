"""Debug AR form body cell construction."""
import fitz, sys
sys.path.insert(0, r"C:\temp")
from PDFEditableConverterAI import extract_grid, GRID_TOL
from collections import Counter

doc = fitz.open(r"C:\temp\AR-project-cover-page-OPPDReview.pdf")
page = doc[0]
h, v, cells = extract_grid(page, tol=GRID_TOL)

print(f"v_pos: {[round(x,1) for x in v]}")
print(f"Total cells: {len(cells)}")

# Show all cells at y~163 (Grant Number input row)
print("\n=== y~163 cells ===")
for c in sorted(cells, key=lambda r: r.x0):
    if abs(c.y0 - 163) < 5:
        print(f"  [{c.x0:.1f},{c.y0:.1f},{c.x1:.1f},{c.y1:.1f}] w={c.width:.1f}")

# Show all cells at y~124 (Grant Number label row)
print("\n=== y~124 cells ===")
for c in sorted(cells, key=lambda r: r.x0):
    if abs(c.y0 - 124) < 3:
        print(f"  [{c.x0:.1f},{c.y0:.1f},{c.x1:.1f},{c.y1:.1f}] w={c.width:.1f}")

# Show all cells at y~217 (Site Info row)
print("\n=== y~217 cells ===")
for c in sorted(cells, key=lambda r: r.x0):
    if abs(c.y0 - 217) < 3:
        print(f"  [{c.x0:.1f},{c.y0:.1f},{c.x1:.1f},{c.y1:.1f}] w={c.width:.1f}")

doc.close()
