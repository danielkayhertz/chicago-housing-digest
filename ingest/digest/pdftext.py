from io import BytesIO

from pypdf import PdfReader


def pdf_text(data: bytes, max_pages: int = 60) -> str:
    """Text layer of a PDF, or "" if it has none or cannot be parsed."""
    try:
        reader = PdfReader(BytesIO(data))
        pages = reader.pages[:max_pages]
    except Exception:
        return ""
    out = []
    for page in pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return "\n".join(out)


def has_text_layer(text: str) -> bool:
    return len(text.strip()) >= 200
