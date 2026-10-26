#!/usr/bin/env python3
"""manifest.json voor de app: zie lib/truck/pack.ts (parseManifest) voor de lezer. Gebruik: manifest.py <outdir> <tiles.tar> <pbf> <valhalla>"""
import hashlib, json, os, sys, datetime

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

out, tar, pbf, vv = sys.argv[1:5]
import osmium
r = osmium.io.Reader(pbf, osmium.osm.osm_entity_bits.NOTHING)
osm_ts = r.header().get('osmosis_replication_timestamp') or None
r.close()
now = datetime.datetime.now(datetime.timezone.utc)
gz = os.path.join(out, 'nl-truck-tiles.tar.gz')
aux = os.path.join(out, 'restrictions.json')
doc = {
    'format': 1,
    'region': 'NL',
    'version': now.strftime('%Y-%m-%d'),
    'builtAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
    'osmTimestamp': osm_ts,
    'valhalla': vv,
    'attribution': '© OpenStreetMap-bijdragers (ODbL) · NDW open data',
    **({'baseUrl': os.environ['FILE_BASE_URL']} if os.environ.get('FILE_BASE_URL') else {}),
    'files': [
        {'name': 'nl-truck-tiles.tar.gz', 'kind': 'tiles', 'size': os.path.getsize(gz), 'sha256': sha(gz),
         'unpackedName': 'tiles.tar', 'unpackedSize': os.path.getsize(tar), 'unpackedSha256': sha(tar)},
        {'name': 'restrictions.json', 'kind': 'restrictions', 'size': os.path.getsize(aux), 'sha256': sha(aux)},
    ],
}
print(json.dumps(doc, indent=2))
