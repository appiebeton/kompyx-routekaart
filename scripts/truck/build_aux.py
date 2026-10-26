#!/usr/bin/env python3
"""
Bouwt restrictions.json: compacte puntenlijst met vrachtwagenbeperkingen die Valhalla-tegels NIET dekken
of die we apart willen waarschuwen.

Bronnen:
  - OSM-knopen met maxheight/maxwidth/maxweight/maxlength (bv. hoogteportalen, barrier=height_restrictor).
    Valhalla leest deze tags alleen op wegen (ways), niet op knopen: dit is het "knoop-gat".
  - NDW verkeersborden (actueel beeld, CSV): C7 (geen vrachtauto), C17 lengte, C18 breedte, C19 hoogte,
    C20 aslast, C21 totaalgewicht. Licentie: NDW open data, vrij herbruikbaar.

Formaat (v2): {"v":2,"built":ISO,"osm":ISO|null,"ndw":ISO|null,"nwb":str|null,"p":[[lat,lon,k,val,hdg,src,flags],...]}
  k    : h=hoogte(m) b=breedte(m) l=lengte(m) w=gewicht(t) a=aslast(t) n=geen vrachtauto (C7)
  hdg  : -1 = geen (OSM-knoop: geldt in elke richting op die plek)
         0-359 = rijrichting waarvoor het bord geldt (op de OSM-weg gelegd)
         1000-1179 = as van de weg (beide richtingen, alleen op deze weg; kruisende wegen tellen niet)
  src  : o=OSM-knoop, n=NDW-bord
  flags: 1 = onderbord met uitzondering, 2 = snelweg, 4 = koppeling onzeker. Elk van de drie: alleen waarschuwen.
NDW-borden worden via het NWB-wegvak (roadSectionId + fraction + drivingDirection) op een OSM-weg gelegd: zie ndw_match.py.
Gebruik: [VALHALLA_CONFIG=valhalla.json] build_aux.py <nl.osm.pbf> <signs.csv.gz|-> <out.json> [nwb_wegen.gpkg]
  Met VALHALLA_CONFIG wordt elk bordpunt daarna exact op een routeerbaar wegdeel van de tegels gelegd.
"""

import csv, gzip, io, json, re, sys, datetime

def num(v):
    if v is None: return None
    s = str(v).strip().lower().replace(',', '.')
    if s in ('', 'none', 'default', 'below_default', 'no', 'unsigned', 'physical'): return None
    m = re.match(r"^(\d+)'\s*(\d+)?\"?$", s)  # voet/inch
    if m:
        return round(int(m.group(1)) * 0.3048 + (int(m.group(2) or 0)) * 0.0254, 2)
    m = re.match(r'^([0-9]*\.?[0-9]+)\s*(m|t|st|ton|tons)?$', s)
    if not m: return None
    x = float(m.group(1))
    if m.group(2) == 'st': x *= 0.907
    return x

SANE = {'h': (1.5, 6.0), 'b': (1.5, 4.5), 'l': (3, 30), 'w': (1, 80), 'a': (1, 30)}
def sane(k, v):
    if v is None: return False
    lo, hi = SANE[k]; return lo <= v <= hi

def osm_nodes(pbf):
    import osmium
    out = []
    ts = None
    class H(osmium.SimpleHandler):
        def node(self, n):
            t = n.tags
            for tag, k in (('maxheight', 'h'), ('maxheight:physical', 'h'), ('maxwidth', 'b'), ('maxwidth:physical', 'b'),
                           ('maxlength', 'l'), ('maxweight', 'w'), ('maxaxleload', 'a')):
                if tag in t:
                    v = num(t[tag])
                    if sane(k, v):
                        out.append([round(n.location.lat, 6), round(n.location.lon, 6), k, round(v, 2), -1, 'o', 0])
                        break
    r = osmium.io.Reader(pbf, osmium.osm.osm_entity_bits.NOTHING)
    ts = r.header().get('osmosis_replication_timestamp') or None
    r.close()
    H().apply_file(pbf, locations=False)
    return out, ts

CODES = {'C19': 'h', 'C18': 'b', 'C17': 'l', 'C21': 'w', 'C20': 'a', 'C7': 'n'}
VOOR = re.compile(r'\bVOOR\(|\bKEN\(')
# Onderbord met uitzondering (UIT), tijdvenster (TIJD) of pijl (RICH: geldt voor een zijweg): alleen waarschuwen.
WARN_ONLY = re.compile(r'\b(UIT|TIJD|RICH)\(')
EXEMPT = re.compile(r'uitgez|uitzonder|bestemming|laden|lossen|ontheffing|vergunning|landbouw|bus|m\.u\.v|behalve', re.I)

def ndw_signs(path):
    out = []
    newest = ''
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rt', encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            code = (row.get('rvvCode') or '').strip()
            k = CODES.get(code)
            if not k or row.get('status') != 'PLACED' or row.get('removedOn'): continue
            try:
                lat = float(row['latitude']); lon = float(row['longitude'])
            except (KeyError, ValueError):
                continue
            if not (50.7 <= lat <= 53.7 and 3.2 <= lon <= 7.3): continue
            v = 0.0
            if k != 'n':
                v = num(row.get('blackCode'))
                if not sane(k, v): continue
            try: wvk = int(row.get('roadSectionId') or 0) or None
            except ValueError: wvk = None
            try: frac = float(row['fraction']) if (row.get('fraction') or '').strip() else None
            except ValueError: frac = None
            d = (row.get('drivingDirection') or '').strip().upper()
            txt = row.get('textSigns') or ''
            try: brg = float(row['bearing']) if (row.get('bearing') or '').strip() else None
            except ValueError: brg = None
            out.append({'lat': lat, 'lon': lon, 'k': k, 'v': round(v, 2), 'wvk': wvk, 'frac': frac, 'brg': brg,
                        'dir': d if d in ('H', 'T') else '', 'voor': bool(VOOR.search(txt)),
                        'flag': 1 if (EXEMPT.search(txt) or WARN_ONLY.search(txt)) else 0})
            ls = row.get('lastSeenOn') or ''
            if ls > newest: newest = ls
    # Vooraankondiging ('VOOR(na 150 m)'): de beperking zelf staat verderop en heeft een eigen bord. Weglaten,
    # ook het kale bord op dezelfde plek (zelfde bord, onderbord apart geregistreerd).
    voor = [s for s in out if s['voor']]
    cell = {}
    for s in voor: cell.setdefault((round(s['lat'], 3), round(s['lon'], 3)), []).append(s)
    def twin(s):
        for dl in (-0.001, 0, 0.001):
            for dn in (-0.001, 0, 0.001):
                for t in cell.get((round(s['lat'] + dl, 3), round(s['lon'] + dn, 3)), ()):
                    if t['k'] == s['k'] and t['v'] == s['v'] and abs(t['lat'] - s['lat']) * 110540 < 6 and abs(t['lon'] - s['lon']) * 68000 < 6:
                        return True
        return False
    kept = [s for s in out if not s['voor'] and not twin(s)]
    print('ndw: vooraankondiging weggelaten', len(out) - len(kept), file=sys.stderr)
    return kept, newest or None

def main():
    pbf, signs, dst = sys.argv[1:4]
    gpkg = sys.argv[4] if len(sys.argv) > 4 else None
    pts, osm_ts = osm_nodes(pbf)
    pts = [p[:7] for p in pts]
    ndw_ts = None
    nwb = None
    stats = {}
    if signs != '-':
        import os, ndw_match
        raw, ndw_ts = ndw_signs(signs)
        idx = os.path.join(os.path.dirname(os.path.abspath(dst)), 'nodes.idx')
        mapped, stats = ndw_match.match(raw, pbf, gpkg, idx)
        try: os.remove(idx)
        except OSError: pass
        cfg = os.environ.get('VALHALLA_CONFIG')
        if cfg:
            geo = [None if m is None else [m[0], m[1], m[2], m[3]] for m in mapped]
            stats['graph_snapped'], stats['graph_dropped'], stats['graph_unsure'] = ndw_match.refine_on_graph(geo, cfg)
            mapped = [None if g is None else (g[0], g[1], g[2], g[3], m[4]) for g, m in zip(geo, mapped)]
        for s, m in zip(raw, mapped):
            if m is None: continue
            lat, lon, hdg, flags, _way = m
            pts.append([lat, lon, s['k'], s['v'], hdg, 'n', flags | s['flag']])
        if gpkg:
            nwb = datetime.datetime.fromtimestamp(os.path.getmtime(gpkg), datetime.timezone.utc).strftime('%Y-%m-%d')
    pts.sort(key=lambda p: (p[0], p[1]))
    # Zelfde bord twee keer (dubbel in NDW of OSM-knoop + NDW-bord op dezelfde plek): één houden.
    seen, uniq = set(), []
    for p in pts:
        key = (round(p[0], 5), round(p[1], 5), p[2], p[3], p[4])
        if key in seen: continue
        seen.add(key); uniq.append(p)
    pts = uniq
    doc = {'v': 2, 'built': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'osm': osm_ts, 'ndw': ndw_ts, 'nwb': nwb, 'p': pts}
    with open(dst, 'w') as f:
        json.dump(doc, f, separators=(',', ':'))
    by = {}
    for p in pts: by[p[5] + p[2]] = by.get(p[5] + p[2], 0) + 1
    print('restrictions', len(pts), json.dumps(by, sort_keys=True), 'match', json.dumps(stats), file=sys.stderr)

if __name__ == '__main__':
    main()
