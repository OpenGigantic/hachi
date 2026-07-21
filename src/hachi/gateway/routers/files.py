"""``/files/*`` -- file / attachment platform routes (Initiative E, T-47).

POST   /files            — multipart upload; returns FileMeta JSON
GET    /files            — list all file metadata records
GET    /files/{id}       — get FileMeta for a single file
GET    /files/{id}/content — stream raw bytes (FileResponse)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from hachi.gateway.deps import require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES

router = APIRouter(
    prefix="/files",
    tags=["files"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


# Max upload size guard — read from [files] settings, fallback 50 MB.
def _files_max_bytes() -> int:
    try:
        from hachi.core.config_loader import load_effective_config

        return int(load_effective_config().files.max_size_bytes)
    except Exception:
        return 50 * 1024 * 1024


def _files_allowed_types() -> set[str]:
    try:
        from hachi.core.config_loader import load_effective_config

        return set(load_effective_config().files.allowed_types)
    except Exception:
        return set()


def _get_file_pipeline() -> Any:
    """Build a FilePipeline from paths (one per request; lightweight)."""
    from hachi.core.files.store import (
        FileMetaStore,
        FilePipeline,
        LocalStorageProvider,
        NoOpScanner,
    )
    from hachi.core.paths import get_hachi_paths

    paths = get_hachi_paths()
    meta_store = FileMetaStore(paths.files_db)
    storage = LocalStorageProvider(paths.blobs_dir, _files_max_bytes())
    return FilePipeline(
        meta_store=meta_store,
        storage=storage,
        scanner=NoOpScanner(),
        allowed_types=_files_allowed_types(),
        max_size_bytes=_files_max_bytes(),
    )


def _get_file_meta_store() -> Any:
    from hachi.core.files.store import FileMetaStore
    from hachi.core.paths import get_hachi_paths

    return FileMetaStore(get_hachi_paths().files_db)


def _get_file_storage() -> Any:
    from hachi.core.files.store import LocalStorageProvider
    from hachi.core.paths import get_hachi_paths

    return LocalStorageProvider(get_hachi_paths().blobs_dir)


def _reject_path_traversal(file_id: str) -> None:
    """Raise 400 if *file_id* contains path traversal sequences."""
    if "/" in file_id or "\\" in file_id or ".." in file_id or not file_id:
        raise HTTPException(status_code=400, detail="Invalid file_id")


def _trigger_extraction_inline(file_id: str, name: str, media_type: str) -> None:
    """Best-effort inline extraction when the durable path is unavailable."""
    try:
        from hachi.core.runtime.job_handlers import handle_auto_extract

        handle_auto_extract(
            event_name="file.uploaded",
            event_payload={
                "file_id": file_id,
                "name": name,
                "media_type": media_type,
            },
        )
    except Exception:
        pass  # Non-fatal; file stays at 'stored' until a job worker picks it up.


@router.post("", summary="Upload a file attachment")
async def upload_file(
    file: UploadFile = File(...),  # noqa: B008
    media_type_override: str | None = Query(default=None, alias="media_type"),
) -> Any:
    """Ingest a multipart file upload into the managed attachment store.

    Validates type and size BEFORE storage.  Returns :class:`FileMeta` JSON.
    A ``file.uploaded`` event is emitted after storage so the reactive
    extract-and-index pipeline fires asynchronously via the JobRegistry.
    """
    if file.filename is None:
        raise HTTPException(status_code=400, detail="Filename is required")

    # Infer MIME type: explicit override → content_type header → extension.
    effective_type = media_type_override or file.content_type or "application/octet-stream"

    # Size pre-check from Content-Length header (not authoritative but fast).
    max_bytes = _files_max_bytes()

    import io as _io

    raw = await file.read()
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size ({max_bytes} bytes)",
        )

    try:
        pipeline = _get_file_pipeline()
        meta = pipeline.ingest(
            name=file.filename,
            media_type=effective_type,
            data=_io.BytesIO(raw),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Trigger extraction synchronously as a best-effort inline step when
    # the durable-handler path is not configured (e.g. during tests).
    # In production (durable_handlers=true) the EventBus subscription does this.
    _trigger_extraction_inline(meta.file_id, meta.name, meta.media_type)

    return meta.model_dump()


@router.get("", summary="List all file attachments")
async def list_files() -> Any:
    """Return all stored file metadata records, newest first."""
    store = _get_file_meta_store()
    return [m.model_dump() for m in store.list_all()]


@router.get("/{file_id}", summary="Get file metadata")
async def get_file_meta(file_id: str) -> Any:
    """Return the :class:`FileMeta` record for a single *file_id*."""
    _reject_path_traversal(file_id)
    store = _get_file_meta_store()
    meta = store.get(file_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"File {file_id!r} not found")
    return meta.model_dump()


@router.get("/{file_id}/content", summary="Stream raw file content")
async def get_file_content(file_id: str) -> Any:
    """Stream the raw bytes of the stored blob for *file_id*.

    Quarantined files are NOT served (404).
    """
    _reject_path_traversal(file_id)
    store = _get_file_meta_store()
    meta = store.get(file_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"File {file_id!r} not found")
    if meta.status == "quarantined":
        raise HTTPException(status_code=404, detail="File is not available")

    from hachi.core.paths import get_hachi_paths

    blob_path = get_hachi_paths().blobs_dir / file_id
    if not blob_path.exists():
        raise HTTPException(status_code=404, detail="Blob not found on disk")

    return FileResponse(
        path=str(blob_path),
        media_type=meta.media_type,
        filename=meta.name,
    )
