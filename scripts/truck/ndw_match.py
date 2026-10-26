#!/usr/bin/env python3
"""
Koppelt NDW-verkeersborden aan OSM-wegen (bij de tegelbouw), zodat de app op geometrie + rijrichting matcht en niet op
straatnaam + afstand (dat gaf fouten bij parallelle wegen met dezelfde naam, bv. Ringbaan-Oost in Tilburg).

Per bord:
 1. NWB-wegvak (roadSectionId = WVK_ID) + fraction → punt op de NWB-wegas; drivingDirection H/T → rijrichting
    (H = met de digitaliseerrichting van het wegvak mee, T = ertegenin, leeg = beide).
 2. Dat punt op de dichtstbijzijnde OSM-autoweg leggen die evenwijdig loopt (max 12 m, as ±30°).
    Zonder (vindbaar) wegvak: het bordpunt zelf, max 15 m; twijfel tussen twee wegen = 'onzeker' (alleen waarschuwen).
 3. Punt niet precies op een kruispunt (wegeinde): 6 m het wegdeel in, zodat een kruisende route het niet raakt.
Uit: lijst [lat, lon, hdg, flags, osm_way_id], hdg = rijrichting (0–359) of 1000 + as (beide richtingen).
Bronnen: NWB (Rijkswaterstaat, CC0) via PDOK GeoPackage, OSM (ODbL), NDW (open data).
"""
import math, sqlite3, struct, sys

DRIVABLE = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link',
            'tertiary', 'tertiary_link', 'unclassified', 'residential', 'living_street', 'service', 'road'}
MOTORWAY = {'motorway', 'motorway_link'}
MAJOR = MOTORWAY | {'trunk', 'trunk_link', 'primary', 'primary_link'}
FLAG_MOTORWAY, FLAG_UNSURE = 2, 4
CELL = 0.0004

def bearing(a, b):
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = math.cos(math.radians(a[0])) * math.sin(math.radians(b[0])) - math.sin(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(math.radians(b[1] - a[1]))
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def adiff(a, b):
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d

def axdiff(a, b):
    return min(adiff(a, b), adiff(a + 180, b))

def gpkg_wkb(blob):
    flags = blob[3]
    env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    return bytes(blob[8 + env:])

def nwb_points(gpkg, signs):
    """signs: list of dict met wvk, frac, dir. Vult s['nwb'] = (lat, lon, travel|None, axis)."""
    from shapely import wkb
    from pyproj import Transformer
    con = sqlite3.connect(gpkg)
    tab, col, srs = con.execute("select table_name, column_name, srs_id from gpkg_geometry_columns where table_name = 'wegvakken'").fetchone()
    cols = [r[1] for r in con.execute(f'pragma table_info("{tab}")')]
    idc = next(c for c in cols if c.lower() == 'wvk_id')
    tr = Transformer.from_crs(f'EPSG:{srs}', 'EPSG:4326', always_xy=True)
    want = sorted({s['wvk'] for s in signs if s['wvk']})
    geom = {}
    for i in range(0, len(want), 900):
        part = want[i:i + 900]
        q = f'select "{idc}", "{col}" from "{tab}" where "{idc}" in ({",".join("?" * len(part))})'
        for wid, blob in con.execute(q, part):
            if blob is None: continue
            g = wkb.loads(gpkg_wkb(blob))
            if g.geom_type == 'MultiLineString': g = max(g.geoms, key=lambda x: x.length)
            geom[int(wid)] = g
    con.close()
    hit = 0
    for s in signs:
        g = geom.get(s['wvk'])
        if g is None or s['frac'] is None: continue
        f = min(1.0, max(0.0, s['frac']))
        L = g.length
        p = g.interpolate(f * L)
        a = g.interpolate(max(0.0, f * L - 5)); b = g.interpolate(min(L, f * L + 5))
        lon, lat = tr.transform(p.x, p.y)
        alon, alat = tr.transform(a.x, a.y); blon, blat = tr.transform(b.x, b.y)
        brg = bearing((alat, alon), (blat, blon))
        travel = brg if s['dir'] == 'H' else (brg + 180) % 360 if s['dir'] == 'T' else None
        s['nwb'] = (lat, lon, travel, brg % 180)
        hit += 1
    return hit, len(want), len(geom)

def osm_candidates(pbf, targets, idx_path):
    """targets: list of (lat, lon, maxM). Geeft per target een lijst kandidaten (d, way, hw, lat, lon, segbrg)."""
    import osmium
    grid = {}
    for i, (lat, lon, m) in enumerate(targets):
        grid.setdefault((int(math.floor(lat / CELL)), int(math.floor(lon / CELL))), []).append(i)
    cands = [[] for _ in targets]

    class H(osmium.SimpleHandler):
        def way(self, w):
            hw = w.tags.get('highway')
            if hw not in DRIVABLE: return
            if w.tags.get('access') in ('no', 'private') and hw == 'service': return
            try:
                pts = [(n.lat, n.lon) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(pts) < 2: return
            cum = [0.0]
            for k in range(1, len(pts)):
                cum.append(cum[-1] + d_m(pts[k - 1], pts[k]))
            for k in range(1, len(pts)):
                a, b = pts[k - 1], pts[k]
                x0, x1 = sorted((int(math.floor(a[0] / CELL)), int(math.floor(b[0] / CELL))))
                y0, y1 = sorted((int(math.floor(a[1] / CELL)), int(math.floor(b[1] / CELL))))
                if (x1 - x0 + 3) * (y1 - y0 + 3) > 400: continue  # extreem lang segment: overslaan
                for x in range(x0 - 1, x1 + 2):
                    for y in range(y0 - 1, y1 + 2):
                        for i in grid.get((x, y), ()):
                            t = targets[i]
                            d, t01, q = proj(t, a, b)
                            if d <= t[2]:
                                cands[i].append((d, w.id, hw, q, bearing(a, b), cum[k - 1] + t01 * (cum[k] - cum[k - 1]), cum[-1], pts, cum))

    H().apply_file(pbf, locations=True, idx=f'sparse_file_array,{idx_path}')
    return cands

def d_m(a, b):
    kx = 111320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110540, (a[1] - b[1]) * kx)

def proj(p, a, b):
    kx = 111320 * math.cos(math.radians(p[0])); ky = 110540
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(qx, qy), t, (p[0] + qy / ky, p[1] + qx / kx)

def point_along(pts, cum, s):
    k = 1
    while k < len(pts) - 1 and cum[k] < s: k += 1
    a, b = pts[k - 1], pts[k]
    seg = (cum[k] - cum[k - 1]) or 1
    t = max(0.0, min(1.0, (s - cum[k - 1]) / seg))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), bearing(a, b)

def match(signs, pbf, gpkg, idx_path):
    stats = {'signs': len(signs)}
    if gpkg:
        stats['nwb_hit'], stats['wvk_wanted'], stats['wvk_found'] = nwb_points(gpkg, signs)
    # Doelpunt + verwachte rijrichting per bord.
    #  - H/T met NWB-wegvak: punt op de NWB-wegas, richting uit het wegvak (klopt in 98% met NDW 'bearing').
    #  - anders (richting leeg of geen wegvak): het bordpunt zelf + NDW 'bearing'. Bij lege richting staat het bord
    #    meestal aan een zijweg (bearing staat dwars op het wegvak) en is bearing grof (0/90/180/270): ruimere marge.
    targets, want = [], []
    for s in signs:
        nwb = s.get('nwb')
        if nwb and s['dir']:
            targets.append((nwb[0], nwb[1], 12.0)); want.append((nwb[2], 30, True, nwb[3]))
        elif nwb:
            # Richting leeg: bearing is hier onbetrouwbaar (vaak dwars op de weg). Wel het wegvak volgen, beide richtingen.
            targets.append((nwb[0], nwb[1], 12.0)); want.append((None, 30, True, nwb[3]))
        else:
            targets.append((s['lat'], s['lon'], 15.0))
            want.append((s.get('brg') if s['dir'] else None, 30, False, None))
    cands = osm_candidates(pbf, targets, idx_path)
    out = []
    n_nwb = n_fb = n_unsure = n_drop = 0
    for s, cs, (travel, tol, via_nwb, axis) in zip(signs, cands, want):
        nwb = via_nwb
        ref = travel if travel is not None else axis
        if ref is not None:
            cs = [c for c in cs if axdiff(c[4], ref) <= tol]
        if not cs:
            n_drop += 1; out.append(None); continue
        cs.sort(key=lambda c: c[0])
        best = cs[0]
        flags = 0
        rival = next((c for c in cs[1:] if c[1] != best[1] and d_m(c[3], best[3]) > 4 and c[0] <= best[0] + (2.5 if nwb else 4)), None)
        # Onzeker (alleen waarschuwen): twee kandidaat-wegen, geen richting en geen wegvak, of een bord zonder rijrichting
        # op een hoofdweg (staat dan vrijwel altijd voor een zijweg of ventweg, bv. C17 5 m langs de N65).
        if rival is not None or ref is None or (travel is None and best[2] in MAJOR):
            flags |= FLAG_UNSURE; n_unsure += 1
        if best[2] in MOTORWAY: flags |= FLAG_MOTORWAY
        _, wid, hw, q, segb, along, total, pts, cum = best
        # Niet op een kruispunt (wegeinde): 6 m het wegdeel in.
        if total > 12 and (along < 3 or total - along < 3):
            along = 6.0 if along < 3 else total - 6.0
            q, segb = point_along(pts, cum, along)
        if travel is not None:
            hdg = int(round(segb if adiff(segb, travel) <= 90 else (segb + 180) % 360)) % 360
        else:
            hdg = 1000 + int(round(segb % 180)) % 180
        if nwb: n_nwb += 1
        else: n_fb += 1
        out.append((round(q[0], 6), round(q[1], 6), hdg, flags, wid))
    stats.update(matched_nwb=n_nwb, matched_fallback=n_fb, unsure=n_unsure, dropped=n_drop)
    return out, stats

def refine_on_graph(points, config):
    """Legt elk gekoppeld bordpunt exact op een Valhalla-wegdeel (= de weg waar de app over routeert).
    points: lijst [lat, lon, hdg, flags] (wordt aangepast). Geen evenwijdig routeerbaar wegdeel binnen 6 m: None
    (zo'n weg komt in geen enkele route voor; laten staan zou alleen een vals 'raak' op de weg ernaast geven)."""
    import json, valhalla
    cfg = json.load(open(config))
    cfg.setdefault('service_limits', {}).setdefault('auto', {})['max_locations'] = 1000
    cfg['service_limits']['max_radius'] = 50
    tmp = config + '.locate.json'
    json.dump(cfg, open(tmp, 'w'))
    actor = valhalla.Actor(tmp)
    moved = dropped = unsure = 0
    for i in range(0, len(points), 500):
        part = [(k, p) for k, p in enumerate(points[i:i + 500], i) if p is not None]
        if not part: continue
        req = {'locations': [{'lat': p[0], 'lon': p[1], 'radius': 6} for _, p in part], 'costing': 'auto', 'verbose': True}
        res = json.loads(actor.locate(json.dumps(req)))
        for (k, p), r in zip(part, res):
            ref = p[2] - 1000 if p[2] >= 1000 else p[2]
            par = sorted((e for e in r.get('edges') or [] if e.get('distance', 99) <= 6 and axdiff(e['heading'], ref) <= 30),
                         key=lambda e: e['distance'])
            if not par:
                points[k] = None; dropped += 1; continue
            best = par[0]
            bp = (best['correlated_lat'], best['correlated_lon'])
            # Tweede evenwijdige rijbaan vlak ernaast (bv. twee keer 'Rijswijkseweg' onder één viaduct): niet zeker welke.
            if any(d_m(bp, (e['correlated_lat'], e['correlated_lon'])) > 3 and e['distance'] <= best['distance'] + 2 for e in par[1:]):
                p[3] |= FLAG_UNSURE; unsure += 1
            p[0], p[1] = round(best['correlated_lat'], 6), round(best['correlated_lon'], 6)
            h = best['heading']
            p[2] = 1000 + int(round(h % 180)) % 180 if p[2] >= 1000 else int(round(h if adiff(h, ref) <= 90 else (h + 180) % 360)) % 360
            moved += 1
    import os; os.remove(tmp)
    return moved, dropped, unsure
