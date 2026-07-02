"""Stocare obiecte: Cloudflare R2 (productie) sau disc local (development).

DB tine doar cheile obiectelor. Browserul incarca pozele prin URL-uri
presemnate direct din R2 — serverul Flask nu serveste niciodata bytes grei.
"""
import os
import uuid


class R2Storage:
    def __init__(self, app):
        import boto3
        from botocore.config import Config as BotoConfig

        c = app.config
        self.bucket = c["R2_BUCKET"]
        self.expiry = c["R2_URL_EXPIRY"]
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{c['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=c["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=c["R2_SECRET_ACCESS_KEY"],
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )

    def put(self, key, data, content_type):
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def url(self, key):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.expiry,
        )

    def put_url(self, key, content_type):
        """URL presemnat de PUT — browserul urca direct in R2.

        Content-Type intra in semnatura: clientul trebuie sa trimita
        exact acelasi header, altfel R2 respinge cererea.
        """
        return self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self.expiry,
        )

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


class LocalStorage:
    """Fallback pentru development, fara credentiale R2."""

    def __init__(self, app):
        self.root = os.path.join(app.instance_path, "uploads")
        os.makedirs(self.root, exist_ok=True)

    def put(self, key, data, content_type):
        path = os.path.join(self.root, key.replace("/", "__"))
        with open(path, "wb") as f:
            f.write(data)

    def url(self, key):
        from flask import url_for
        return url_for("trails.local_media", key=key)

    def put_url(self, key, content_type):
        from flask import url_for
        return url_for("trails.local_media_put", key=key)

    def delete(self, key):
        path = os.path.join(self.root, key.replace("/", "__"))
        if os.path.exists(path):
            os.remove(path)

    def path_for(self, key):
        return os.path.join(self.root, key.replace("/", "__"))


def build_storage(app):
    c = app.config
    if c["R2_ACCOUNT_ID"] and c["R2_ACCESS_KEY_ID"] and c["R2_SECRET_ACCESS_KEY"]:
        app.logger.info("Storage: Cloudflare R2 (%s)", c["R2_BUCKET"])
        return R2Storage(app)
    app.logger.warning("Storage: disc local — doar pentru development")
    return LocalStorage(app)


def new_key(trail_id, kind, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"trails/{trail_id}/{kind}/{uuid.uuid4().hex}{ext}"
