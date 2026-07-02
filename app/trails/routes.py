import io
import json
import re
from datetime import date, datetime

from flask import (abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, url_for)
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


def _gpx_points(trail):
    """Reciteste GPX-ul din storage pentru plasarea pozelor. None daca nu se poate."""
    if not trail.gpx_key:
        return None
    try:
        st = _storage()
        if isinstance(st, LocalStorage):
            with open(st.path_for(trail.gpx_key), "rb") as fh:
                return parse_gpx(fh, current_app.config)["_points"]
        obj = st.client.get_object(Bucket=st.bucket, Key=trail.gpx_key)
        return parse_gpx(obj["Body"], current_app.config)["_points"]
    except Exception:
        current_app.logger.exception("Nu am putut reciti GPX-ul pentru plasarea pozelor")
        return None


def _parse_taken_at(raw):
    """Accepta formatul EXIF brut (2026:06:27 11:30:00) sau ISO 8601."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


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
    st = _storage()
    photos = [
        {
            "id": p.id, "url": st.url(p.key), "caption": p.caption or "",
            "orig_url": st.url(p.original_key) if p.original_key else st.url(p.key),
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
    wants_json = "application/json" in request.headers.get("Accept", "")
    if request.method == "POST":
        gpx_file = request.files.get("gpx")
        if not gpx_file or not gpx_file.filename:
            if wants_json:
                return jsonify(error="Lipsește fișierul GPX."), 400
            flash("Lipsește fișierul GPX.")
            return render_template("new.html", marcaje=MARCAJE, today=date.today().isoformat())

        gpx_bytes = gpx_file.read()
        try:
            stats = parse_gpx(io.BytesIO(gpx_bytes), current_app.config)
        except Exception as e:
            if wants_json:
                return jsonify(error=f"GPX invalid: {e}"), 400
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
        if wants_json:
            # fluxul AJAX din new.html: creeaza tura, apoi urca pozele direct in R2
            return jsonify(trail_id=trail.id,
                           redirect=url_for("trails.detail", trail_id=trail.id))
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

        # corectie manuala de altitudine maxima: gol = valoarea din GPX
        raw_elev = request.form.get("elev_max_override", "").strip()
        if raw_elev:
            try:
                trail.elev_max_override = max(0, min(9000, int(raw_elev)))
            except ValueError:
                flash("Corecția de altitudine trebuie să fie un număr — am ignorat-o.")
        else:
            trail.elev_max_override = None

        files = request.files.getlist("photos")
        if any(f.filename for f in files):
            _attach_photos(trail, files, _gpx_points(trail))

        db.session.commit()
        flash("Tură actualizată.")
        return redirect(url_for("trails.detail", trail_id=trail.id))

    return render_template("edit.html", trail=trail, marcaje=MARCAJE)


@bp.route("/trail/<int:trail_id>/photos/presign", methods=["POST"])
@login_required
def presign_photos(trail_id):
    """Intoarce URL-uri presemnate de PUT: browserul urca direct in R2,
    serverul nu mai atinge bytes de poze."""
    trail = db.session.get(Trail, trail_id) or abort(404)
    data = request.get_json(silent=True) or {}
    files = data.get("files")
    if not isinstance(files, list) or not 0 < len(files) <= 50:
        return jsonify(error="Trimite o listă de 1–50 fișiere."), 400

    st = _storage()
    out = []
    for f in files:
        if not isinstance(f, dict):
            return jsonify(error="Fiecare fișier trebuie să aibă nume și tip."), 400
        name = (f.get("name") or "").strip() or "photo.jpg"
        ctype = (f.get("type") or "").strip() or "image/jpeg"
        if not ctype.startswith("image/"):
            return jsonify(error=f"Tip de fișier neacceptat: {ctype}"), 400
        original_key = new_key(trail.id, "photos", name)
        display_key = new_key(trail.id, "photos", "display.jpg")
        out.append({
            "name": name,
            "original_key": original_key,
            "original_put_url": st.put_url(original_key, ctype),
            "original_content_type": ctype,
            "display_key": display_key,
            "display_put_url": st.put_url(display_key, "image/jpeg"),
        })
    return jsonify(files=out)


@bp.route("/trail/<int:trail_id>/photos/register", methods=["POST"])
@login_required
def register_photos(trail_id):
    """Creeaza randurile Photo dupa ce browserul a urcat obiectele in R2.

    Plasarea pe traseu ramane pe server: GPS-ul din EXIF are prioritate,
    altfel potrivim timestamp-ul pe punctele GPX recitite din storage.
    """
    trail = db.session.get(Trail, trail_id) or abort(404)
    data = request.get_json(silent=True) or {}
    photos = data.get("photos")
    if not isinstance(photos, list) or not 0 < len(photos) <= 50:
        return jsonify(error="Trimite o listă de 1–50 poze."), 400

    prefix = f"trails/{trail.id}/photos/"
    cfg = current_app.config
    gpx_points = None
    gpx_loaded = False
    added = []
    for ph in photos:
        if not isinstance(ph, dict):
            return jsonify(error="Fiecare poză trebuie să fie un obiect."), 400
        display_key = ph.get("display_key") or ""
        original_key = ph.get("original_key") or None
        if not display_key.startswith(prefix) or \
                (original_key and not original_key.startswith(prefix)):
            return jsonify(error="Cheie de obiect invalidă pentru această tură."), 400

        taken_at = _parse_taken_at(ph.get("taken_at"))
        try:
            lat = float(ph["lat"]) if ph.get("lat") is not None else None
            lng = float(ph["lng"]) if ph.get("lng") is not None else None
        except (TypeError, ValueError):
            lat = lng = None
        if lat is None or lng is None:
            lat = lng = None

        on_track = lat is not None
        if lat is None and taken_at:
            if not gpx_loaded:
                gpx_points = _gpx_points(trail)
                gpx_loaded = True
            if gpx_points:
                pos = match_photo_to_track(taken_at, gpx_points, cfg)
                if pos:
                    lat, lng = pos
                    on_track = True

        p = Photo(
            trail_id=trail.id, key=display_key, original_key=original_key,
            filename=(ph.get("filename") or "").strip()[:200] or None,
            taken_at=taken_at, lat=lat, lng=lng, on_track=on_track,
        )
        db.session.add(p)
        added.append(p)

    db.session.commit()
    return jsonify(added=len(added), photo_ids=[p.id for p in added])


@bp.route("/trail/<int:trail_id>/delete", methods=["POST"])
@login_required
def delete(trail_id):
    trail = db.session.get(Trail, trail_id) or abort(404)
    st = _storage()
    for p in trail.photos:
        for k in filter(None, (p.key, p.original_key)):
            try:
                st.delete(k)
            except Exception:
                current_app.logger.exception("Nu am putut sterge obiectul %s", k)
    if trail.gpx_key:
        try:
            st.delete(trail.gpx_key)
        except Exception:
            current_app.logger.exception("Nu am putut sterge GPX-ul %s", trail.gpx_key)
    db.session.delete(trail)
    db.session.commit()
    flash("Tură ștearsă.")
    return redirect(url_for("trails.index"))


@bp.route("/trail/<int:trail_id>/photos/delete", methods=["POST"])
@login_required
def delete_photos_bulk(trail_id):
    """Sterge mai multe poze odata (selectie multipla din galerie).

    Sterge doar poze care apartin turei — id-urile straine sunt ignorate.
    """
    trail = db.session.get(Trail, trail_id) or abort(404)
    data = request.get_json(silent=True) or {}
    ids = data.get("photo_ids")
    if not isinstance(ids, list) or not 0 < len(ids) <= 500:
        return jsonify(error="Trimite o listă de id-uri de poze (1–500)."), 400
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify(error="Id-uri invalide."), 400

    photos = Photo.query.filter(Photo.trail_id == trail.id, Photo.id.in_(ids)).all()
    st = _storage()
    for p in photos:
        for k in filter(None, (p.key, p.original_key)):
            try:
                st.delete(k)
            except Exception:
                current_app.logger.exception("Nu am putut sterge obiectul %s", k)
        db.session.delete(p)
    db.session.commit()
    return jsonify(deleted=len(photos))


@bp.route("/photo/<int:photo_id>/delete", methods=["POST"])
@login_required
def delete_photo(photo_id):
    p = db.session.get(Photo, photo_id) or abort(404)
    trail_id = p.trail_id
    for k in filter(None, (p.key, p.original_key)):
        try:
            _storage().delete(k)
        except Exception:
            current_app.logger.exception("Nu am putut sterge obiectul %s", k)
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


_LOCAL_PUT_KEY = re.compile(r"^trails/\d+/photos/[A-Za-z0-9._-]+$")


@bp.route("/media-put/<path:key>", methods=["PUT"])
@login_required
def local_media_put(key):
    """Echivalentul local al PUT-ului presemnat din R2 (doar development).

    Cheia e restrictionata la formatul generat de new_key, ca sa nu se
    poata scrie in afara directorului de upload.
    """
    st = _storage()
    if not isinstance(st, LocalStorage) or not _LOCAL_PUT_KEY.match(key):
        abort(404)
    st.put(key, request.get_data(), request.content_type or "application/octet-stream")
    return "", 204
