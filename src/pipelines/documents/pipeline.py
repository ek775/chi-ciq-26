import argparse
import hashlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Connection, Engine

from databases.documents.schema import (
    check_schema,
    connect_database,
    documents,
    ensure_directory,
    lock_key,
    source_key,
)
from shared.embeddings import SentenceTextEmbedder, TextEmbedder, validate_vector
from shared.logging import configure_logging
from shared.sharding import shard_paths, validate_shard

from .extraction import (
    EXTRACTION_VERSION,
    ArchivePdfSource,
    discover_document_sources,
    extract_text,
    split_text,
)

logger = logging.getLogger(__name__)


class DocumentPipeline:
    def __init__(
        self,
        engine: Engine,
        embedder_factory: Callable[[], TextEmbedder],
        *,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        chunk_characters: int = 1000,
    ):
        if chunk_characters <= 0:
            raise ValueError("Chunk size must be positive")
        self.engine = engine
        self.embedder_factory = embedder_factory
        self.embedder: TextEmbedder | None = None
        self.model_name = model_name
        self.chunk_characters = chunk_characters
        self.signature = f"extract:{EXTRACTION_VERSION};chars:{chunk_characters}"

    def ingest_file(
        self,
        source: Path,
        root: Path,
        *,
        namespace: str,
        force: bool = False,
        archive_pdf: ArchivePdfSource | None = None,
    ) -> bool:
        relative_path = source.relative_to(root).as_posix()
        key = source_key(namespace, relative_path)
        directory_path = source.parent.relative_to(root).as_posix()
        sidecar_key = (
            source_key(namespace, archive_pdf.text_path.relative_to(root).as_posix())
            if archive_pdf is not None and archive_pdf.text_path is not None
            else None
        )
        with self.engine.begin() as connection:
            lock_keys = [key]
            if sidecar_key is not None and sidecar_key != key:
                lock_keys.append(sidecar_key)
            for lock in sorted(lock_keys):
                lock_key(connection, f"document:{lock}")
            # Snapshot after locking so waiting workers cannot publish stale bytes.
            content = source.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            processing_signature = self.signature
            pdf_text = None
            if archive_pdf is not None:
                if digest != archive_pdf.sha256 or len(content) != archive_pdf.size:
                    raise ValueError(f"PDF does not match its archive manifest: {source}")
                if archive_pdf.text_path is not None and archive_pdf.text_path.is_file():
                    pdf_text = archive_pdf.text_path.read_bytes()
                    sidecar_digest = hashlib.sha256(pdf_text).hexdigest()
                    processing_signature += f";archive-text:{sidecar_digest}"
                elif not content:
                    pdf_text = b""
                    logger.warning("Empty PDF recorded by archive manifest: %s", source)
                elif archive_pdf.text_path is not None:
                    logger.warning(
                        "PDF text sidecar is missing; using embedded-text extraction: %s",
                        archive_pdf.text_path,
                    )
            existing = (
                connection.execute(select(documents).where(documents.c.key == key))
                .mappings()
                .one_or_none()
            )
            if (
                not force
                and existing is not None
                and existing["content_sha256"] == digest
                and existing["processing_signature"] == processing_signature
                and existing["text_embedding_model"] == self.model_name
            ):
                logger.info("Unchanged, skipping %s", key)
                self._remove_pdf_sidecar(connection, sidecar_key)
                return False
            logger.info("Processing %s", key)
            full_text = extract_text(content, source.suffix, pdf_text=pdf_text)
            if archive_pdf is not None and content and not full_text.strip():
                logger.warning("No readable text in archived PDF: %s", source)
            chunks = split_text(full_text, self.chunk_characters)
            rows = []
            dimensions = None
            for sequence, chunk in enumerate(chunks):
                if self.embedder is None:
                    self.embedder = self.embedder_factory()
                if self.embedder.model_name != self.model_name:
                    raise ValueError("Embedder model does not match configured model")
                vector = validate_vector(self.embedder.embed(chunk.text))
                if dimensions is not None and len(vector) != dimensions:
                    raise ValueError("Inconsistent document embedding dimensions")
                dimensions = len(vector)
                rows.append(
                    {
                        "document_key": key,
                        "sequence": sequence,
                        "start_character": chunk.start,
                        "end_character": chunk.end,
                        "text": chunk.text,
                        "text_embedding": vector,
                    }
                )
            table = ensure_directory(connection, namespace, directory_path)
            if existing is not None:
                connection.execute(delete(documents).where(documents.c.key == key))
            connection.execute(
                documents.insert().values(
                    key=key,
                    directory_key=source_key(namespace, directory_path),
                    relative_path=relative_path,
                    filename=source.name,
                    format=source.suffix.lower(),
                    content_sha256=digest,
                    processing_signature=processing_signature,
                    text=full_text,
                    text_embedding_model=self.model_name,
                    text_embedding_dimensions=dimensions,
                    chunk_count=len(rows),
                    updated_at=func.now(),
                )
            )
            if rows:
                connection.execute(table.insert(), rows)
            self._remove_pdf_sidecar(connection, sidecar_key)
        logger.info("Published %s (%d chunks)", key, len(rows))
        return True

    @staticmethod
    def _remove_pdf_sidecar(
        connection: Connection, sidecar_key: str | None
    ) -> None:
        if sidecar_key is None:
            return
        result = connection.execute(
            delete(documents).where(documents.c.key == sidecar_key)
        )
        if result.rowcount:
            logger.info("Removed previously indexed PDF text sidecar %s", sidecar_key)

    def ingest_tree(
        self,
        root: Path,
        *,
        namespace: str = "documents",
        force: bool = False,
        shard_index: int = 0,
        shard_count: int = 1,
    ) -> int:
        validate_shard(shard_index, shard_count)
        source_key(namespace, ".")
        directories, files, archive_pdfs = discover_document_sources(root)
        files = shard_paths(files, root, index=shard_index, count=shard_count)
        for directory in directories:
            with self.engine.begin() as connection:
                ensure_directory(
                    connection, namespace, directory.relative_to(root).as_posix()
                )
        logger.info(
            "Discovered %d directories and %d documents", len(directories), len(files)
        )
        published = 0
        for source in files:
            published += self.ingest_file(
                source,
                root,
                namespace=namespace,
                force=force,
                archive_pdf=archive_pdfs.get(source),
            )
        logger.info(
            "Document ingestion completed: %d published, %d unchanged",
            published,
            len(files) - published,
        )
        return published


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Mirror documents into directory tables"
    )
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--namespace", default="documents")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--chunk-characters", type=int, default=1000)
    parser.add_argument(
        "--text-model", default="sentence-transformers/all-MiniLM-L6-v2"
    )
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)
    if args.chunk_characters <= 0:
        parser.error("--chunk-characters must be positive")
    try:
        validate_shard(args.shard_index, args.shard_count)
    except ValueError as error:
        parser.error(str(error))
    source_key(args.namespace, ".")
    configure_logging("documents")
    root = args.input_dir or Path(
        os.environ.get("DOCUMENTS_INPUT_DIR", "data-reference")
    )
    url = os.environ.get("DOCUMENTS_DATABASE_URL") or os.environ["DATABASE_URL"]
    engine = connect_database(url)
    try:
        check_schema(engine)
        pipeline = DocumentPipeline(
            engine,
            lambda: SentenceTextEmbedder(args.text_model, device=args.device),
            model_name=args.text_model,
            chunk_characters=args.chunk_characters,
        )
        pipeline.ingest_tree(
            root,
            namespace=args.namespace,
            force=args.force,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
