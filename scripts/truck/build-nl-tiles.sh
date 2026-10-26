#!/usr/bin/env bash
# Reproduceerbare bouw van de Nederlandse vrachtwagen-routetegels voor KOMPYX (on-device Valhalla).
#
# Uitvoer in $OUT (standaard dist/truck-nl):
#   nl-truck-tiles.tar.gz   Valhalla tile-extract (tar), gzip voor de download; de app pakt hem uit naar tiles.tar
#   restrictions.json       knoop-hoogtes (OSM) + NDW-borden C7/C17-C21, via NWB-wegvak op OSM-weg gelegd (zie build_aux.py)
#   manifest.json           versie, datum, groottes en sha256 per bestand (de app controleert deze)
#   SHA256SUMS
#
# Vastgepind: pyvalhalla 3.9.1 = dezelfde Valhalla als Rallista/valhalla-mobile 0.6.4 in de app (tegelformaat gelijk).
# Vereist: python3 (venv), curl, unzip, spatialite-bin (voor de tijdzone-database), ~6 GB vrije schijf, ~4 GB RAM.
# Gebruik: scripts/truck/build-nl-tiles.sh            (WORK=.truck-build OUT=dist/truck-nl REUSE_PBF=1 om opnieuw te gebruiken)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORK:-$PWD/.truck-build}"
OUT="${OUT:-$PWD/dist/truck-nl}"
PBF_URL="${PBF_URL:-https://download.geofabrik.de/europe/netherlands-latest.osm.pbf}"
SIGNS_URL="${SIGNS_URL:-https://opendata.ndw.nu/verkeersborden_actueel_beeld.csv.gz}"
VALHALLA_VERSION=3.9.1
OSMIUM_VERSION=4.3.2
mkdir -p "$WORK" "$OUT"
cd "$WORK"
log() { echo "[$(date -u +%H:%M:%S)] $*" >&2; }

if [ ! -x venv/bin/valhalla_build_tiles ]; then
  log "venv + pyvalhalla $VALHALLA_VERSION"
  python3 -m venv venv
  venv/bin/pip install -q "pyvalhalla==$VALHALLA_VERSION" "osmium==$OSMIUM_VERSION"
fi
export PATH="$WORK/venv/bin:$PATH"

if [ "${REUSE_PBF:-0}" != 1 ] || [ ! -s nl.osm.pbf ]; then
  log "download $PBF_URL"
  curl -fsSL -o nl.osm.pbf.part "$PBF_URL"
  curl -fsSL -o nl.osm.pbf.md5 "$PBF_URL.md5"
  echo "$(cut -d' ' -f1 nl.osm.pbf.md5)  nl.osm.pbf.part" | md5sum -c - >&2
  mv nl.osm.pbf.part nl.osm.pbf
fi
if [ "${REUSE_SIGNS:-0}" != 1 ] || [ ! -s signs.csv.gz ]; then
  log "download NDW verkeersborden"
  curl -fsSL -o signs.csv.gz.part "$SIGNS_URL" && mv signs.csv.gz.part signs.csv.gz
fi

NWB_URL="${NWB_URL:-https://service.pdok.nl/rws/nationaal-wegenbestand-wegen/atom/downloads/nwb_wegen.gpkg}"
if [ "${REUSE_NWB:-0}" != 1 ] || [ ! -s nwb_wegen.gpkg ]; then
  log "download NWB wegen (Rijkswaterstaat, CC0) voor het koppelen van NDW-borden"
  curl -fsSL -C - -o nwb_wegen.gpkg.part "$NWB_URL" && mv nwb_wegen.gpkg.part nwb_wegen.gpkg
fi

rm -rf tiles && mkdir -p tiles
valhalla_build_config \
  --mjolnir-tile-dir "$WORK/tiles" --mjolnir-tile-extract "$WORK/tiles.tar" \
  --mjolnir-admin "$WORK/admins.sqlite" --mjolnir-timezone "$WORK/tz.sqlite" \
  --mjolnir-concurrency "$(nproc)" > valhalla.json

if [ ! -s tz.sqlite ]; then
  log "tijdzone-database (valhalla_build_timezones $VALHALLA_VERSION)"
  curl -fsSL -o build_tz.sh "https://raw.githubusercontent.com/valhalla/valhalla/$VALHALLA_VERSION/scripts/valhalla_build_timezones"
  sh build_tz.sh > tz.sqlite.part && mv tz.sqlite.part tz.sqlite
fi
log "admin-database"
rm -f admins.sqlite
valhalla_build_admins -c valhalla.json nl.osm.pbf > admins.log 2>&1
log "tegels"
valhalla_build_tiles -c valhalla.json nl.osm.pbf > tiles.log 2>&1
log "extract"
valhalla_build_extract -c valhalla.json -v --overwrite > extract.log 2>&1
log "restrictions.json"
VALHALLA_CONFIG="$WORK/valhalla.json" python3 "$HERE/build_aux.py" nl.osm.pbf signs.csv.gz restrictions.json nwb_wegen.gpkg

log "inpakken"
gzip -6 -c tiles.tar > "$OUT/nl-truck-tiles.tar.gz"
cp restrictions.json "$OUT/restrictions.json"
python3 "$HERE/manifest.py" "$OUT" "$WORK/tiles.tar" nl.osm.pbf "$VALHALLA_VERSION" > "$OUT/manifest.json"
( cd "$OUT" && sha256sum nl-truck-tiles.tar.gz restrictions.json manifest.json > SHA256SUMS )
log "klaar: $OUT"; cat "$OUT/manifest.json" >&2
