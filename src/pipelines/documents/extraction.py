import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
EXTRACTION_VERSION = "2"
PDF_PAGE_LABEL = re.compile(r"(?m)^--- Page \d+(?: \(OCR\))? ---\s*$")


@dataclass(frozen=True)
class ArchivePdfSource:
    sha256: str
    size: int
    text_path: Path | None


def _archive_path(root: Path, manifest: Path, value: str, field: str) -> Path:
    relative = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"Invalid {field} in archive manifest {manifest}")
    path = manifest.parent.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"{field} escapes the archive tree: {path}")
    return path


def discover_archive_pdf_sources(root: Path) -> dict[Path, ArchivePdfSource]:
    sources = {}
    for manifest in sorted(root.rglob("manifest.json")):
        if manifest.is_symlink():
            raise ValueError(f"Input tree contains a symlink: {manifest}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise TypeError(f"Invalid archive manifest: {manifest}")
        for item in data["files"]:
            if not isinstance(item, dict):
                raise TypeError(f"Invalid file record in archive manifest: {manifest}")
            relative_path = item.get("path")
            if not isinstance(relative_path, str) or Path(relative_path).suffix.lower() != ".pdf":
                continue
            source = _archive_path(root, manifest, relative_path, "path")
            digest = item.get("sha256")
            size = item.get("bytes")
            if (
                not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or type(size) is not int
                or size < 0
            ):
                raise ValueError(f"Invalid PDF integrity metadata in {manifest}")
            text_value = item.get("text_path")
            if text_value is not None and not isinstance(text_value, str):
                raise ValueError(f"Invalid PDF text_path in {manifest}")
            text_path = (
                _archive_path(root, manifest, text_value, "text_path")
                if text_value
                else None
            )
            if text_path is not None and text_path.suffix.lower() != ".txt":
                raise ValueError(f"PDF text_path must end in .txt in {manifest}")
            entry = ArchivePdfSource(digest, size, text_path)
            if source in sources and sources[source] != entry:
                raise ValueError(f"Conflicting PDF records in archive manifests: {source}")
            sources[source] = entry
    return sources


def discover_document_sources(
    root: Path,
) -> tuple[list[Path], list[Path], dict[Path, ArchivePdfSource]]:
    directories, files = discover_tree(root)
    pdf_sources = discover_archive_pdf_sources(root)
    sidecars = {
        source.text_path
        for source in pdf_sources.values()
        if source.text_path is not None and source.text_path.is_file()
    }
    return directories, [path for path in files if path not in sidecars], pdf_sources


def extract_text(content: bytes, suffix: str, *, pdf_text: bytes | None = None) -> str:
    suffix = suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        result = content.decode("utf-8-sig")
    elif suffix == ".pdf":
        if pdf_text is not None:
            result = pdf_text.decode("utf-8-sig")
            if not PDF_PAGE_LABEL.sub("", result).strip():
                result = ""
        else:
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
