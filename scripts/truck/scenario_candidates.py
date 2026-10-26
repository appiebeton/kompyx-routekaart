#!/usr/bin/env python3
"""Kandidaten voor de regressietests (test/fixtures/truck-scenarios.json): echte NL-wegen met vrachtwagenbeperkingen uit OSM.
Twee passes (geen node-index voor heel NL nodig). Uitvoer: JSON-lijst met per weg: soort, waarde, naam, plaats-hint, coördinaten.
Daarna filtert scripts/truck/make-scenarios.mts met de lokale router welke kandidaten zinvol zijn (kleine wagen rijdt er wél langs).
Gebruik: scenario_candidates.py <nl.osm.pbf> <out.json>"""
import json, re, sys, osmium

ROADS = {'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'primary_link', 'secondary_link', 'tertiary_link', 'living_street', 'service'}
def num(v):
    m = re.match(r'^\s*([0-9]*[.,]?[0-9]+)\s*(m|t)?\s*$', v or '')
    return float(m.group(1).replace(',', '.')) if m else None

cand = []
class W(osmium.SimpleHandler):
    def way(self, w):
        t = w.tags
        hw = t.get('highway')
        if hw not in ROADS or t.get('access') in ('no', 'private') or t.get('area') == 'yes': return
        kind = None
        h, wt, ax = num(t.get('maxheight')), num(t.get('maxweight')), num(t.get('maxaxleload'))
        if h and 2.0 <= h <= 3.7: kind, val = 'height', h
        elif wt and 5 <= wt <= 30: kind, val = 'weight', wt
        elif wt and 44 <= wt < 55: kind, val = 'weight-heavy', wt
        elif ax and 3 <= ax <= 9: kind, val = 'axle', ax
        elif t.get('hgv') == 'no' and hw in ('residential', 'tertiary', 'unclassified', 'secondary', 'living_street'): kind, val = 'hgv', 0
        if not kind or len(w.nodes) < 2: return
        cand.append({'way': w.id, 'kind': kind, 'value': val, 'name': t.get('name', ''), 'highway': hw, 'oneway': t.get('oneway', ''),
                     'bridge': t.get('bridge', ''), 'tunnel': t.get('tunnel', ''), 'nodes': [n.ref for n in w.nodes]})
W().apply_file(sys.argv[1])
need = set(n for c in cand for n in c['nodes'])
loc = {}
class N(osmium.SimpleHandler):
    def node(self, n):
        if n.id in need: loc[n.id] = (round(n.location.lat, 6), round(n.location.lon, 6))
N().apply_file(sys.argv[1])
out = []
for c in cand:
    pts = [loc[n] for n in c.pop('nodes') if n in loc]
    if len(pts) >= 2: c['coords'] = pts; out.append(c)
json.dump(out, open(sys.argv[2], 'w'))
from collections import Counter
print(len(out), Counter(c['kind'] for c in out), file=sys.stderr)
