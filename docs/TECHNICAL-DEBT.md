# ProofLens AI — Technical Debt & Architectural Risk Register

This document tracks accepted, non-blocking technical debt items, architectural deferrals, and operational trade-offs for ProofLens AI.

> **Governance Invariant:** In accordance with ProofLens AI architectural guidelines, issues impacting security, workspace isolation, database integrity, migration correctness, or retrieval corpus compatibility **MUST NEVER** be classified as deferrable technical debt. Such items are blocking defects that must be resolved prior to release.

---

## Technical Debt Register

| ID | Title / Description | Priority | Impact | Affected Phase | Mitigation Strategy | Resolution Target |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TD-01** | **Single-Node Background Task Execution vs. Distributed Broker**<br>Ingestion tasks run via FastAPI `BackgroundTasks` within the application process rather than a distributed Celery/Temporal broker. | Medium | If the single backend node restarts, active in-flight jobs terminate and require lease recovery. Throughput is constrained by single-node CPU/RAM. | Phase 2, Phase 3 | Database lease mechanism (`worker_id`, `processing_heartbeat` with 15-minute stale timeout) and startup sweeper automatically detect and recover crashed jobs. | Phase 6 (Reliability & Scaling) |
| **TD-02** | **Storage Provider Limited to Local Filesystem**<br>`StorageService` interface is fully defined, but only `LocalStorageService` is implemented for MVP. S3/GCS blob storage backends are deferred. | Low | Workspaces and documents are bound to the local storage mount of the host system. Horizontal scaling across multiple stateless nodes requires shared NFS/EFS. | Phase 1, Phase 2 | Clean abstract `StorageService` interface isolates all file operations; domain services remain storage-agnostic. Path traversal checks and SHA-256 integrity checks are strictly enforced. | Post-MVP / Production Cloud Deployment |
| **TD-03** | **Absence of Rate Limiting / Queue Backpressure on Uploads**<br>Multiple concurrent large PDF uploads could compete for disk I/O and PyMuPDF CPU resources without an application-level semaphore queue. | Medium | Temporary resource saturation under high burst concurrency. | Phase 2 | Request payload capped at 50 MB; FastAPI streaming to disk; PyMuPDF page iteration yields control with stage-boundary heartbeats; client-facing 429 backpressure can be configured at reverse proxy. | Phase 6 (Performance Hardening) |
| **TD-04** | **Static Vector Column Dimensions in Schema Migration**<br>Alembic migration statically defines `vector(768)` matching Google Gemini Embedding 2 (`gemini-embedding-2`). Switching to local FastEmbed (384 dims) requires a schema migration or separate test database table. | Low | Switching active embedding providers across different dimension sizes cannot happen dynamically at runtime without ALTER TABLE / re-embedding migration. | Phase 1, Phase 3 | `CorpusCompatibilityGate` strictly enforces 5-tuple fingerprint and rejects vector mismatches (`HTTP 409 Conflict`); FastEmbed wrapper performs explicit dimension validation before vector persistence. | Phase 3 (Live Probe Verification) |
| **TD-05** | **Workspace Capability Token Authentication vs. Multi-User RBAC**<br>Authentication is workspace-centric via high-entropy capability bearer tokens (`secrets.token_urlsafe(32)`) rather than fine-grained user identity (OAuth2/OIDC) and role-based permissions (Admin, Editor, Viewer). | Low | Anyone holding the capability token has full operational access to that specific workspace. | Phase 1 | High-entropy 256-bit entropy tokens, constant-time SHA-256 hashing in database, token rotation endpoint (`POST .../token/rotate`), and strict tenant-isolated queries (`WHERE workspace_id = :id`). | Enterprise v2 Roadmap |
| **TD-06** | **Supabase Cloud Storage Integration** *(RESOLVED)*<br>Supabase Storage private bucket (`prooflens-documents`) with `SupabaseStorageService`, signed URLs, and atomic SHA-256 uploads is fully implemented and active. | Low | Resolved. Both PostgreSQL database and PDF document storage are cloud-native in Supabase. | Phase 1 | `SupabaseStorageService` implements `StorageService` interface with path sanitization, signed URLs, and automated regression tests. | **RESOLVED (Phase 1)** |


---

## Review and Clearance Process
1. Technical debt items are reviewed at the conclusion of each implementation phase.
2. An item can only be closed when production code and automated regression tests demonstrate its resolution without compromising existing invariants.
3. Any debt item that evolves into a data corruption, isolation leak, or compatibility failure must be immediately upgraded to a blocking critical issue.
