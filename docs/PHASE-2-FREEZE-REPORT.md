# ProofLens AI — Phase 2 Freeze & Verification Record

**Document Identifier:** PLAI-PHASE2-FREEZE  
**Date:** 2026-10-14  
**Status:** **PHASE 2 COMPLETE & VERIFIED — READY FOR FREEZE**  
**Git Branch:** `main`  
**Base Commit Hash:** `3cbc9668a92f5a66a50b612e6bda4e916f387aa3`  
**Proposed Freeze Checkpoint Tag:** `phase-2-freeze`  

---

## 1. Executive Summary

Phase 2 implements a production-grade, workspace-isolated document ingestion and processing pipeline for ProofLens AI. Building directly on the verified Phase 1 foundation (FastAPI, Supabase PostgreSQL with pgvector, Supabase Storage, and capability-token authentication), Phase 2 delivers:

1. **Secure Document Upload & Deep Validation:** Streamed 64 KB reading with 50 MB hard cap, `%PDF-` signature verification, PyMuPDF in-memory parseability/encryption checks, scanned image-only rejection (`SCANNED_PDF_UNSUPPORTED`), workspace-scoped duplicate SHA-256 detection, and compensating storage cleanup on database insertion failure.
2. **Deterministic Page-by-Page PDF Text Extraction:** Built with PyMuPDF (`fitz`), preserving 1-based physical page indices and page sequence order, normalizing line breaks and redundant whitespace, and detecting empty pages.
3. **Sentence-Aware Text Chunking with Exact Provenance:** Sentence boundary snapping (`.!?`), configurable target (1000) and max (1500) characters, sentence-boundary overlap (200), word-boundary subdivision for long sentences, and character-accurate provenance offsets (`char_start`, `char_end`) where `page_text[char_start:char_end] == chunk.content`.
4. **Document Library & Status Inspection APIs:** RESTful endpoints for uploading (`POST /api/v1/documents`), listing with pagination and dynamic counts (`GET /api/v1/documents`), and detail/status polling (`GET /api/v1/documents/{id}`).
5. **Durable Cascading Deletion:** Two-phase deletion (`DELETING` state marker -> storage asset delete -> database cascade), idempotent 404 handling, and historical conversation citation marking (`document_deleted: true`).
6. **Interrupted-Job Recovery & Bounded Retries:** Stale-job sweeper with 15-minute lease grace period (`INGESTION_LEASE_TIMEOUT_SECONDS = 900`), scalar-attribute loading avoiding async greenlet errors, bounded retries (max 3), and strict workspace-scoped tenant isolation.

All 66 automated tests pass with 100% success rate. The phase is verified and prepared for freeze.

---

## 2. Automated Test Suite Execution & Results

- **Test Execution Command:**
  ```powershell
  $env:PYTHONPATH="e:\ProofLense"; ..\.venv\Scripts\pytest.exe -v
  ```
- **Execution Runtime:** 154.02s
- **Total Tests:** 66
- **Passed:** 66 (100%)
- **Failed:** 0
- **Skipped:** 0

### Test Breakdown by Subsystem:

| Subsystem / Test File | Test Count | Result | Scope Verified |
| :--- | :---: | :---: | :--- |
| `backend/tests/test_database_config.py` | 10 | **PASSED** | Dynamic URL normalization (asyncpg/psycopg), password character encoding (`@`, `#`), secret redaction in URLs and error messages, SSL modes, Supabase host detection, fail-closed validation on missing `DATABASE_URL` in non-test environments, and isolated test defaults. *(Phase 1 Baseline)* |
| `backend/tests/test_health.py` | 1 | **PASSED** | Health check probe returning HTTP 200, database readiness (`supabase`), storage readiness (`supabase`), and canonical 5-tuple embedding configuration matching settings. *(Phase 1 Baseline)* |
| `backend/tests/test_storage.py` | 7 | **PASSED** | Local filesystem atomic save/retrieve/delete, path traversal blocking, fail-closed validation when `SUPABASE_SERVICE_ROLE_KEY` is omitted in production, explicit local storage mode permission, path sanitization in `SupabaseStorageService`, and live Supabase Cloud Storage CRUD + signed URLs. *(Phase 1 Baseline)* |
| `backend/tests/test_workspaces.py` | 7 | **PASSED** | Workspace creation returning raw capability token, SHA-256 token hash storage, authenticated access with Bearer token, rejection of missing/invalid tokens (HTTP 401), non-existent workspace (HTTP 404), token rotation, and strict tenant isolation. *(Phase 1 Baseline)* |
| `backend/tests/test_embedding_provider.py` | 4 | **PASSED** | Fallback embedding provider interface, embedding vector generation, fail-closed dimension validation (768-dim required), and credential security when `GEMINI_API_KEY` is missing. *(Phase 1 Baseline)* |
| `backend/tests/test_pdf_extractor.py` | 9 | **PASSED** | Valid multi-page PDF extraction, 1-based page numbering preservation, blank page detection, scanned image-only PDF rejection, empty file rejection, invalid magic bytes, corrupted PDF structure rejection, password-protected PDF handling, extraction determinism, and line normalization. *(Phase 2)* |
| `backend/tests/test_text_chunker.py` | 9 | **PASSED** | Sentence boundary preservation, maximum chunk size enforcement, long sentence subdivision, empty/whitespace page handling, provenance metadata (`page_id`, `document_id`, `workspace_id`, `chunk_index`), overlap behavior, chunking determinism, `overlap_size=0` non-default handling, and exact character slice equality (`text[start:end] == content`). *(Phase 2)* |
| `backend/tests/test_document_ingestion.py` | 10 | **PASSED** | Valid PDF upload (sync and async), invalid extension rejection (400), invalid MIME rejection (400), empty file rejection (400), corrupted PDF rejection (400), scanned PDF without OCR rejection (422), duplicate SHA-256 conflict in same workspace (409), duplicate hash permitted in different workspaces, list/get tenant isolation (404), and compensating storage cleanup on database failure. *(Phase 2)* |
| `backend/tests/test_document_deletion.py` | 4 | **PASSED** | Durable deletion cascading to storage and DB records, repeated idempotent deletion, cross-workspace deletion prevention (404 non-disclosing), and historical conversation citation deletion marking (`document_deleted: true`). *(Phase 2)* |
| `backend/tests/test_document_recovery.py` | 5 | **PASSED** | Manual retry of failed documents (`POST /documents/{id}/retry`), rejection of retry on ready documents (400), stale-job recovery sweeping crashed jobs, bounded retry exhaustion transitioning to `FAILED`, and strict workspace isolation of recovery endpoint (`test_recovery_endpoint_workspace_isolation`). *(Phase 2)* |

---

## 3. Defects Identified and Corrected Prior to Freeze

1. **`TextChunkerService` Overlap-Size Falsy Default Bug:**
   - *Issue:* `overlap_size or settings.CHUNK_OVERLAP_CHARS` reset `overlap_size=0` to `200`.
   - *Fix:* Replaced with `overlap_size if overlap_size is not None else settings.CHUNK_OVERLAP_CHARS`.
   - *Verified By:* `test_chunker_overlap_zero_permitted`.
2. **Character Offset Boundary Drift:**
   - *Issue:* Sentence boundaries included trailing whitespace, causing `len(content) != char_end - char_start`.
   - *Fix:* Trimmed `char_start` and `char_end` to strictly bound the non-whitespace slice.
   - *Verified By:* `test_chunker_exact_char_slice_matches_content`.
3. **Recovery Endpoint Tenant Isolation Gap:**
   - *Issue:* `POST /api/v1/documents/recovery/stale-jobs` was not passing `workspace_id` to `RecoveryService.recover_stale_documents`, allowing one tenant to sweep other tenants' documents.
   - *Fix:* Added `workspace_id` parameter to `recover_stale_documents` and enforced caller workspace filtering.
   - *Verified By:* `test_recovery_endpoint_workspace_isolation`.
4. **Historical Citation Deletion Marking:**
   - *Issue:* `delete_document` purged document rows without flagging references in `messages.citations`.
   - *Fix:* Added historical message citation inspection and flagged referencing citations with `document_deleted: true`.
   - *Verified By:* `test_document_deletion_marks_historical_citations`.

---

## 4. Database Schema & Migration Status

- **Alembic Revision:** `001_initial_schema` (active and verified on live Supabase PostgreSQL).
- **Production Migrations:** **Zero unapproved migrations were executed.**
- **Table Structure:**
  - `workspaces`: 7 columns.
  - `documents`: 17 columns (`id`, `workspace_id`, `filename`, `file_hash`, `mime_type`, `storage_path`, `status`, `error_message`, `deletion_attempts`, `deletion_error`, `processing_heartbeat`, `worker_id`, `is_corpus_excluded`, `corpus_exclusion_reason`, `corpus_excluded_at`, `created_at`, `updated_at`).
  - `document_pages`: 6 columns (`id`, `document_id`, `workspace_id`, `page_number`, `text_content`, `created_at`).
  - `document_chunks`: 15 columns with `Vector(768)` and HNSW index.
- **Dynamic Aggregation (TD-07):** Page and chunk counts are calculated dynamically via correlated subqueries (`func.count(DocumentPage.id)`, `func.count(DocumentChunk.id)`) on read operations, avoiding unapproved schema mutations.

---

## 5. Storage Engine Status

- **Active Storage:** Supabase Storage private bucket `prooflens-documents`.
- **Path Hierarchy:** `workspaces/{workspace_id}/raw/{document_id}.pdf`.
- **Isolation:** Workspace IDs are validated server-side. Path traversal (`../`) is strictly blocked.
- **Local Fallback:** `LocalStorageService` is retained for air-gapped unit testing, but blocked in non-test environments when `STORAGE_BACKEND="supabase"`.

---

## 6. Strict Phase 3 Boundary Compliance

Phase 2 strictly adheres to architectural boundaries:
- ❌ **Zero Gemini embedding generation calls** (`google-genai` was not invoked).
- ❌ **Zero fake or zero-filled vectors stored** (`embedding` column strictly remains `NULL`).
- ❌ **Zero vector similarity search or retrieval endpoints**.
- ❌ **Zero RAG answer generation, citation validation, or claim ledger logic**.
- ❌ **Zero external queue dependencies** (No Redis, Celery, or Kafka).

---

## 7. Accepted Technical Debt & Handover Notes

1. **TD-01 (Single-Node Background Task Execution):** Ingestion runs via FastAPI `BackgroundTasks`. Mitigated by database lease heartbeats and `RecoveryService` detecting and recovering jobs stalled beyond 15 minutes.
2. **TD-03 (Absence of Queue Backpressure on Uploads):** Capped at 50 MB with immediate pre-validation of PDF signatures before queueing. Application-level semaphore or queue backpressure can be added post-MVP.
3. **TD-07 (Dynamic Aggregation for Document Counts):** `page_count` and `chunk_count` are counted dynamically via correlated subqueries (<2ms per request). Avoids unapproved production table mutations.
4. **Dual Usage of `deletion_attempts`:** `RecoveryService` tracks recovery retry counts using `deletion_attempts` to bound retries without altering production schemas. Adding a dedicated `processing_attempts` column via an additive migration can be considered in a future phase.
5. **Complex Multi-Column PDF Reading-Order Limitations:** PyMuPDF extracts text in physical stream order; complex multi-column layouts with floating tables/figures may interleave text in edge cases. Advanced layout-aware extraction is deferred to a future phase.

---

## 8. Handover Readiness for Phase 3

The codebase is fully prepared for **Phase 3 (Embeddings and Vector Retrieval)**:
- Deterministic text chunks are stored in `document_chunks` with `char_start`, `char_end`, and `task_type="RETRIEVAL_DOCUMENT"`.
- `embedding` column is ready to receive 768-dimensional vectors from `gemini-embedding-2`.
- `001_initial_schema` already has the `ix_document_chunks_embedding_hnsw` index configured.
