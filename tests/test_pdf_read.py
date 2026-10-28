import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, Fit, NameObject

from nomoscope.pdf import read as pdf


def outlined_pdf() -> PdfReader:
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    ch = writer.add_outline_item("Chapter 1", 0, fit=Fit.xyz(top=720))
    writer.add_outline_item("§ 1-1 Intent.", 1, parent=ch, fit=Fit.xyz(top=500))
    writer.add_outline_item("Chapter 2", 2)
    buf = io.BytesIO()
    writer.write(buf)
    return PdfReader(buf)


def test_outline_rows():
    rows = pdf.outline_rows(outlined_pdf(), "code.pdf", "abc")
    assert [(r["depth"], r["title"], r["page"]) for r in rows] == [
        (0, "Chapter 1", 0),
        (1, "§ 1-1 Intent.", 1),
        (0, "Chapter 2", 2),
    ]
    assert rows[0]["top"] == 72.0
    assert rows[1]["top"] == 292.0


def text_pdf(content: bytes = b"BT /F1 11 Tf 72 700 Td (A. Permits) Tj ET") -> PdfReader:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Times-Bold"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    buf = io.BytesIO()
    writer.write(buf)
    return PdfReader(buf)


def test_span_rows():
    [row] = pdf.span_rows(text_pdf(), "code.pdf", "abc")
    assert (row["page"], row["x"], row["y"], row["size"]) == (0, 72.0, 92.0, 11.0)
    assert (row["font"], row["bold"], row["italic"], row["text"]) == (
        "Times-Bold",
        True,
        False,
        "A. Permits",
    )


def test_span_rows_blank():
    assert pdf.span_rows(outlined_pdf(), "code.pdf", "abc") == []


def test_span_rows_skip_invisible():
    content = (
        b"q 3 Tr BT /F1 11 Tf 72 700 Td (scanner ocr) Tj ET Q "
        b"BT /F1 11 Tf 72 680 Td (visible) Tj ET "
        b"3 Tr BT /F1 11 Tf 72 660 Td (hidden) Tj ET"
    )
    assert [r["text"] for r in pdf.span_rows(text_pdf(content), "code.pdf", "abc")] == ["visible"]


def test_rotated_text_keeps_its_size():
    assert pdf._scale([1.0, 0.0, 0.0, 1.0, 72.0, 700.0]) == 1.0
    assert pdf._scale([0.0, 1.0, -1.0, 0.0, 72.0, 700.0]) == 1.0
