"""Probe the exact border positions in equipment-list.pdf to understand
the double-border issue and find the correct inner borders."""
import fitz

doc = fitz.open(r"C:\temp\equipment-list.pdf")
page = doc[0]
drawings = page.get_drawings()

# Collect all rect edges in the table area
print("=== All rects in table header/body area (y=248-510, x=140-580) ===")
print("Focusing on VERTICAL boundaries (x positions):\n")

# Group rects by their x0 and x1 positions
x_edges = {}
for d in drawings:
    for it in d.get("items", []):
        if it[0] == "re":
            rect = it[1]
            if rect.y0 > 240 and rect.y0 < 510:
                if rect.width >= 8:
                    for x in [rect.x0, rect.x1]:
                        if 140 < x < 580:
                            x_edges.setdefault(round(x, 1), []).append(
                                f"  rect [{rect.x0:.1f},{rect.y0:.1f},{rect.x1:.1f},{rect.y1:.1f}] w={rect.width:.1f}")
                elif rect.width < 2 and rect.height > 5:
                    x_mid = (rect.x0 + rect.x1) / 2
                    if 140 < x_mid < 580:
                        x_edges.setdefault(round(x_mid, 1), []).append(
                            f"  thin_v [{rect.x0:.1f},{rect.y0:.1f},{rect.x1:.1f},{rect.y1:.1f}] w={rect.width:.1f}")

for x in sorted(x_edges.keys()):
    print(f"x={x}:")
    for desc in x_edges[x][:3]:
        print(f"  {desc}")
    if len(x_edges[x]) > 3:
        print(f"  ... and {len(x_edges[x])-3} more")

# Now show what the actual visible column boundaries should be
# by looking at the header row cells
print("\n\n=== Header row rects (y≈248-276) ===")
for d in drawings:
    for it in d.get("items", []):
        if it[0] == "re":
            rect = it[1]
            if abs(rect.y0 - 248) < 5 and abs(rect.y1 - 276) < 5 and rect.width >= 8:
                print(f"  [{rect.x0:.1f},{rect.y0:.1f},{rect.x1:.1f},{rect.y1:.1f}] w={rect.width:.1f}")

# Show the OUTER frame rects (the ones that define the visible borders)
print("\n\n=== Outer frame rects (largest rects spanning full table) ===")
for d in drawings:
    for it in d.get("items", []):
        if it[0] == "re":
            rect = it[1]
            if rect.width > 200 and rect.height > 200 and rect.y0 > 240:
                print(f"  [{rect.x0:.1f},{rect.y0:.1f},{rect.x1:.1f},{rect.y1:.1f}] w={rect.width:.1f} h={rect.height:.1f}")

doc.close()
