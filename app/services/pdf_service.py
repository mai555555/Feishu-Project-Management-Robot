from io import BytesIO

from pypdf import PdfReader


def extract_pdf_text(pdf_bytes: bytes, *, max_pages: int = 80) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    parts: list[str] = []

    for index, page in enumerate(reader.pages[:max_pages], start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            parts.append(f"第 {index} 页\n{text}")

    return "\n\n".join(parts).strip()
