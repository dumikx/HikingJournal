import os


def _database_url() -> str:
    """Railway livrează postgres:// — SQLAlchemy vrea postgresql://."""
    url = os.environ.get("DATABASE_URL", "sqlite:///trail_journal.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # upload-uri mari: GPX + poze in bulk

    # --- Cloudflare R2 (daca lipsesc, se foloseste stocare locala pe disc) ---
    R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
    R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
    R2_BUCKET = os.environ.get("R2_BUCKET", "trail-journal")
    R2_URL_EXPIRY = int(os.environ.get("R2_URL_EXPIRY", 3600))  # secunde

    # --- Parametri algoritm GPX (tunabili) ---
    GPX_HYSTERESIS_M = float(os.environ.get("GPX_HYSTERESIS_M", 3.0))
    GPX_SIMPLIFY_TOLERANCE_M = float(os.environ.get("GPX_SIMPLIFY_TOLERANCE_M", 5.0))
    GPX_PROFILE_POINTS = int(os.environ.get("GPX_PROFILE_POINTS", 200))
    GPX_MOVING_SPEED_KMH = float(os.environ.get("GPX_MOVING_SPEED_KMH", 0.7))

    # --- Potrivirea pozelor pe traseu ---
    PHOTO_MATCH_MAX_GAP_MIN = int(os.environ.get("PHOTO_MATCH_MAX_GAP_MIN", 30))
    # EXIF e in ora locala, GPX in UTC. Romania: +2 iarna / +3 vara.
    PHOTO_TZ_OFFSET_HOURS = float(os.environ.get("PHOTO_TZ_OFFSET_HOURS", 3))

    # --- User initial (creat de `flask init-user` la deploy) ---
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "dumi")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
