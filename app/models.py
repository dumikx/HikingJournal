import json
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Trail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    massif = db.Column(db.String(100))          # ex: Retezat
    peak = db.Column(db.String(200))            # ex: Peleaga 2509m
    marcaj = db.Column(db.String(30))           # banda_rosie, cruce_albastra...

    # statistici calculate din GPX
    distance_km = db.Column(db.Float)
    ascent_m = db.Column(db.Integer)
    descent_m = db.Column(db.Integer)
    elev_min_m = db.Column(db.Integer)
    elev_max_m = db.Column(db.Integer)
    duration_min = db.Column(db.Integer)        # cap-coada
    moving_min = db.Column(db.Integer)          # doar in miscare

    start_lat = db.Column(db.Float)
    start_lng = db.Column(db.Float)
    track_json = db.Column(db.Text)             # [[lat,lng],...] simplificat
    profile_json = db.Column(db.Text)           # {"d":[km...], "e":[m...]}
    gpx_key = db.Column(db.String(300))         # GPX-ul original in storage

    notes = db.Column(db.Text)

    # share-later, fara migrare de schema (privat implicit)
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    public_slug = db.Column(db.String(64), unique=True)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    photos = db.relationship(
        "Photo", backref="trail", cascade="all, delete-orphan", lazy=True,
        order_by="Photo.taken_at",
    )

    @property
    def track(self):
        return json.loads(self.track_json) if self.track_json else []

    @property
    def profile(self):
        return json.loads(self.profile_json) if self.profile_json else {"d": [], "e": []}

    def spark_points(self, w=160, h=36, pad=2):
        """Puncte SVG polyline pentru mini-profilul de elevatie de pe card."""
        prof = self.profile
        es = prof.get("e") or []
        if len(es) < 2:
            return ""
        lo, hi = min(es), max(es)
        span = (hi - lo) or 1
        n = len(es)
        pts = []
        for i, e in enumerate(es):
            x = pad + i * (w - 2 * pad) / (n - 1)
            y = h - pad - (e - lo) * (h - 2 * pad) / span
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)


class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trail_id = db.Column(db.Integer, db.ForeignKey("trail.id"), nullable=False)
    key = db.Column(db.String(300), nullable=False)   # cheia obiectului in storage
    filename = db.Column(db.String(200))
    caption = db.Column(db.String(300))
    taken_at = db.Column(db.DateTime)                 # din EXIF, ora locala
    lat = db.Column(db.Float)                         # pozitia pe traseu
    lng = db.Column(db.Float)
    on_track = db.Column(db.Boolean, default=False)   # a putut fi plasata pe traseu?
