"""Cloudinary uploader — reuses creds from First XI Fitness via .env."""
from __future__ import annotations

import os
from typing import Optional

import cloudinary
import cloudinary.uploader


_configured = False


def configure(
    cloud_name: Optional[str] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> None:
    global _configured
    cloudinary.config(
        cloud_name=cloud_name or os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=api_key or os.getenv("CLOUDINARY_API_KEY"),
        api_secret=api_secret or os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )
    _configured = True


def upload(file_path: str, public_id: Optional[str] = None, folder: Optional[str] = None) -> str:
    """Upload one image and return the secure HTTPS URL."""
    if not _configured:
        configure()
    folder = folder or os.getenv("CLOUDINARY_UPLOAD_FOLDER", "pokemon-bulk")
    result = cloudinary.uploader.upload(
        file_path,
        folder=folder,
        public_id=public_id,
        overwrite=True,
        resource_type="image",
    )
    return result["secure_url"]
