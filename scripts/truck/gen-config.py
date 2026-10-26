#!/usr/bin/env python3
"""Maakt lib/truck/valhallaConfig.ts uit de valhalla_build_config-uitvoer van pyvalhalla (zelfde versie als in de app).
Alleen de onderdelen die de router op het toestel nodig heeft; paden worden in de app ingevuld (truckConfig()).
Gebruik: gen-config.py <valhalla.json> > lib/truck/valhallaConfig.ts"""
import json, sys
c = json.load(open(sys.argv[1]))
keep = {k: c[k] for k in ('loki', 'thor', 'odin', 'meili', 'service_limits', 'additional_data') if k in c}
keep['additional_data'] = {'elevation': ''}
keep['meili'].setdefault('logging', {})
keep['meili']['logging'] = {'long_request': 5000.0}
m = c['mjolnir']
keep['mjolnir'] = {k: m[k] for k in ('max_cache_size', 'use_lru_mem_cache', 'lru_mem_cache_hard_control', 'use_simple_mem_cache', 'global_synchronized_cache', 'tile_url', 'tile_url_gz', 'concurrency', 'logging') if k in m}
keep['mjolnir'].update({'tile_dir': '', 'tile_extract': '', 'traffic_extract': '', 'max_cache_size': 268435456, 'concurrency': 1})
keep['loki']['actions'] = ['route', 'locate', 'status']
keep['thor']['logging'] = {'long_request': 5000.0}
keep['loki']['logging'] = {'long_request': 5000.0}
keep['odin']['logging'] = {'long_request': 5000.0}
# Meer uitsluitingen dan de serverstandaard (50 punten / 10 km omtrek): afsluitingen + borden langs een hele rit.
keep['service_limits']['max_exclude_locations'] = 5000
keep['service_limits']['max_exclude_polygons_length'] = 200000
keep['logging'] = {'type': 'std_out', 'color': False}
print('/* Gegenereerd door scripts/truck/gen-config.py uit pyvalhalla valhalla_build_config (Valhalla 3.9.1). Niet met de hand wijzigen. */')
print('export const VALHALLA_BASE_CONFIG = ' + json.dumps(keep, indent=1, sort_keys=True) + ' as const;')
