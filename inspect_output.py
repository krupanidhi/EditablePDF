import fitz

for fname in ['equipment-list_editable.pdf', 'AR-project-cover-page-OPPDReview_editable.pdf', 'eh-form-1b_editable.pdf']:
    path = f'C:/Users/KPeterson/CascadeProjects/EditablePDF/output/{fname}'
    try:
        doc = fitz.open(path)
        toc = doc.get_toc()
        print(f'\n=== {fname} ===')
        print(f'Pages: {len(doc)}, Bookmarks: {len(toc)}')
        for t in toc[:10]:
            print(f'  BM: [L{t[0]}] p{t[2]} "{t[1][:60]}"')
        if len(toc) > 10:
            print(f'  ... and {len(toc)-10} more bookmarks')
        for pi in range(len(doc)):
            page = doc[pi]
            widgets = list(page.widgets())
            print(f'  Page {pi+1}: {len(widgets)} widgets')
            for w in widgets[:8]:
                r = w.rect
                label = getattr(w, 'field_label', '') or ''
                print(f'    [{w.field_type_string:10}] name={w.field_name[:35]:35} '
                      f'rect=({r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}) '
                      f'w={r.width:.0f}x{r.height:.0f} tooltip="{label[:40]}"')
            if len(widgets) > 8:
                print(f'    ... and {len(widgets)-8} more')
        doc.close()
    except Exception as e:
        print(f'{fname}: {e}')
