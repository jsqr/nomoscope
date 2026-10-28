"""Read a PDF as lines with layout features.

`read` gives the text layer's spans and the outline. `ocr` sends pages to
an OCR model and reads its layout blocks. `lines` joins spans, and OCR
blocks where the text layer is unreadable, into lines in reading order,
with size, weight, position, and the gap above each. `tables` reads the
OCR's tables. Lines, spans, and blocks are dicts (`nomoscope.rows.Row`).
"""
