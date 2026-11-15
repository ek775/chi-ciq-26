import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url

from databases.documents.schema import (
    check_schema,
    chunk_table,
    directories,
    directory_table_name,
    documents,
    migrate_database,
    source_key,
)
from pipelines.documents.extraction import discover_tree, extract_text, split_text
from pipelines.documents.pipeline import DocumentPipeline, main
from shared.embeddings import SentenceTextEmbedder


class Embedder:
    model_name = "test-model"

    def __init__(self):
        self.calls = []

    def embed(self, value):
        self.calls.append(value)
        return [1.0, 2.0, 3.0]


@pytest.fixture
def documents_engine():
    url = os.environ.get("TEST_DOCUMENTS_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL"
    )
    if not url:
        pytest.skip("Set TEST_DOCUMENTS_DATABASE_URL for document integration tests")
    schema = f"documents_test_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(
            text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        )
    scoped = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    )
    engine = create_engine(scoped)
    try:
        migrate_database(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def pipeline(documents_engine):
    embedder = Embedder()
    return DocumentPipeline(
        documents_engine,
        lambda: embedder,
        model_name=embedder.model_name,
        chunk_characters=12,
    )


@pytest.mark.parametrize("suffix", [".txt", ".md", ".markdown", ".TXT"])
def test_plain_text_preserves_content(suffix):
    assert (
        extract_text(b"\xef\xbb\xbf# Title\n\nBody\r\n", suffix)
        == "# Title\n\nBody\r\n"
    )


def test_html_excludes_scripts_and_styles():
    content = b"<html><head><style>hidden</style></head><body><h1>Title</h1><p>Body &amp; more</p><script>bad()</script></body></html>"
    assert extract_text(content, ".html") == "Title\nBody & more"


def test_docx_preserves_paragraph_and_table_order():
    from docx import Document

    document = Document()
    document.add_paragraph("Before")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = "Right"
    document.add_paragraph("After")
    output = BytesIO()
    document.save(output)
    assert extract_text(output.getvalue(), ".docx") == "Before\nLeft\tRight\nAfter"


def test_docx_nested_table_content_is_preserved():
    from docx import Document

    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Outer"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "Nested"
    output = BytesIO()
    document.save(output)
    extracted = extract_text(output.getvalue(), ".docx")
    assert "Outer" in extracted
    assert "Nested" in extracted


def pdf_bytes(*, blank=False, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 100 Td (PDF text) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_extraction():
    assert extract_text(pdf_bytes(), ".pdf") == "PDF text"


def test_pdf_missing_text_requires_ocr():
    with pytest.raises(ValueError, match="OCR"):
        extract_text(pdf_bytes(blank=True), ".pdf")


def test_pdf_encryption_is_explicit():
    with pytest.raises(ValueError, match="Encrypted"):
        extract_text(pdf_bytes(encrypted=True), ".pdf")


@pytest.mark.parametrize(
    ("content", "suffix", "error"),
    [
        (b"bad", ".zip", ValueError),
        (b"\xff", ".txt", UnicodeDecodeError),
        (b"a\x00b", ".txt", ValueError),
        (b"broken", ".pdf", Exception),
    ],
)
def test_invalid_inputs_fail(content, suffix, error):
    with pytest.raises(error):
        extract_text(content, suffix)


@pytest.mark.parametrize("value", ["", " \n\t", "Hello world\n\nMore words", "a" * 35])
def test_chunks_preserve_offsets_and_size(value):
    chunks = split_text(value, 10)
    for chunk in chunks:
        assert 0 <= chunk.start < chunk.end <= len(value)
        assert chunk.text == value[chunk.start : chunk.end]
        assert len(chunk.text) <= 10
    assert "".join(chunk.text for chunk in chunks).strip() == value.strip()


def test_chunk_size_is_positive():
    with pytest.raises(ValueError, match="positive"):
        split_text("text", 0)
    with pytest.raises(SystemExit):
        main(["--chunk-characters", "0"])


def test_discovery_preserves_empty_directories_and_ignores_other_files(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "UPPER.TXT").write_text("text")
    (tmp_path / "image.png").write_bytes(b"image")
    (tmp_path / "._UPPER.TXT").write_bytes(b"\x00\x05\x16\x07")
    (tmp_path / "._folder").mkdir()
    (tmp_path / "._folder" / "sidecar.txt").write_bytes(b"\x00")
    dirs, files = discover_tree(tmp_path)
    assert dirs == [tmp_path, tmp_path / "empty"]
    assert files == [tmp_path / "UPPER.TXT"]


def test_discovery_rejects_links_and_missing_root(tmp_path):
    with pytest.raises(NotADirectoryError):
        discover_tree(tmp_path / "missing")
    (tmp_path / "link.txt").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        discover_tree(tmp_path)


def test_table_names_are_safe_deterministic_and_distinct():
    keys = [
        "docs/.",
        "docs/a-b",
        "docs/a_b",
        'docs/"quoted"; DROP TABLE documents',
        "docs/" + "long" * 100,
        "docs/\u4e2d\u6587",
    ]
    names = [directory_table_name(key) for key in keys]
    assert len(set(names)) == len(keys)
    for key, name in zip(keys, names, strict=True):
        assert name == directory_table_name(key)
        assert len(name) <= 63
        assert chunk_table(name).name == name
    with pytest.raises(ValueError, match="Invalid"):
        chunk_table('documents"; DROP TABLE documents')


@pytest.mark.parametrize(
    ("namespace", "path"),
    [
        ("", "."),
        ("a/b", "."),
        ("docs", "/absolute"),
        ("docs", "../escape"),
        ("docs", "a/../b"),
        ("docs", "a//b"),
        ("docs", ""),
    ],
)
def test_invalid_source_keys(namespace, path):
    with pytest.raises(ValueError):
        source_key(namespace, path)


def test_text_embedding_implementation_is_shared():
    from pipelines.transcription.backends import SentenceTextEmbedder as LegacyEmbedder

    assert LegacyEmbedder is SentenceTextEmbedder


def test_tree_maps_to_tables_and_content(pipeline, documents_engine, tmp_path):
    (tmp_path / "Council").mkdir()
    (tmp_path / "Council" / "empty").mkdir()
    (tmp_path / "root.md").write_text("# Root")
    content = "First words.\nSecond words.\nThird."
    (tmp_path / "Council" / "minutes.txt").write_text(content)
    (tmp_path / "Council" / "blank.txt").write_text("")
    assert pipeline.ingest_tree(tmp_path) == 3
    check_schema(documents_engine)
    with documents_engine.connect() as connection:
        mappings = connection.execute(select(directories)).mappings().all()
        assert {row["relative_path"] for row in mappings} == {
            ".",
            "Council",
            "Council/empty",
        }
        assert "meetings" not in inspect(connection).get_table_names()
        for mapping in mappings:
            assert inspect(connection).has_table(mapping["table_name"])
        document = (
            connection.execute(
                select(documents).where(
                    documents.c.key == "documents/Council/minutes.txt"
                )
            )
            .mappings()
            .one()
        )
        assert document["text"] == content
        assert (
            document["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
        )
        assert document["text_embedding_dimensions"] == 3
        table = chunk_table(directory_table_name("documents/Council"))
        rows = (
            connection.execute(
                select(table)
                .where(table.c.document_key == document["key"])
                .order_by(table.c.sequence)
            )
            .mappings()
            .all()
        )
        assert len(rows) == document["chunk_count"] > 1
        assert "".join(row["text"] for row in rows) == content
        for row in rows:
            assert row["text"] == content[row["start_character"] : row["end_character"]]
            assert list(row["text_embedding"]) == [1, 2, 3]
        blank = (
            connection.execute(
                select(documents).where(documents.c.filename == "blank.txt")
            )
            .mappings()
            .one()
        )
        assert blank["chunk_count"] == 0
        assert blank["text_embedding_dimensions"] is None


def test_updates_skip_replace_and_retain_removed(pipeline, documents_engine, tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("Initial document text")
    assert pipeline.ingest_tree(tmp_path) == 1
    calls = len(pipeline.embedder.calls)
    assert pipeline.ingest_tree(tmp_path) == 0
    assert len(pipeline.embedder.calls) == calls
    source.write_text("New text")
    assert pipeline.ingest_tree(tmp_path) == 1
    table = chunk_table(directory_table_name("documents/."))
    with documents_engine.connect() as connection:
        assert connection.execute(select(documents.c.text)).scalar_one() == "New text"
        assert connection.execute(select(table.c.text)).scalar_one() == "New text"
    assert pipeline.ingest_tree(tmp_path, force=True) == 1
    source.unlink()
    assert pipeline.ingest_tree(tmp_path) == 0
    with documents_engine.connect() as connection:
        assert connection.execute(select(documents.c.text)).scalar_one() == "New text"


def test_embedding_failure_preserves_previous_content(
    pipeline, documents_engine, tmp_path
):
    source = tmp_path / "a.txt"
    source.write_text("Original")
    pipeline.ingest_tree(tmp_path)
    source.write_text("Replacement")
    pipeline.embedder.embed = lambda value: [float("nan")]
    with pytest.raises(ValueError, match="finite"):
        pipeline.ingest_tree(tmp_path)
    with documents_engine.connect() as connection:
        assert connection.execute(select(documents.c.text)).scalar_one() == "Original"


def test_inconsistent_dimensions_reject_publication(
    pipeline, documents_engine, tmp_path
):
    (tmp_path / "a.txt").write_text("First chunk and second chunk")
    vectors = iter([[1, 2, 3], [1, 2]])
    embedder = Embedder()
    embedder.embed = lambda value: next(vectors)
    pipeline.embedder_factory = lambda: embedder
    with pytest.raises(ValueError, match="Inconsistent"):
        pipeline.ingest_tree(tmp_path)
    with documents_engine.connect() as connection:
        assert connection.execute(select(documents)).all() == []


def test_unchanged_tree_does_not_load_model(documents_engine, tmp_path):
    (tmp_path / "a.txt").write_text("Content")
    model = Embedder()
    DocumentPipeline(
        documents_engine, lambda: model, model_name=model.model_name
    ).ingest_tree(tmp_path)

    def fail():
        raise AssertionError("Unchanged files must not load the model")

    pipeline = DocumentPipeline(documents_engine, fail, model_name=model.model_name)
    assert pipeline.ingest_tree(tmp_path) == 0


def test_database_failure_rolls_back_replacement(pipeline, documents_engine, tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("Original")
    pipeline.ingest_tree(tmp_path)
    source.write_text("Replacement")
    table = chunk_table(directory_table_name("documents/."))

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"INSERT INTO {table.name}"):
            raise RuntimeError("Injected insert failure")

    event.listen(documents_engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="Injected"):
            pipeline.ingest_tree(tmp_path)
    finally:
        event.remove(documents_engine, "before_cursor_execute", fail)
    with documents_engine.connect() as connection:
        assert connection.execute(select(documents.c.text)).scalar_one() == "Original"
        assert connection.execute(select(table.c.text)).scalar_one() == "Original"


def test_model_and_chunk_settings_trigger_reprocessing(
    pipeline, documents_engine, tmp_path
):
    (tmp_path / "a.txt").write_text("Some document")
    pipeline.ingest_tree(tmp_path)
    other = Embedder()
    other.model_name = "different"
    changed = DocumentPipeline(documents_engine, lambda: other, model_name="different")
    assert changed.ingest_tree(tmp_path) == 1
    resized = DocumentPipeline(
        documents_engine, lambda: other, model_name="different", chunk_characters=5
    )
    assert resized.ingest_tree(tmp_path) == 1


def test_namespaces_and_same_filenames_do_not_collide(
    pipeline, documents_engine, tmp_path
):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for directory in ("a", "b"):
        (tmp_path / directory / "same.txt").write_text(directory)
    assert pipeline.ingest_tree(tmp_path, namespace="one") == 2
    assert pipeline.ingest_tree(tmp_path, namespace="two") == 2
    with documents_engine.connect() as connection:
        assert len(connection.execute(select(documents)).all()) == 4
        assert len(connection.execute(select(directories)).all()) == 6


def test_concurrent_runs_publish_once(documents_engine, tmp_path):
    (tmp_path / "a.txt").write_text("Concurrent document")

    def run():
        embedder = Embedder()
        return DocumentPipeline(
            documents_engine, lambda: embedder, model_name=embedder.model_name
        ).ingest_tree(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: run(), range(2)))
    assert sorted(results) == [0, 1]


def test_document_cli_ingests_tree(documents_engine, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("CLI content")
    monkeypatch.setenv(
        "DOCUMENTS_DATABASE_URL",
        documents_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setattr(
        "pipelines.documents.pipeline.SentenceTextEmbedder",
        lambda *args, **kwargs: Embedder(),
    )
    main(["--input-dir", str(tmp_path), "--text-model", "test-model"])
    with documents_engine.connect() as connection:
        assert (
            connection.execute(select(documents.c.text)).scalar_one() == "CLI content"
        )


def test_parallel_document_shards_preserve_identity_and_empty_directories(
    documents_engine, tmp_path
):
    (tmp_path / "empty").mkdir()
    (tmp_path / "nested").mkdir()
    for index in range(12):
        (tmp_path / "nested" / f"{index}.txt").write_text(f"Document {index}")

    def run(index):
        return DocumentPipeline(
            documents_engine, Embedder, model_name="test-model"
        ).ingest_tree(
            tmp_path, namespace="transcripts", shard_index=index, shard_count=4
        )

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert sum(workers.map(run, range(4))) == 12
        assert sum(workers.map(run, range(4))) == 0
    with documents_engine.connect() as connection:
        assert set(connection.execute(select(documents.c.key)).scalars()) == {
            f"transcripts/nested/{index}.txt" for index in range(12)
        }
        assert set(connection.execute(select(directories.c.key)).scalars()) == {
            "transcripts/.",
            "transcripts/empty",
            "transcripts/nested",
        }


@pytest.mark.parametrize(
    "arguments",
    [["--shard-count", "0"], ["--shard-index", "-1"], ["--shard-index", "1"]],
)
def test_document_cli_rejects_invalid_shards(arguments):
    with pytest.raises(SystemExit):
        main(arguments)


@pytest.mark.skipif(
    os.environ.get("RUN_DOCUMENT_EMBEDDING_TESTS") != "1",
    reason="Set RUN_DOCUMENT_EMBEDDING_TESTS=1 to download and run the real model",
)
def test_real_embeddings_for_all_formats(documents_engine, tmp_path):
    import numpy as np
    from docx import Document

    (tmp_path / "nested").mkdir()
    (tmp_path / "a.txt").write_text("Consumer protection and city council hearings.")
    (tmp_path / "nested" / "b.md").write_text("# Public safety\n\nCommittee report.")
    (tmp_path / "nested" / "c.html").write_text("<p>Budget and finance.</p>")
    (tmp_path / "nested" / "d.pdf").write_bytes(pdf_bytes())
    document = Document()
    document.add_paragraph("Transportation and infrastructure.")
    document.save(tmp_path / "nested" / "e.docx")
    pipeline = DocumentPipeline(documents_engine, SentenceTextEmbedder)
    assert pipeline.ingest_tree(tmp_path) == 5
    assert pipeline.ingest_tree(tmp_path) == 0
    with documents_engine.connect() as connection:
        assert set(
            connection.execute(select(documents.c.text_embedding_dimensions)).scalars()
        ) == {384}
        for name in connection.execute(select(directories.c.table_name)).scalars():
            table = chunk_table(name)
            for vector in connection.execute(select(table.c.text_embedding)).scalars():
                assert len(vector) == 384
                assert np.isfinite(vector).all()
                assert np.linalg.norm(vector) == pytest.approx(1, abs=1e-6)
