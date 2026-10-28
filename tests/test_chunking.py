from app.graph.helpers.loaders import parse_pdf
from app.rag import bm25
from app.rag.chunking.base import count_tokens
from app.rag.chunking.headings import chunk_headings
from app.rag.chunking.recursive import split_recursive
from app.rag.chunking.resume import chunk_resume, section_name
from app.rag.chunking.router import chunk_document, pick_source_type
from scripts.make_sample_resume import SAMPLE_RESUME_LINES, build_pdf


def sample_text(tmp_path) -> str:
    pdf = tmp_path / "r.pdf"
    build_pdf(pdf)
    return parse_pdf(pdf.read_bytes())


def test_section_detection():
    assert section_name("PROJECTS") == "Projects"
    assert section_name("Work Experience:") == "Experience"
    assert section_name("Technical Skills") == "Skills"
    assert section_name("Built a projects dashboard for the team") is None


def test_resume_entries_become_separate_chunks_with_headers():
    chunks = chunk_resume("\n".join(SAMPLE_RESUME_LINES))
    headers = [h for h, _, _ in chunks]
    assert "Resume > Projects > Ocular AI" in headers
    assert "Resume > Projects > DocuChat RAG" in headers
    assert "Resume > Experience > Backend Engineer" in headers
    assert "Resume > Experience > Software Engineering Intern" in headers
    assert headers.count("Resume > Skills") == 1  # Skills stays one chunk
    assert not any("alex.morgan@example.com" in t for _, t, _ in chunks)  # contact PII dropped
    ocular = next(t for h, t, _ in chunks if h.endswith("Ocular AI"))
    assert "94% accuracy" in ocular and "DocuChat" not in ocular
    meta = next(m for h, _, m in chunks if h.endswith("Ocular AI"))
    assert meta["project"] == "Ocular AI" and meta["section"] == "Projects"


def test_resume_pdf_round_trip(tmp_path):
    chunks = chunk_resume(sample_text(tmp_path))
    headers = [h for h, _, _ in chunks]
    assert "Resume > Projects > Ocular AI" in headers
    assert "Resume > Projects > DocuChat RAG" in headers
    assert sum(h.startswith("Resume > Experience") for h in headers) == 2


def test_stable_ids_and_metadata():
    a = chunk_document(doc_id="d1", source_type="resume", source_name="r.pdf",
                       text="\n".join(SAMPLE_RESUME_LINES), base_meta={"user_id": "u", "track_id": "t"})
    b = chunk_document(doc_id="d1", source_type="resume", source_name="r.pdf",
                       text="\n".join(SAMPLE_RESUME_LINES), base_meta={"user_id": "u", "track_id": "t"})
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert a[0].chunk_id == "d1:v1:0"
    assert len({c.chunk_id for c in a}) == len(a)
    flat = a[0].flat_metadata()
    assert flat["user_id"] == "u" and flat["track_id"] == "t" and flat["doc_id"] == "d1"
    assert flat["content_hash"] and flat["chunker_version"] == "v1" and flat["kb"] == "personal"
    assert all(v is not None for v in flat.values())


MD = """# Ocular AI

Intro paragraph about the project.

## Architecture

The system has three parts.

```python
# not a heading
def predict(x):
    return model(x)
```

| Layer | Tool |
|-------|------|
| API | FastAPI |

## Results

Accuracy was 94 percent.
"""


def test_heading_chunks_keep_code_and_tables_and_paths():
    chunks = chunk_headings(MD)
    paths = [p for p, _ in chunks]
    assert "Ocular AI > Architecture" in paths and "Ocular AI > Results" in paths
    arch = next(t for p, t in chunks if p.endswith("Architecture"))
    assert "# not a heading" in arch and "def predict" in arch  # code block intact
    assert "| API | FastAPI |" in arch
    assert not any(p.endswith("not a heading") for p in paths)


def test_heading_chunks_split_long_sections_under_budget():
    long_body = " ".join(f"Sentence number {i} explains vector databases." for i in range(600))
    chunks = chunk_headings(f"# Doc\n\n## Long\n\n{long_body}\n", max_tokens=300)
    assert len(chunks) > 3
    assert all(count_tokens(t) <= 330 for _, t in chunks)
    assert all(p == "Doc > Long" for p, _ in chunks)


def test_recursive_splitter_sizes_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 80 for i in range(30))
    chunks = split_recursive(text, max_tokens=200, overlap=40)
    assert len(chunks) > 5
    assert all(count_tokens(c) <= 240 for c in chunks)
    # overlap: some text at the end of chunk n reappears at the start of chunk n+1
    assert any(chunks[i][-30:].split()[-1] in chunks[i + 1] for i in range(len(chunks) - 1))


def test_router_choices():
    assert pick_source_type("pdf", "x", is_resume=True) == "resume"
    assert pick_source_type("md", "plain text") == "project_doc"
    assert pick_source_type("txt", "just some notes\nno headings") == "notes"
    assert pick_source_type("txt", "INTRO\nbody\nDETAILS\nmore") == "project_doc"


def test_bm25_filters_after_scoring(monkeypatch):
    from app.rag import stores

    rows = [
        {"id": "a", "text": "vector database embeddings", "metadata": {"topic": "rag", "header": ""}},
        {"id": "b", "text": "vector clocks distributed systems", "metadata": {"topic": "dist", "header": ""}},
        {"id": "c", "text": "cooking pasta recipe", "metadata": {"topic": "food", "header": ""}},
    ]
    monkeypatch.setattr(stores, "get_chunks", lambda collection, where=None: rows)
    bm25.invalidate("kbx")
    hits = bm25.search("kbx", "vector", 5)
    assert {i for i, _ in hits} == {"a", "b"}
    assert [i for i, _ in bm25.search("kbx", "vector", 5, filters={"topic": "dist"})] == ["b"]
    bm25.invalidate("kbx")
