from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
EXTRACTION_VERSION = "1"


def extract_text(content: bytes, suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        result = content.decode("utf-8-sig")
    elif suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        result = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if reader.pages and not result.strip():
            raise ValueError("PDF has no extractable text; OCR is required")
    elif suffix == ".docx":
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        def block_text(block: Paragraph | Table) -> str:
            if isinstance(block, Table):
                return "\n".join(
                    "\t".join(
                        "\n".join(
                            block_text(child) for child in cell.iter_inner_content()
                        )
                        for cell in row.cells
                    )
                    for row in block.rows
                )
            return block.text

        document = Document(BytesIO(content))
        result = "\n".join(block_text(block) for block in document.iter_inner_content())
    elif suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")
        for element in soup(["script", "style", "template"]):
            element.decompose()
        result = soup.get_text(separator="\n")
    else:
        raise ValueError(f"Unsupported document format: {suffix}")
    if "\x00" in result:
        raise ValueError("Document text contains NUL characters")
    return result


@dataclass(frozen=True)
class TextChunk:
    start: int
    end: int
    text: str


def split_text(text: str, chunk_characters: int) -> list[TextChunk]:
    if chunk_characters <= 0:
        raise ValueError("Chunk size must be positive")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_characters, len(text))
        if end < len(text):
            boundary = next(
                (
                    index + 1
                    for index in range(end - 1, start, -1)
                    if text[index].isspace()
                ),
                end,
            )
            end = boundary
        piece = text[start:end]
        if piece.strip():
            chunks.append(TextChunk(start, end, piece))
        start = end
    return chunks


def discover_tree(root: Path) -> tuple[list[Path], list[Path]]:
    if not root.is_dir():
        raise NotADirectoryError(root)
    if root.is_symlink():
        raise ValueError(f"Input root must not be a symlink: {root}")
    directories = [root]
    files = []
    for path in sorted(root.rglob("*")):
        # Never follow document or directory links outside the mounted input tree.
        if path.is_symlink():
            raise ValueError(f"Input tree contains a symlink: {path}")
        if any(part.startswith("._") for part in path.relative_to(root).parts):
            continue
        if path.is_dir():
            directories.append(path)
        elif path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
    return directories, files
