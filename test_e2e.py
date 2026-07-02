"""Test end-to-end pe SQLite + stocare locala.

Genereaza un GPX sintetic (urcare 800 -> 2100 m, ~6 km) si doua poze JPEG:
una cu EXIF in fereastra turei (trebuie plasata pe traseu), una cu EXIF
in afara ferestrei (trebuie respinsa). Apoi ruleaza tot fluxul prin
Flask test client: login, creare tura, detaliu, index, editare, stergere poza.
"""
import io
import math
import re
import sys
from datetime import datetime, timedelta, timezone

from PIL import Image

sys.path.insert(0, ".")
from app import create_app  # noqa: E402

START = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)


def make_gpx():
    pts = []
    n = 400
    for i in range(n):
        lat = 45.3660 + i * 0.0001          # ~ nord, Retezat-ish
        lon = 22.8800 + i * 0.00005 + 0.0006 * math.sin(i / 18)  # serpentine
        ele = 800 + 1300 * (i / (n - 1)) + (3 if i % 7 == 0 else 0)  # jitter mic
        t = START + timedelta(seconds=i * 45)  # 5 ore total
        pts.append(
            f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{ele:.1f}</ele>'
            f'<time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>'
        )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="test" '
        'xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        + "".join(pts) + "</trkseg></trk></gpx>"
    ).encode()


def make_photo(taken_local: datetime) -> bytes:
    img = Image.new("RGB", (320, 240), (60, 90, 70))
    exif = Image.Exif()
    exif[306] = taken_local.strftime("%Y:%m:%d %H:%M:%S")  # DateTime
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


app = create_app()
app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
c = app.test_client()

# --- login ---
r = c.post("/auth/login", data={"username": "dumi", "password": "test123"},
           follow_redirects=True)
assert r.status_code == 200 and "Turele mele" in r.get_data(as_text=True), "login esuat"
print("login: OK")

# --- creare tura ---
gpx = make_gpx()
photo_in = make_photo(datetime(2026, 6, 27, 11, 30))    # 08:30 UTC — pe traseu
photo_out = make_photo(datetime(2026, 6, 27, 22, 0))    # 19:00 UTC — dupa tura
r = c.post(
    "/trail/new",
    data={
        "title": "Test Peleaga", "date": "2026-06-27", "massif": "Retezat",
        "peak": "Peleaga 2509m", "marcaj": "banda_rosie", "notes": "Zi superba.\nCreasta libera.",
        "gpx": (io.BytesIO(gpx), "tura.gpx"),
        "photos": [(io.BytesIO(photo_in), "in.jpg"), (io.BytesIO(photo_out), "out.jpg")],
    },
    content_type="multipart/form-data",
    follow_redirects=True,
)
html = r.get_data(as_text=True)
assert r.status_code == 200, f"creare esuata: {r.status_code}"
assert "Test Peleaga" in html and "2 poze" in html
print("creare tura: OK")

with app.app_context():
    from app.models import Photo, Trail
    t = Trail.query.first()
    print(f"  distanta: {t.distance_km} km (asteptat ~6.1)")
    print(f"  urcare:   {t.ascent_m} m (asteptat ~1300, jitter 3m filtrat)")
    print(f"  coborare: {t.descent_m} m (asteptat ~0)")
    print(f"  elevatie: {t.elev_min_m}-{t.elev_max_m} m")
    print(f"  durata:   {t.duration_min} min (asteptat ~299)")
    print(f"  track simplificat: {len(t.track)} puncte din 400")
    assert 4.5 < t.distance_km < 7.5
    assert 1250 <= t.ascent_m <= 1400
    assert t.descent_m < 60
    assert 290 <= t.duration_min <= 305
    assert 10 < len(t.track) < 400

    ph = Photo.query.order_by(Photo.filename).all()
    p_in = next(p for p in ph if p.filename == "in.jpg")
    p_out = next(p for p in ph if p.filename == "out.jpg")
    assert p_in.on_track and p_in.lat is not None, "poza din fereastra nu a fost plasata"
    assert not p_out.on_track and p_out.lat is None, "poza din afara ferestrei a fost plasata gresit"
    print(f"  poza in fereastra plasata la ({p_in.lat:.4f},{p_in.lng:.4f}); poza tarzie respinsa")
    trail_id = t.id
    photo_out_id = p_out.id
print("statistici + potrivire poze: OK")

# --- pagini ---
r = c.get(f"/trail/{trail_id}")
html = r.get_data(as_text=True)
assert r.status_code == 200
for needle in ["Test Peleaga", "trail-map", "profile", "Notițe", "banda_rosie", "Poze (2)"]:
    assert needle in html, f"lipseste din detaliu: {needle}"
print("pagina detaliu: OK")

r = c.get("/")
html = r.get_data(as_text=True)
assert "Test Peleaga" in html and "spark" in html and "opentopomap" in html
assert re.search(r"<b>1</b> ture", html)
print("pagina index (harta + card + sparkline): OK")

# --- filtre ---
assert "Test Peleaga" in c.get("/?year=2026&massif=Retezat").get_data(as_text=True)
assert "Test Peleaga" not in c.get("/?year=2020").get_data(as_text=True)
print("filtre an/masiv: OK")

# --- descarcare GPX ---
r = c.get(f"/trail/{trail_id}/gpx")
assert r.status_code == 200 and b"<gpx" in r.data
print("descarcare GPX: OK")

# --- editare ---
r = c.post(f"/trail/{trail_id}/edit", data={
    "title": "Test Peleaga v2", "date": "2026-06-27", "massif": "Retezat",
    "peak": "Peleaga 2509m", "marcaj": "cruce_albastra", "notes": "Editat.",
}, follow_redirects=True)
assert "Test Peleaga v2" in r.get_data(as_text=True)
print("editare: OK")

# --- stergere poza ---
r = c.post(f"/photo/{photo_out_id}/delete", follow_redirects=True)
assert "Poze (1)" in r.get_data(as_text=True)
print("stergere poza: OK")

# --- flux nou: upload direct (presign -> PUT -> register) ---
# 1. creare tura prin AJAX (cum face new.html): raspuns JSON cu trail_id
r = c.post(
    "/trail/new",
    data={"title": "Test Direct Upload", "date": "2026-06-27",
          "gpx": (io.BytesIO(gpx), "tura2.gpx")},
    content_type="multipart/form-data",
    headers={"Accept": "application/json"},
)
assert r.status_code == 200, f"creare AJAX esuata: {r.status_code}"
j = r.get_json()
assert j["trail_id"] and j["redirect"], "raspunsul AJAX nu are trail_id/redirect"
t2_id = j["trail_id"]
print("creare tura prin AJAX (JSON): OK")

# 2. presign: originale + variante display pentru 3 poze
r = c.post(f"/trail/{t2_id}/photos/presign", json={
    "files": [{"name": "gps.jpg", "type": "image/jpeg"},
              {"name": "timp_in.jpg", "type": "image/jpeg"},
              {"name": "timp_out.jpg", "type": "image/jpeg"}],
})
assert r.status_code == 200, f"presign esuat: {r.status_code}"
grants = r.get_json()["files"]
assert len(grants) == 3
for g in grants:
    assert g["original_key"].startswith(f"trails/{t2_id}/photos/")
    assert g["display_key"].startswith(f"trails/{t2_id}/photos/")
    assert g["original_put_url"] and g["display_put_url"]
# tip non-imagine respins
r = c.post(f"/trail/{t2_id}/photos/presign",
           json={"files": [{"name": "x.exe", "type": "application/x-msdownload"}]})
assert r.status_code == 400, "presign a acceptat un tip non-imagine"
print("presign (chei + URL-uri PUT, tipuri validate): OK")

# 3. PUT direct in storage (echivalentul local al PUT-ului presemnat R2)
photo_bytes = make_photo(datetime(2026, 6, 27, 12, 0))
for g in grants:
    for url in (g["original_put_url"], g["display_put_url"]):
        r = c.put(url, data=photo_bytes, content_type="image/jpeg")
        assert r.status_code == 204, f"PUT local esuat: {url} -> {r.status_code}"
# cheie in afara formatului generat (alt kind decat photos) -> respinsa
r = c.put("/media-put/trails/999/gpx/x.gpx", data=b"x", content_type="image/jpeg")
assert r.status_code == 404, "PUT local a acceptat o cheie invalida"
print("PUT direct in storage local: OK")

# 4. register: GPS explicit / doar timestamp in fereastra / timestamp in afara
r = c.post(f"/trail/{t2_id}/photos/register", json={"photos": [
    {"original_key": grants[0]["original_key"], "display_key": grants[0]["display_key"],
     "filename": "gps.jpg", "taken_at": "2026:06:27 11:00:00",
     "lat": 45.3702, "lng": 22.8821},
    {"original_key": grants[1]["original_key"], "display_key": grants[1]["display_key"],
     "filename": "timp_in.jpg", "taken_at": "2026:06:27 11:30:00"},
    {"original_key": grants[2]["original_key"], "display_key": grants[2]["display_key"],
     "filename": "timp_out.jpg", "taken_at": "2026:06:27 22:00:00"},
]})
assert r.status_code == 200, f"register esuat: {r.status_code} {r.get_data(as_text=True)}"
assert r.get_json()["added"] == 3
# cheie care nu apartine turei -> respinsa, nimic salvat
r = c.post(f"/trail/{t2_id}/photos/register", json={"photos": [
    {"display_key": f"trails/{trail_id}/photos/furt.jpg"},
]})
assert r.status_code == 400, "register a acceptat o cheie straina"

with app.app_context():
    from app.models import Photo
    ph2 = {p.filename: p for p in Photo.query.filter_by(trail_id=t2_id).all()}
    assert len(ph2) == 3
    p_gps = ph2["gps.jpg"]
    assert p_gps.on_track and abs(p_gps.lat - 45.3702) < 1e-6, "GPS-ul explicit nu a fost pastrat"
    assert p_gps.original_key and p_gps.original_key != p_gps.key
    p_tin = ph2["timp_in.jpg"]
    assert p_tin.on_track and p_tin.lat is not None, "poza cu timestamp in fereastra nu a fost plasata"
    p_tout = ph2["timp_out.jpg"]
    assert not p_tout.on_track and p_tout.lat is None, "poza din afara ferestrei a fost plasata gresit"
    assert p_tin.taken_at.hour == 11 and p_tin.taken_at.minute == 30
print("register (GPS pastrat, potrivire timestamp pe GPX recitit, chei validate): OK")

# 5. pagina detaliu: galerie pe display, link Original in lightbox
html = c.get(f"/trail/{t2_id}").get_data(as_text=True)
assert "Poze (3)" in html and "orig_url" in html and "lightbox-orig" in html
assert "add-photos-btn" in html, "lipseste butonul de adaugare poze din detaliu"
assert "select-btn" in html and "sel-delete" in html, "lipseste selectia multipla"
print("detaliu cu varianta display + link Original + buton adaugare: OK")

# 5b. stergere in masa: 2 poze proprii + un id strain (ignorat) + un id inexistent
with app.app_context():
    from app.models import Photo
    ph2 = {p.filename: p.id for p in Photo.query.filter_by(trail_id=t2_id).all()}
    foreign_id = Photo.query.filter_by(trail_id=trail_id).first().id
    tin_display = Photo.query.get(ph2["timp_in.jpg"]).key
    st = app.extensions["storage"]
    tin_path = st.path_for(tin_display)
import os as _os
assert _os.path.exists(tin_path)
r = c.post(f"/trail/{t2_id}/photos/delete", json={
    "photo_ids": [ph2["timp_in.jpg"], ph2["timp_out.jpg"], foreign_id, 999999],
})
assert r.status_code == 200, f"stergere in masa esuata: {r.status_code}"
assert r.get_json()["deleted"] == 2, "trebuia sa stearga exact 2 poze"
assert not _os.path.exists(tin_path), "obiectul display nu a fost sters din storage"
with app.app_context():
    from app.models import Photo
    assert Photo.query.filter_by(trail_id=t2_id).count() == 1
    assert Photo.query.get(foreign_id) is not None, "a sters o poza din alta tura!"
assert "Poze (1)" in c.get(f"/trail/{t2_id}").get_data(as_text=True)
# lista goala -> 400
r = c.post(f"/trail/{t2_id}/photos/delete", json={"photo_ids": []})
assert r.status_code == 400
print("stergere in masa (doar pozele turei, storage curatat): OK")

# 6. stergerea turei curata si originalele din storage
import os as _os
with app.app_context():
    st = app.extensions["storage"]
    orig_path = st.path_for(grants[0]["original_key"])
    assert _os.path.exists(orig_path)
r = c.post(f"/trail/{t2_id}/delete", follow_redirects=True)
assert r.status_code == 200
assert not _os.path.exists(orig_path), "originalul nu a fost sters din storage"
print("stergere tura cu originale (storage curatat): OK")

# --- stergere tura ---
r = c.post(f"/trail/{trail_id}/delete", follow_redirects=True)
assert "Nicio tură încă" in r.get_data(as_text=True)
with app.app_context():
    from app.models import Photo, Trail
    assert Trail.query.count() == 0 and Photo.query.count() == 0
print("stergere tura (cascade + storage): OK")

print("\nTOATE TESTELE AU TRECUT")
