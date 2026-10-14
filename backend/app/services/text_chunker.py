import re
from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from backend.app.core.config import settings


@dataclass(frozen=True)
class TextSentence:
    """Represents a discrete sentence or text unit with provenance offsets."""
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ChunkData:
    """Represents a persisted or in-memory document chunk with full provenance."""
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    page_number: int
    page_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    workspace_id: Optional[UUID] = None


class TextChunkerService:
    """Sentence-aware, deterministic document chunker with character-offset provenance."""

    def __init__(
        self,
        target_size: Optional[int] = None,
        max_size: Optional[int] = None,
        overlap_size: Optional[int] = None,
    ):
        self.target_size = target_size if target_size is not None else settings.CHUNK_TARGET_SIZE_CHARS
        self.max_size = max_size if max_size is not None else settings.CHUNK_MAX_SIZE_CHARS
        self.overlap_size = overlap_size if overlap_size is not None else settings.CHUNK_OVERLAP_CHARS

        if self.target_size <= 0:
            raise ValueError("target_size must be positive.")
        if self.max_size < self.target_size:
            raise ValueError("max_size cannot be smaller than target_size.")
        if self.overlap_size < 0 or self.overlap_size >= self.target_size:
            raise ValueError("overlap_size must be non-negative and strictly smaller than target_size.")

    def split_into_sentences(self, text: str) -> List[TextSentence]:
        """Split text into sentences while strictly tracking character offsets."""
        if not text or not text.strip():
            return []

        # Split on sentence boundaries [.!?] followed by whitespace or linebreaks, or paragraph breaks
        pattern = re.compile(r'.+?(?:(?<=[.!?])\s+|\n\n+|$)', re.DOTALL)
        raw_units: List[TextSentence] = []

        for match in pattern.finditer(text):
            chunk_str = match.group(0)
            if not chunk_str:
                continue
            start = match.start()
            end = match.end()
            raw_units.append(TextSentence(text=chunk_str, start=start, end=end))

        # Check for any sentences exceeding max_size and break them down safely
        refined_sentences: List[TextSentence] = []
        for unit in raw_units:
            if len(unit.text) <= self.max_size:
                refined_sentences.append(unit)
            else:
                # Sub-split long sentence by clauses or words
                sub_units = self._subdivide_long_sentence(unit)
                refined_sentences.extend(sub_units)

        return refined_sentences

    def _subdivide_long_sentence(self, sentence: TextSentence) -> List[TextSentence]:
        """Safely subdivide an oversized sentence without losing offsets or exceeding max_size."""
        text = sentence.text
        base_offset = sentence.start
        result: List[TextSentence] = []

        # Split by words
        words = re.finditer(r'\S+\s*', text)
        current_sub = ""
        sub_start_rel = 0

        for m in words:
            word_str = m.group(0)
            # If a single word itself exceeds max_size, hard-slice it
            if len(word_str) > self.max_size:
                if current_sub:
                    sub_end_rel = m.start()
                    result.append(
                        TextSentence(
                            text=current_sub,
                            start=base_offset + sub_start_rel,
                            end=base_offset + sub_end_rel,
                        )
                    )
                    current_sub = ""
                # Hard slice oversized token
                for i in range(0, len(word_str), self.target_size):
                    slice_str = word_str[i : i + self.target_size]
                    slice_start = m.start() + i
                    slice_end = slice_start + len(slice_str)
                    result.append(
                        TextSentence(
                            text=slice_str,
                            start=base_offset + slice_start,
                            end=base_offset + slice_end,
                        )
                    )
                sub_start_rel = m.end()
                continue

            if len(current_sub) + len(word_str) <= self.target_size:
                if not current_sub:
                    sub_start_rel = m.start()
                current_sub += word_str
            else:
                if current_sub:
                    sub_end_rel = m.start()
                    result.append(
                        TextSentence(
                            text=current_sub,
                            start=base_offset + sub_start_rel,
                            end=base_offset + sub_end_rel,
                        )
                    )
                current_sub = word_str
                sub_start_rel = m.start()

        if current_sub:
            result.append(
                TextSentence(
                    text=current_sub,
                    start=base_offset + sub_start_rel,
                    end=base_offset + len(text),
                )
            )

        return result

    def chunk_page_text(
        self,
        page_text: str,
        page_number: int,
        page_id: Optional[UUID] = None,
        document_id: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
        start_chunk_index: int = 0,
    ) -> List[ChunkData]:
        """Generate deterministic, sentence-snapped chunks for a single page.
        
        Guarantees:
        - Chunks respect sentence boundaries.
        - Chunks never exceed self.max_size.
        - Exact start and end character offsets within page_text are preserved.
        - Deterministic chunk ordering.
        - Empty or whitespace-only text produces an empty list.
        """
        if not page_text or not page_text.strip():
            return []

        sentences = self.split_into_sentences(page_text)
        if not sentences:
            return []

        chunks: List[ChunkData] = []
        current_chunk_sentences: List[TextSentence] = []
        current_chunk_len = 0
        running_index = start_chunk_index
        idx = 0

        while idx < len(sentences):
            sent = sentences[idx]
            sent_len = len(sent.text)

            # Check if adding this sentence stays within target_size or max_size
            if current_chunk_sentences and (current_chunk_len + sent_len > self.max_size or (current_chunk_len >= self.target_size and current_chunk_len + sent_len > self.target_size)):
                # Emit current chunk
                raw_start = current_chunk_sentences[0].start
                raw_end = current_chunk_sentences[-1].end
                raw_slice = page_text[raw_start:raw_end]
                leading_ws = len(raw_slice) - len(raw_slice.lstrip())
                trailing_ws = len(raw_slice) - len(raw_slice.rstrip())

                if len(raw_slice) > leading_ws + trailing_ws:
                    char_start = raw_start + leading_ws
                    char_end = raw_end - trailing_ws
                    content = raw_slice[leading_ws : len(raw_slice) - trailing_ws]
                    chunks.append(
                        ChunkData(
                            chunk_index=running_index,
                            content=content,
                            char_start=char_start,
                            char_end=char_end,
                            page_number=page_number,
                            page_id=page_id,
                            document_id=document_id,
                            workspace_id=workspace_id,
                        )
                    )
                    running_index += 1

                # Calculate overlap: take tail sentences from current chunk up to overlap_size
                overlap_sentences: List[TextSentence] = []
                overlap_accum = 0
                for prev_sent in reversed(current_chunk_sentences):
                    if overlap_accum + len(prev_sent.text) <= self.overlap_size:
                        overlap_sentences.insert(0, prev_sent)
                        overlap_accum += len(prev_sent.text)
                    else:
                        break

                current_chunk_sentences = list(overlap_sentences)
                current_chunk_len = sum(len(s.text) for s in current_chunk_sentences)

            current_chunk_sentences.append(sent)
            current_chunk_len += sent_len
            idx += 1

        # Emit any remaining chunk
        if current_chunk_sentences:
            raw_start = current_chunk_sentences[0].start
            raw_end = current_chunk_sentences[-1].end
            raw_slice = page_text[raw_start:raw_end]
            leading_ws = len(raw_slice) - len(raw_slice.lstrip())
            trailing_ws = len(raw_slice) - len(raw_slice.rstrip())

            if len(raw_slice) > leading_ws + trailing_ws:
                char_start = raw_start + leading_ws
                char_end = raw_end - trailing_ws
                content = raw_slice[leading_ws : len(raw_slice) - trailing_ws]
                chunks.append(
                    ChunkData(
                        chunk_index=running_index,
                        content=content,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_number,
                        page_id=page_id,
                        document_id=document_id,
                        workspace_id=workspace_id,
                    )
                )

        return chunks
