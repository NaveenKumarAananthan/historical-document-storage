"""Authenticated, create-only document storage for Historical Intelligence."""

import hashlib
import os
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool


def create_app():
    token = os.environ.get("STORAGE_TOKEN", "")
    directory = os.environ.get("STORAGE_DIRECTORY", "")
    max_bytes = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise RuntimeError("STORAGE_TOKEN must be an ASCII secret of at least 32 characters, without spaces")
    if not directory or not Path(directory).is_absolute():
        raise RuntimeError("STORAGE_DIRECTORY must be an explicit absolute directory")
    if not 1024 <= max_bytes <= 100 * 1024 * 1024:
        raise RuntimeError("MAX_UPLOAD_BYTES must be between 1024 and 104857600")
    root = Path(directory).resolve()

    def check_storage():
        if not root.is_dir():
            raise OSError("Configured storage directory does not exist")
        # Fail health checks when the mounted storage cannot accept writes.
        with tempfile.TemporaryFile(dir=root) as probe:
            probe.write(b"storage-health")
            probe.flush()
            os.fsync(probe.fileno())

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(check_storage)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(request: Request):
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied.encode("utf-8"), ("Bearer " + token).encode("ascii")):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    def object_path(key: str):
        if not key or len(key.encode("utf-8")) > 4096 or "\x00" in key:
            raise HTTPException(422, "Invalid storage key")
        # Hash the full opaque key: never interpret it as a filesystem path.
        return root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".blob")

    @app.get("/health")
    async def health():
        try:
            await run_in_threadpool(check_storage)
        except OSError:
            raise HTTPException(503, "Storage is unavailable") from None
        return {"status": "ok"}

    @app.put("/objects/{key:path}", dependencies=[Depends(authorize)])
    async def put_object(key: str, request: Request):
        destination = object_path(key)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                size = int(declared)
                if size < 0:
                    raise ValueError()
            except ValueError:
                raise HTTPException(400, "Invalid Content-Length") from None
            if size > max_bytes:
                raise HTTPException(413, "Source file exceeds the size limit")
        if destination.exists():
            raise HTTPException(409, "Object already exists; originals cannot be overwritten")
        temporary = None
        try:
            # Only publish complete uploads. A hard link gives atomic create-only
            # semantics even when two requests use the same key concurrently.
            temporary = await run_in_threadpool(tempfile.NamedTemporaryFile, dir=root, prefix=".upload-", delete=False)
            count = 0
            async for chunk in request.stream():
                count += len(chunk)
                if count > max_bytes:
                    raise HTTPException(413, "Source file exceeds the size limit")
                await run_in_threadpool(temporary.write, chunk)
            if declared is not None and count != size:
                raise HTTPException(400, "Incomplete upload")
            await run_in_threadpool(temporary.flush)
            await run_in_threadpool(os.fsync, temporary.fileno())
            await run_in_threadpool(temporary.close)
            try:
                await run_in_threadpool(os.link, temporary.name, destination)
            except FileExistsError:
                raise HTTPException(409, "Object already exists; originals cannot be overwritten") from None
        except OSError:
            raise HTTPException(503, "Storage is unavailable") from None
        finally:
            if temporary is not None:
                temporary.close()
                Path(temporary.name).unlink(missing_ok=True)
        return Response(status_code=201)

    @app.get("/objects/{key:path}", dependencies=[Depends(authorize)])
    def get_object(key: str):
        path = object_path(key)
        if not path.is_file():
            raise HTTPException(404, "Source file is unavailable")
        if path.stat().st_size > max_bytes:
            raise HTTPException(413, "Source file exceeds the size limit")
        return FileResponse(path, media_type="application/octet-stream", filename="source-document", headers={
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox",
        })

    @app.delete("/objects/{key:path}", dependencies=[Depends(authorize)])
    def delete_object(key: str):
        try:
            object_path(key).unlink(missing_ok=True)
        except OSError:
            raise HTTPException(503, "Storage is unavailable") from None
        return Response(status_code=204)

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), proxy_headers=False)
