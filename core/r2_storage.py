from __future__ import annotations

"""
Backends R2 (Cloudflare) pour les médias de la page Feed.

On ne remplace PAS DEFAULT_FILE_STORAGE (cloudinary) pour ne pas casser les autres uploads.
On utilise un FileField dédié sur le modèle `Post`.
"""

from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class R2MediaStorage(S3Boto3Storage):
    """
    Stockage compatible S3 pour Cloudflare R2.
    Les variables sont lues depuis settings.py (env R2_*).
    """

    bucket_name = getattr(settings, "R2_BUCKET_NAME", None) or getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)
    location = "feed_media"

    access_key = getattr(settings, "R2_ACCESS_KEY_ID", None) or getattr(settings, "AWS_ACCESS_KEY_ID", None)
    secret_key = getattr(settings, "R2_SECRET_ACCESS_KEY", None) or getattr(settings, "AWS_SECRET_ACCESS_KEY", None)

    # Cloudflare R2 utilise une région "auto"
    region_name = getattr(settings, "R2_REGION_NAME", None) or "auto"
    endpoint_url = getattr(settings, "R2_ENDPOINT_URL", None) or getattr(settings, "AWS_S3_ENDPOINT_URL", None)

    file_overwrite = False

    # Feed: on veut des URLs rapides (si bucket public) ; sinon, le front peut utiliser signed URLs.
    querystring_auth = False

    def __init__(self, *args, **kwargs):
        """
        Permet de réutiliser le même backend R2 en changeant seulement la `location`.

        Exemple:
            R2MediaStorage(location="comment_media")
        """
        location_override = kwargs.pop("location_override", None)
        super().__init__(*args, **kwargs)
        if location_override:
            self.location = location_override

