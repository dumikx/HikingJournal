"""Parsare GPX + potrivirea pozelor pe traseu.

Toti parametrii vin din Config (GPX_* / PHOTO_*), ca sa poata fi ajustati
din variabile de mediu fara modificari de cod.
"""
import math
import re
from datetime import timedelta

import gpxpy


def _fix_unbound_prefixes(xml_text):
    """Repara GPX-uri cu prefixe XML nedeclarate (ex: gpxtpx: fara xmlns).

    Unele tool-uri (mergere, convertoare) emit extensii cu prefixe fara sa
    declare namespace-ul — XML invalid pe care parserul strict il respinge.
    Declaram prefixele lipsa pe tagul radacina si lasam parsarea sa continue;
    extensiile oricum nu ne intereseaza. Intoarce None daca nu e cazul.
    """
    used = set(re.findall(r"</?([A-Za-z_][\w.-]*):", xml_text))
    declared = set(re.findall(r"xmlns:([A-Za-z_][\w.-]*)\s*=", xml_text))
    missing = used - declared - {"xml"}
    if not missing:
        return None
    decls = " ".join(f'xmlns:{p}="urn:ignore:{p}"' for p in sorted(missing))
    fixed = re.sub(r"<gpx\b", f"<gpx {decls} ", xml_text, count=1)
    return fixed if fixed != xml_text else None


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _rdp(points, tolerance_m):
    """Douglas-Peucker pe (lat, lng), distanta aproximata in metri."""
    if len(points) < 3:
        return points

    def perp_dist(pt, a, b):
        # proiectie locala echirectangulara — suficienta la scara unui traseu
        lat0 = math.radians(a[0])
        ax, ay = 0.0, 0.0
        bx = _m_x(b, a, lat0)
        by = _m_y(b, a)
        px = _m_x(pt, a, lat0)
        py = _m_y(pt, a)
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px, py)
        t = max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - t * dx, py - t * dy)

    def _m_x(p, origin, lat0):
        return math.radians(p[1] - origin[1]) * 6371000.0 * math.cos(lat0)

    def _m_y(p, origin):
        return math.radians(p[0] - origin[0]) * 6371000.0

    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        start, end = stack.pop()
        max_d, idx = 0.0, -1
        for i in range(start + 1, end):
            d = perp_dist(points[i], points[start], points[end])
            if d > max_d:
                max_d, idx = d, i
        if max_d > tolerance_m and idx > 0:
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))
    return [p for p, k in zip(points, keep) if k]


def parse_gpx(file_obj, config):
    """Intoarce dict cu statistici, traseu simplificat si profil de elevatie."""
    raw = file_obj.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="replace")
    try:
        gpx = gpxpy.parse(raw)
    except Exception:
        fixed = _fix_unbound_prefixes(raw)
        if fixed is None:
            raise
        gpx = gpxpy.parse(fixed)

    pts = []  # (lat, lon, ele, time)
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                pts.append((p.latitude, p.longitude, p.elevation or 0.0, p.time))
    if not pts and gpx.routes:
        for route in gpx.routes:
            for p in route.points:
                pts.append((p.latitude, p.longitude, p.elevation or 0.0, p.time))
    if len(pts) < 2:
        raise ValueError("GPX-ul nu contine un traseu (minim 2 puncte).")

    hyst = config["GPX_HYSTERESIS_M"]
    moving_thresh_ms = config["GPX_MOVING_SPEED_KMH"] / 3.6

    dist_3d = 0.0
    ascent = descent = 0.0
    moving_s = 0.0
    ref_ele = pts[0][2]
    cum_dist = [0.0]

    for i in range(1, len(pts)):
        lat1, lon1, e1, t1 = pts[i - 1]
        lat2, lon2, e2, t2 = pts[i]
        d2 = _haversine_m(lat1, lon1, lat2, lon2)
        de = e2 - e1
        d3 = math.sqrt(d2 * d2 + de * de)
        dist_3d += d3
        cum_dist.append(dist_3d)

        # urcare/coborare cu histerezis: ignora oscilatiile sub prag (jitter GPS)
        delta = e2 - ref_ele
        if delta >= hyst:
            ascent += delta
            ref_ele = e2
        elif delta <= -hyst:
            descent += -delta
            ref_ele = e2

        if t1 and t2:
            dt = (t2 - t1).total_seconds()
            if 0 < dt < 3600 and (d2 / dt) >= moving_thresh_ms:
                moving_s += dt

    times = [p[3] for p in pts if p[3]]
    duration_min = int((times[-1] - times[0]).total_seconds() // 60) if len(times) >= 2 else None

    elevations = [p[2] for p in pts]

    # traseu simplificat pentru harta
    simplified = _rdp([(p[0], p[1]) for p in pts], config["GPX_SIMPLIFY_TOLERANCE_M"])
    simplified = [[round(la, 6), round(lo, 6)] for la, lo in simplified]

    # profil de elevatie, esantionat uniform pe distanta
    n = min(config["GPX_PROFILE_POINTS"], len(pts))
    prof_d, prof_e = [], []
    step = dist_3d / (n - 1) if n > 1 else 1
    j = 0
    for k in range(n):
        target = k * step
        while j < len(cum_dist) - 1 and cum_dist[j + 1] < target:
            j += 1
        prof_d.append(round(cum_dist[j] / 1000, 3))
        prof_e.append(round(elevations[j]))

    return {
        "distance_km": round(dist_3d / 1000, 2),
        "ascent_m": int(round(ascent)),
        "descent_m": int(round(descent)),
        "elev_min_m": int(round(min(elevations))),
        "elev_max_m": int(round(max(elevations))),
        "duration_min": duration_min,
        "moving_min": int(moving_s // 60) if moving_s else None,
        "start_lat": pts[0][0],
        "start_lng": pts[0][1],
        "track": simplified,
        "profile": {"d": prof_d, "e": prof_e},
        "_points": pts,  # pentru potrivirea pozelor; nu se salveaza in DB
    }


def match_photo_to_track(taken_at_local, points, config):
    """Plaseaza o poza pe traseu dupa timestamp.

    EXIF e in ora locala; punctele GPX au timp UTC. Aplicam offsetul
    configurat, apoi cautam punctul cel mai apropiat in timp. Daca gap-ul
    depaseste pragul, poza nu se plaseaza (a fost facuta in alta parte).
    """
    timed = [(p[3], p[0], p[1]) for p in points if p[3]]
    if not timed or not taken_at_local:
        return None

    offset = timedelta(hours=config["PHOTO_TZ_OFFSET_HOURS"])
    target_utc = taken_at_local - offset

    best = None
    best_gap = None
    for t, lat, lng in timed:
        t_naive = t.replace(tzinfo=None)
        gap = abs((t_naive - target_utc).total_seconds())
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best = (lat, lng)

    if best_gap is not None and best_gap <= config["PHOTO_MATCH_MAX_GAP_MIN"] * 60:
        return best
    return None
