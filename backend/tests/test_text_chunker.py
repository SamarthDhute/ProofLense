import uuid
import pytest

from backend.app.services.text_chunker import TextChunkerService


def test_chunker_sentence_boundary_preservation():
    """Verify chunker respects sentence boundaries when grouping text."""
    text = (
        "Sentence one has critical context. Sentence two adds more information. "
        "Sentence three provides concluding evidence. Sentence four is a follow-up."
    )
    chunker = TextChunkerService(target_size=80, max_size=120, overlap_size=20)
    chunks = chunker.chunk_page_text(text, page_number=1)

    assert len(chunks) > 1
    for chunk in chunks:
        # Every chunk should start and end with complete sentence boundaries
        assert chunk.content.endswith((".", "!", "?"))
        # Character slices must match the content in source text
        sliced = text[chunk.char_start:chunk.char_end].strip()
        assert sliced == chunk.content


def test_chunker_max_size_enforced():
    """Verify no chunk exceeds configured max_size under any circumstances."""
    text = (
        "This is paragraph one with several statements. It explains the hypothesis. "
        "Another sentence discusses the variables involved. We also note significant variations."
    )
    chunker = TextChunkerService(target_size=60, max_size=90, overlap_size=15)
    chunks = chunker.chunk_page_text(text, page_number=1)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk.content) <= 90


def test_chunker_handles_extra_long_sentence():
    """Verify a sentence longer than max_size is safely subdivided without crashing."""
    long_sentence = (
        "This is an unusually long run-on sentence without any punctuation periods "
        "designed specifically to test the subdivision algorithm and guarantee that ProofLens AI "
        "will never raise an unhandled exception or produce a chunk larger than the configured maximum size "
        "even when processing academic papers with verbose technical descriptions."
    )
    chunker = TextChunkerService(target_size=70, max_size=100, overlap_size=20)
    chunks = chunker.chunk_page_text(long_sentence, page_number=1)

    assert len(chunks) >= 3
    for chunk in chunks:
        assert len(chunk.content) <= 100


def test_chunker_empty_and_whitespace_page():
    """Verify empty or whitespace-only text yields zero chunks."""
    chunker = TextChunkerService()
    assert chunker.chunk_page_text("", page_number=1) == []
    assert chunker.chunk_page_text("   \n\n\t  ", page_number=1) == []


def test_chunker_provenance_and_indexing():
    """Verify page_id, document_id, workspace_id, char offsets, and chunk indices are preserved."""
    page_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    ws_id = uuid.uuid4()

    text = "First claim is verified. Second claim is contradicted. Third claim is supported."
    chunker = TextChunkerService(target_size=40, max_size=60, overlap_size=10)
    chunks = chunker.chunk_page_text(
        page_text=text,
        page_number=2,
        page_id=page_id,
        document_id=doc_id,
        workspace_id=ws_id,
        start_chunk_index=5,
    )

    assert len(chunks) >= 2
    for idx, c in enumerate(chunks):
        assert c.chunk_index == 5 + idx
        assert c.page_number == 2
        assert c.page_id == page_id
        assert c.document_id == doc_id
        assert c.workspace_id == ws_id
        assert c.char_start >= 0
        assert c.char_end > c.char_start
        assert text[c.char_start:c.char_end].strip() == c.content


def test_chunker_overlap_behavior():
    """Verify chunk overlap includes trailing sentences from previous chunk."""
    text = (
        "Alpha leads to Beta. "
        "Beta leads to Gamma. "
        "Gamma leads to Delta. "
        "Delta leads to Epsilon."
    )
    chunker = TextChunkerService(target_size=45, max_size=60, overlap_size=25)
    chunks = chunker.chunk_page_text(text, page_number=1)

    assert len(chunks) >= 2
    # Check that adjacent chunks share overlapping text
    overlap_found = False
    for i in range(len(chunks) - 1):
        c1 = chunks[i].content
        c2 = chunks[i + 1].content
        words1 = set(c1.split())
        words2 = set(c2.split())
        if words1.intersection(words2):
            overlap_found = True
            break
    assert overlap_found is True


def test_chunker_determinism():
    """Verify identical text and parameters produce strictly identical chunks across runs."""
    text = "Deterministic testing is mandatory for reliable document chunking in ProofLens AI."
    chunker = TextChunkerService(target_size=40, max_size=80, overlap_size=10)

    run1 = chunker.chunk_page_text(text, page_number=1)
    run2 = chunker.chunk_page_text(text, page_number=1)

    assert len(run1) == len(run2)
    for c1, c2 in zip(run1, run2):
        assert c1.content == c2.content
        assert c1.char_start == c2.char_start
        assert c1.char_end == c2.char_end
        assert c1.chunk_index == c2.chunk_index


def test_chunker_overlap_zero_permitted():
    """Verify overlap_size=0 does not fall back to default and produces non-overlapping chunks."""
    text = "First block of text is here. Second block follows immediately. Third block concludes."
    chunker = TextChunkerService(target_size=35, max_size=50, overlap_size=0)
    assert chunker.overlap_size == 0
    chunks = chunker.chunk_page_text(text, page_number=1)
    assert len(chunks) >= 2


def test_chunker_exact_char_slice_matches_content():
    """Verify that page_text[char_start:char_end] strictly equals chunk.content without stripping."""
    text = "First claim is verified. Second claim is contradicted. Third claim is supported."
    chunker = TextChunkerService(target_size=30, max_size=50, overlap_size=10)
    chunks = chunker.chunk_page_text(text, page_number=1)
    assert len(chunks) >= 2
    for chunk in chunks:
        # Exact character offset equality
        assert text[chunk.char_start:chunk.char_end] == chunk.content

