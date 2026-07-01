import io
import json
from datetime import date, datetime

from flask import (abort, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import login_required

from ..extensions import db
from ..gpx_utils import match_photo_to_track, parse_gpx
from ..models import Photo, Trail
from ..storage import LocalStorage, new_key
from . import bp

MARCAJE = [
    ("banda_rosie", "Bandă roșie"),
    ("banda_albastra", "Bandă albastră"),
    ("banda_galbena", "Bandă galbenă"),
    ("cruce_rosie", "Cruce roșie"),
    ("cruce_albastra", "Cruce albastră"),
    ("cruce_galbena", "Cruce galbenă"),
    ("triunghi_rosu", "Triunghi roșu"),
    ("triunghi_albastru", "Triunghi albastru"),
    ("triunghi_galben", "Triunghi galben"),
    ("punct_rosu", "Punct roșu"),
    ("punct_albastru", "Punct albastru"),
    ("punct_galben", "Punct galben"),
    ("nemarcat", "Nemarcat / creastă"),
]


def _storage():
    return current_app.extensions["storage"]


def _exif_data(img_bytes):
    """(taken_at local, lat, lng) din EXIF; oricare poate lipsi."""
    taken_at = lat = lng = None
    try:
        from PIL import ExifTags, Image

        img = Image.open(io.BytesIO(img_bytes))
        exif = img.getexif()
        if exif:
            dt_raw = exif.get(306)  # DateTime
            ifd = exif.get_ifd(ExifTags.IFD.Exif)
            dt_raw = ifd.get(36867) or dt_raw  # DateTimeOriginal are prioritate
            if dt_raw:
                taken_at = datetime.strptime(str(dt_raw), "%Y:%m:%d %H:%M:%S")

            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
            if gps and 2 in gps and 4 in gps:
                def to_deg(vals, ref):
                    d = float(vals[0]) + float(vals[1]) / 60 + float(vals[2]) / 3600
                    return -d if ref in ("S", "W") else d

                lat = to_deg(gps[2], gps.get(1, "N"))
                lng = to_deg(gps[4], gps.get(3, "E"))
    except Exception:
        current_app.logger.exception("EXIF illizibil — continui fara metadate")
    return taken_at, lat, lng


def _attach_photos(trail, files, gpx_points):
    """Urca pozele in storage si le plaseaza pe traseu (EXIF GPS > timestamp)."""
    cfg = current_app.config
    added = 0
    for f in files:
        if not f or not f.filename:
            continue
        data = f.read()
        if not data:
            continue
        taken_at, lat, lng = _exif_data(data)
        on_track = False
        if lat is None and taken_at and gpx_points:
            pos = match_photo_to_track(taken_at, gpx_points, cfg)
            if pos:
                lat, lng = pos
                on_track = True
        elif lat is not None:
            on_track = True

        key = new_key(trail.id, "photos", f.filename)
        _storage().put(key, data, f.mimetype or "image/jpeg")
        db.session.add(Photo(
            trail_id=trail.id, key=key, filename=f.filename,
            taken_at=taken_at, lat=lat, lng=lng, on_track=on_track,
        ))
        added += 1
    return added


@bp.route("/")
@login_required
def index():
    q = Trail.query.order_by(Trail.date.desc())
    year = request.args.get("year", type=int)
    massif = request.args.get("massif", "").strip()
    if year:
        q = q.filter(db.extract("year", Trail.date) == year)
    if massif:
        q = q.filter(Trail.massif == massif)
    trails = q.all()

    all_trails = Trail.query.with_entities(Trail.date, Trail.massif).all()
    years = sorted({t.date.year for t in all_trails}, reverse=True)
    massifs = sorted({t.massif for t in all_trails if t.massif})

    map_data = [
        {
            "id": t.id, "title": t.title, "date": t.date.isoformat(),
            "lat": t.start_lat, "lng": t.start_lng, "track": t.track,
            "distance_km": t.distance_km, "ascent_m": t.ascent_m,
            "url": url_for("trails.detail", trail_id=t.id),
        }
        for t in trails if t.start_lat is not None
    ]

    totals = {
        "count": len(trails),
        "km": round(sum(t.distance_km or 0 for t in trails), 1),
        "ascent": sum(t.ascent_m or 0 for t in trails),
    }
    return render_template(
        "index.html", trails=trails, map_data=json.dumps(map_data),
        years=years, massifs=massifs, sel_year=year, sel_massif=massif,
        totals=totals,
    )


@bp.route("/trail/<int:trail_id>")
@login_required
def detail(trail_id):
    trail = db.session.get(Trail, trail_id) or abort(404)
    photos = [
        {
            "id": p.id, "url": _storage().url(p.key), "caption": p.caption or "",
            "lat": p.lat, "lng": p.lng, "on_track": p.on_track,
            "taken_at": p.taken_at.strftime("%H:%M") if p.taken_at else None,
        }
        for p in trail.photos
    ]
    return render_template(
        "detail.html", trail=trail, photos=photos,
        photos_json=json.dumps(photos), track_json=json.dumps(trail.track),
        profile_json=json.dumps(trail.profile), marcaje=dict(MARCAJE),
    )


@bp.route("/trail/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        gpx_file = request.files.get("gpx")
        if not gpx_file or not gpx_file.filename:
            flash("Lipsește fișierul GPX.")
            return render_template("new.html", marcaje=MARCAJE, today=date.today().isoformat())

        gpx_bytes = gpx_file.read()
        try:
            stats = parse_gpx(io.BytesIO(gpx_bytes), current_app.config)
        except Exception as e:
            flash(f"GPX invalid: {e}")
            return render_template("new.html", marcaje=MARCAJE, today=date.today().isoformat())

        trail = Trail(
            title=request.form.get("title", "").strip() or gpx_file.filename,
            date=date.fromisoformat(request.form.get("date") or date.today().isoformat()),
            massif=request.form.get("massif", "").strip() or None,
            peak=request.form.get("peak", "").strip() or None,
            marcaj=request.form.get("marcaj") or None,
            notes=request.form.get("notes", "").strip() or None,
            distance_km=stats["distance_km"], ascent_m=stats["ascent_m"],
            descent_m=stats["descent_m"], elev_min_m=stats["elev_min_m"],
            elev_max_m=stats["elev_max_m"], duration_min=stats["duration_min"],
            moving_min=stats["moving_min"], start_lat=stats["start_lat"],
            start_lng=stats["start_lng"], track_json=json.dumps(stats["track"]),
            profile_json=json.dumps(stats["profile"]),
        )
        db.session.add(trail)
        db.session.flush()  # avem nevoie de trail.id pentru cheile de storage

        gpx_key = new_key(trail.id, "gpx", gpx_file.filename)
        _storage().put(gpx_key, gpx_bytes, "application/gpx+xml")
        trail.gpx_key = gpx_key

        n = _attach_photos(trail, request.files.getlist("photos"), stats["_points"])
        db.session.commit()
        flash(f"Tură salvată — {n} poze adăugate." if n else "Tură salvată.")
        return redirect(url_for("trails.detail", trail_id=trail.id))

    return render_template("new.html", marcaje=MARCAJE, today=date.today().isoformat())


@bp.route("/trail/<int:trail_id>/edit", methods=["GET", "POST"])
@login_required
def edit(trail_id):
    trail = db.session.get(Trail, trail_id) or abort(404)
    if request.method == "POST":
        trail.title = request.form.get("title", "").strip() or trail.title
        trail.date = date.fromisoformat(request.form.get("date") or trail.date.isoformat())
        trail.massif = request.form.get("massif", "").strip() or None
        trail.peak = request.form.get("peak", "").strip() or None
        trail.marcaj = request.form.get("marcaj") or None
        trail.notes = request.form.get("notes", "").strip() or None

        files = request.files.getlist("photos")
        if any(f.filename for f in files):
            gpx_points = None
            if trail.gpx_key:
                try:
                    st = _storage()
                    if isinstance(st, LocalStorage):
                        with open(st.path_for(trail.gpx_key), "rb") as fh:
                            gpx_points = parse_gpx(fh, current_app.config)["_points"]
                    else:
                        obj = st.client.get_object(Bucket=st.bucket, Key=trail.gpx_key)
                        gpx_points = parse_gpx(obj["Body"], current_app.config)["_points"]
                except Exception:
                    current_app.logger.exception("Nu am putut reciti GPX-ul pentru plasarea pozelor")
            _attach_photos(trail, files, gpx_points)

        db.session.commit()
        flash("Tură actualizată.")
        return redirect(url_for("trails.detail", trail_id=trail.id))

    return render_template("edit.html", trail=trail, marcaje=MARCAJE)


@bp.route("/trail/<int:trail_id>/delete", methods=["POST"])
@login_required
def delete(trail_id):
    trail = db.session.get(Trail, trail_id) or abort(404)
    st = _storage()
    for p in trail.photos:
        try:
            st.delete(p.key)
        except Exception:
            current_app.logger.exception("Nu am putut sterge obiectul %s", p.key)
    if trail.gpx_key:
        try:
            st.delete(trail.gpx_key)
        except Exception:
            current_app.logger.exception("Nu am putut sterge GPX-ul %s", trail.gpx_key)
    db.session.delete(trail)
    db.session.commit()
    flash("Tură ștearsă.")
    return redirect(url_for("trails.index"))


@bp.route("/photo/<int:photo_id>/delete", methods=["POST"])
@login_required
def delete_photo(photo_id):
    p = db.session.get(Photo, photo_id) or abort(404)
    trail_id = p.trail_id
    try:
        _storage().delete(p.key)
    except Exception:
        current_app.logger.exception("Nu am putut sterge obiectul %s", p.key)
    db.session.delete(p)
    db.session.commit()
    return redirect(url_for("trails.detail", trail_id=trail_id))


@bp.route("/trail/<int:trail_id>/gpx")
@login_required
def download_gpx(trail_id):
    trail = db.session.get(Trail, trail_id) or abort(404)
    if not trail.gpx_key:
        abort(404)
    st = _storage()
    if isinstance(st, LocalStorage):
        return send_file(st.path_for(trail.gpx_key), as_attachment=True,
                         download_name=f"{trail.title}.gpx")
    return redirect(st.url(trail.gpx_key))


@bp.route("/media/<path:key>")
@login_required
def local_media(key):
    """Serveste fisiere doar cand storage-ul e local (development)."""
    st = _storage()
    if not isinstance(st, LocalStorage):
        abort(404)
    return send_file(st.path_for(key))
