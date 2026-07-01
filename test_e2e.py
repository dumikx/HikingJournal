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

# --- stergere tura ---
r = c.post(f"/trail/{trail_id}/delete", follow_redirects=True)
assert "Nicio tură încă" in r.get_data(as_text=True)
with app.app_context():
    from app.models import Photo, Trail
    assert Trail.query.count() == 0 and Photo.query.count() == 0
print("stergere tura (cascade + storage): OK")

print("\nTOATE TESTELE AU TRECUT")
