# ABOUTME: Builds a real one-page PDF with a text layer, for extraction and chain tests.
# ABOUTME: pypdf writes it (plain or AES-encrypted); PdfWriter._add_object is the registration call pypdf 6 exposes.
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def make_pdf(text: str, font_is_a_number: bool = False) -> bytes:
    """font_is_a_number: a malformed /Font entry on which pypdf's extract_text raises a bare TypeError."""
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    fonts = NumberObject(5) if font_is_a_number else DictionaryObject({NameObject("/F1"): w._add_object(font)})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 10 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


def make_aes_pdf(text: str, user_password: str = "") -> bytes:
    """The same PDF under AES-128. An empty user password opens with the owner password only (no prompt);
    a non-empty one cannot be opened at all. pypdf needs its [crypto] extra to write or read either."""
    w = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(text))))
    w.encrypt(user_password, owner_password="owner", algorithm="AES-128")
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()
