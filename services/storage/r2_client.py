"""Cloudflare R2 storage client for media files."""

import hashlib
import logging
from datetime import datetime
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


class R2StorageClient:
    """Client for uploading media files to Cloudflare R2 storage."""

    def __init__(self):
        """Initialize the R2 client with credentials from settings."""
        self.access_key_id = settings.R2_ACCESS_KEY_ID
        self.secret_access_key = settings.R2_SECRET_ACCESS_KEY
        self.endpoint_url = settings.R2_ENDPOINT_URL
        self.bucket_name = settings.R2_BUCKET_NAME
        self.public_url = settings.R2_PUBLIC_URL

        if not all([self.access_key_id, self.secret_access_key, self.endpoint_url]):
            logger.warning("R2 credentials not fully configured")
            self.client = None
        else:
            self.client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                config=Config(signature_version="s3v4"),
            )

    def upload_media(
        self,
        media_data: bytes,
        media_id: str,
        mime_type: str,
        phone_number: str,
    ) -> Optional[str]:
        """
        Upload media file to R2 storage.

        Args:
            media_data: The raw media file bytes
            media_id: The Meta media ID (used for idempotency)
            mime_type: The MIME type of the media
            phone_number: The phone number of the sender (for organizing files)

        Returns:
            The public URL of the uploaded file, or None if upload failed
        """
        if not self.client:
            logger.error("R2 client not initialized - credentials missing")
            return None

        try:
            # Generate file path: YYYY/MM/DD/phone_number/media_id.ext
            now = datetime.utcnow()
            extension = self._get_extension_from_mime(mime_type)

            # Sanitize phone number for file path (remove + and spaces)
            safe_phone = phone_number.replace("+", "").replace(" ", "")

            file_key = f"{now.year:04d}/{now.month:02d}/{now.day:02d}/{safe_phone}/{media_id}.{extension}"

            # Calculate MD5 hash for integrity check
            md5_hash = hashlib.md5(media_data).hexdigest()

            # Upload to R2
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=file_key,
                Body=media_data,
                ContentType=mime_type,
                Metadata={
                    "media_id": media_id,
                    "phone_number": phone_number,
                    "md5": md5_hash,
                },
            )

            # Construct public URL
            if self.public_url:
                public_url = f"{self.public_url.rstrip('/')}/{file_key}"
            else:
                # Fallback to R2 endpoint URL
                public_url = f"{self.endpoint_url.rstrip('/')}/{self.bucket_name}/{file_key}"

            logger.info(f"Uploaded media {media_id} to R2: {file_key}")
            return public_url

        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to upload media {media_id} to R2: {e}", exc_info=True)
            return None

    def _get_extension_from_mime(self, mime_type: str) -> str:
        """Map MIME type to file extension."""
        mime_to_ext = {
            # Audio
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/aac": "aac",
            "audio/amr": "amr",
            "audio/opus": "opus",
            # Images
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
            # Video
            "video/mp4": "mp4",
            "video/3gpp": "3gp",
            "video/quicktime": "mov",
            # Documents
            "application/pdf": "pdf",
            "application/msword": "doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.ms-excel": "xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        }
        return mime_to_ext.get(mime_type, "bin")
