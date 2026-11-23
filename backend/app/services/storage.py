"""Proof photo storage: S3-compatible bucket with signed URLs, or local disk in dev."""

import io
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import settings

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


class UploadError(ValueError):
    pass


def strip_exif(data: bytes) -> tuple[bytes, str]:
    """Re-encode the image without metadata (removes EXIF GPS). Returns (bytes, extension)."""
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise UploadError(f"File too large (max {settings.max_upload_mb} MB)")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadError("Not a valid image") from exc
    fmt = img.format or ""
    if fmt not in ALLOWED_FORMATS:
        raise UploadError("Only JPEG, PNG or WEBP images are allowed")
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    out = io.BytesIO()
    clean.save(out, format=fmt)
    return out.getvalue(), ALLOWED_FORMATS[fmt]


def _s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
    )


def save_proof(user_id: int, data: bytes) -> str:
    """Store a cleaned image; returns the storage key."""
    clean, ext = strip_exif(data)
    key = f"proofs/{user_id}/{uuid.uuid4().hex}.{ext}"
    if settings.s3_bucket:
        _s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=clean, ContentType=f"image/{ext}")
    else:
        path = Path(settings.local_upload_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(clean)
    return key


def signed_url(key: str | None, expires: int = 3600) -> str | None:
    """Proof photos are private: hand out short-lived signed URLs only."""
    if not key:
        return None
    if settings.s3_bucket:
        return _s3().generate_presigned_url(
            "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=expires
        )
    # Local dev: sign the key with a short-lived JWT so <img> tags work without auth headers
    import jwt
    from datetime import datetime, timedelta, timezone

    sig = jwt.encode(
        {"key": key, "exp": datetime.now(timezone.utc) + timedelta(seconds=expires)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return f"/api/v1/uploads/{key}?sig={sig}"


def verify_local_signature(key: str, sig: str) -> bool:
    import jwt

    try:
        return jwt.decode(sig, settings.jwt_secret, algorithms=[settings.jwt_algorithm]).get("key") == key
    except jwt.PyJWTError:
        return False


def delete_user_files(user_id: int) -> None:
    prefix = f"proofs/{user_id}/"
    if settings.s3_bucket:
        s3 = _s3()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                s3.delete_object(Bucket=settings.s3_bucket, Key=obj["Key"])
    else:
        folder = Path(settings.local_upload_dir) / prefix
        if folder.exists():
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
