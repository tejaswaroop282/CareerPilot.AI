"""Parse uploaded files into text. Output is untrusted data, never instructions."""
import pymupdf


def parse_pdf(data: bytes) -> str:
    """Extract text block by block (blank line between layout blocks) so entry boundaries survive."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = []
        for page in doc:
            blocks = page.get_text("blocks", sort=True)
            pages.append("\n\n".join(b[4].strip() for b in blocks if b[6] == 0 and b[4].strip()))
    return "\n\n".join(pages)


def parse_file(data: bytes, file_type: str) -> str:
    if file_type == "pdf":
        return parse_pdf(data)
    return data.decode("utf-8", errors="replace")
