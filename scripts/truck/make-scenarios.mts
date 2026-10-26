/**
 * Bouwt test/fixtures/truck-scenarios.json: echte NL-situaties waarin een AUTO wél door de beperking rijdt
 * (de beperking ligt dus op de logische route) en de vrachtwagen eromheen moet. Kandidaten komen uit OSM
 * (scenario_candidates.py), NDW-borden/OSM-knopen (restrictions.json) en de opgeslagen NDW-afsluitingen.
 * Gebruik (lokale router draait, zie local-service.mjs):
 *   node --import tsx scripts/truck/make-scenarios.mts <candidates.json> <restrictions.json>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'fflate';
import { parseDatex } from '../../lib/traffic/datex.ts';
import { bearing, dist, indexRoute, nearRoute, angleDiff } from '../../lib/truck/geo.ts';
import { decodePolyline6 } from '../../lib/truck/valhalla.ts';

const URL = process.env.VALHALLA_URL ?? 'http://127.0.0.1:8002';
const [candFile, auxFile] = process.argv.slice(2);
type LL = { lat: number; lon: number };
const post = async (path: string, body: unknown) => (await fetch(`${URL}${path}`, { method: 'POST', body: JSON.stringify(body) })).json() as Promise<any>;

function move(p: LL, brg: number, m: number): LL {
  const r = 6371000, d = m / r, b = (brg * Math.PI) / 180, la = (p.lat * Math.PI) / 180, lo = (p.lon * Math.PI) / 180;
  const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b));
  const lo2 = lo + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2));
  return { lat: +((la2 * 180) / Math.PI).toFixed(6), lon: +((lo2 * 180) / Math.PI).toFixed(6) };
}
function midOf(c: [number, number][]): { p: LL; heading: number } {
  const pts = c.map(([lat, lon]) => ({ lat, lon }));
  let tot = 0; for (let i = 1; i < pts.length; i++) tot += dist(pts[i - 1], pts[i]);
  let acc = 0;
  for (let i = 1; i < pts.length; i++) {
    const d = dist(pts[i - 1], pts[i]);
    if (acc + d >= tot / 2) { const t = (tot / 2 - acc) / (d || 1); return { p: { lat: +(pts[i - 1].lat + (pts[i].lat - pts[i - 1].lat) * t).toFixed(6), lon: +(pts[i - 1].lon + (pts[i].lon - pts[i - 1].lon) * t).toFixed(6) }, heading: bearing(pts[i - 1], pts[i]) }; }
    acc += d;
  }
  return { p: pts[0], heading: 0 };
}
async function routeCoords(costing: string, a: LL, b: LL, opts: Record<string, unknown> = {}): Promise<[number, number][] | null> {
  const r = await post('/route', { locations: [a, b], costing, costing_options: { [costing]: opts }, shape_format: 'polyline6' });
  if (!r.trip) return null;
  return r.trip.legs.flatMap((l: any) => decodePolyline6(l.shape));
}
function passes(c: [number, number][] | null, p: LL, maxM: number, heading?: number): boolean {
  if (!c) return false;
  const n = nearRoute(indexRoute(c), p, maxM);
  return !!n && (heading == null || angleDiff(heading, n.heading) <= 50 || angleDiff(heading, n.heading) >= 130);
}
const rank = (id: number) => (Math.imul(id, 2654435761) >>> 0);
const TRUCK = { heightM: 4.0, widthM: 2.55, lengthM: 16.5, weightT: 40, axleLoadT: 11.5, hazmat: false };
const costingOf = (v: typeof TRUCK) => ({ height: v.heightM, width: v.widthM, length: v.lengthM, weight: v.weightT, axle_load: v.axleLoadT, hazmat: v.hazmat });

const out: any[] = [];
const cands = JSON.parse(readFileSync(candFile, 'utf8')) as any[];
const WANT: Record<string, { n: number; vehicles: (typeof TRUCK)[]; label: (c: any) => string }> = {
  height: { n: 7, vehicles: [TRUCK], label: (c) => `Lage doorgang ${c.value} m (OSM maxheight)` },
  weight: { n: 4, vehicles: [TRUCK], label: (c) => `Gewichtsbeperking ${c.value} t (OSM maxweight), 40 t` },
  'weight-heavy': { n: 4, vehicles: [{ ...TRUCK, weightT: 55 }, { ...TRUCK, weightT: 60 }], label: (c) => `Maximaal ${c.value} t met zwaar transport` },
  axle: { n: 2, vehicles: [TRUCK], label: (c) => `Aslast maximaal ${c.value} t (OSM maxaxleload)` },
  hgv: { n: 5, vehicles: [TRUCK], label: () => 'Vrachtwagenverbod (OSM hgv=no)' },
};
for (const [kind, w] of Object.entries(WANT)) {
  const list = cands.filter((c) => c.kind === kind && c.name).sort((a, b) => rank(a.way) - rank(b.way));
  let got = 0, tried = 0;
  for (const c of list) {
    if (got >= w.n || tried > 400) break;
    tried++;
    const coords = c.coords as [number, number][];
    if (coords.length < 2) continue;
    const first = { lat: coords[0][0], lon: coords[0][1] }, second = { lat: coords[1][0], lon: coords[1][1] };
    const last = { lat: coords[coords.length - 1][0], lon: coords[coords.length - 1][1] }, prev = { lat: coords[coords.length - 2][0], lon: coords[coords.length - 2][1] };
    const from = move(first, bearing(second, first), 300), to = move(last, bearing(prev, last), 300);
    const { p: mid } = midOf(coords);
    const car = await routeCoords('auto', from, to);
    if (!passes(car, mid, 8)) continue;
    const v = w.vehicles[got % w.vehicles.length];
    const truck = await routeCoords('truck', from, to, costingOf(v));
    if (!truck || passes(truck, mid, 8)) continue;
    if (kind === 'weight-heavy') { // 40 t mag er wel langs: bewijst dat het om het gewicht gaat
      const t40 = await routeCoords('truck', from, to, costingOf(TRUCK));
      if (!passes(t40, mid, 8)) continue;
    }
    got++;
    out.push({ id: `${kind}-${c.way}`, kind, label: `${w.label(c)}: ${c.name}`, from, to, avoid: mid, avoidM: 8, vehicle: v, osmWay: c.way, value: c.value });
  }
  console.error(kind, got, 'van', tried);
}

// NDW-borden (C19) en OSM-knopen met maxheight: het 'gat' dat Valhalla zelf niet ziet.
const aux = JSON.parse(readFileSync(auxFile, 'utf8')) as { v: number; p: any[] };
if (aux.v !== 2) throw new Error('restrictions.json v2 nodig');
for (const [src, n, label] of [['n', 5, 'NDW-bord C19'], ['o', 3, 'OSM-hoogteportaal (knoop)']] as const) {
  const pts = aux.p.map((p, i) => ({ p, i })).filter(({ p }) => p[2] === 'h' && p[5] === src && p[3] < 3.8 && p[6] === 0).sort((a, b) => rank(a.i) - rank(b.i));
  let got = 0, tried = 0;
  for (const { p } of pts) {
    if (got >= n || tried > 500) break;
    tried++;
    const at = { lat: p[0], lon: p[1] };
    const loc = await post('/locate', { locations: [at], costing: 'auto', verbose: true });
    const edges = (loc?.[0]?.edges ?? []) as any[];
    // Beide liggen al op de OSM-weg (NDW-borden via het NWB-wegvak gekoppeld bij de tegelbouw).
    const e = edges.find((x) => x.distance < 3);
    if (!e || (e.edge_info?.names ?? []).some((nm: string) => /park|garage/i.test(nm))) continue;
    const street: string = (e.edge_info?.names ?? [])[0] ?? '';
    const on = at;
    const tol = 2;
    const hdg = p[4] >= 1000 ? p[4] - 1000 : p[4] >= 0 ? p[4] : e.heading;
    const from = move(on, (hdg + 180) % 360, 300), to = move(on, hdg, 300);
    const car = await routeCoords('auto', from, to);
    if (!passes(car, on, tol)) continue;
    const truck = await routeCoords('truck', from, to, costingOf(TRUCK));
    if (!truck) continue;
    const gap = passes(truck, on, tol);
    if (!gap) continue;
    // Beperking niet vlak bij vertrek/bestemming (dan is hij terecht niet te omzeilen).
    const ends = [truck[0], truck[truck.length - 1]].map(([lat, lon]) => dist({ lat, lon }, on));
    if (Math.min(...ends) < 250) continue; // alleen echte gaten: Valhalla alleen zou er WEL langs gaan
    // Is er een vrachtwagenroute zonder dit punt? Zo niet: de app moet hier een conflict-waarschuwing geven.
    const alt = await post('/route', { locations: [from, to], costing: 'truck', costing_options: { truck: costingOf(TRUCK) }, exclude_locations: [on] });
    const expect = alt.trip ? 'avoid' : 'conflict';
    got++;
    out.push({ expect, id: `aux-${src}-${p[0]},${p[1]}`, kind: src === 'n' ? 'ndw-c19' : 'osm-node', label: `${label} ${String(p[3]).replace('.', ',')} m${street ? `: ${street}` : ''}${expect === 'conflict' ? ' (geen alternatief: waarschuwen)' : ''}`, from, to, avoid: on, avoidM: src === 'o' ? 2.5 : 4, vehicle: TRUCK, value: p[3], needsAux: true });
  }
  console.error(src, got, 'van', tried);
}

// Afsluitingen uit de opgeslagen NDW-feed (tijdelijke verkeersmaatregelen, 20-10-2026 23:48).
const xml = new TextDecoder().decode(gunzipSync(readFileSync('test/fixtures/ndw-afsluitingen-2026-10-20.xml.gz')));
const NOW = Date.parse('2026-10-20T21:48:00Z');
const evs = parseDatex(xml, { source: 'NDW', now: NOW }).filter((e) => e.kind === 'closure' && e.points.length > 1 && e.minHeightM == null);
let got = 0;
const usedSpots = new Set<string>();
for (const e of evs.sort((a, b) => rank(a.id.length * 7919 + a.id.charCodeAt(a.id.length - 1)) - rank(b.id.length * 7919 + b.id.charCodeAt(b.id.length - 1)))) {
  if (got >= 5) break;
  const line = e.points;
  let len = 0; for (let i = 1; i < line.length; i++) len += dist(line[i - 1], line[i]);
  if (len < 150 || len > 2500) continue;
  const from = move(line[0], bearing(line[1], line[0]), 250), to = move(line[line.length - 1], bearing(line[line.length - 2], line[line.length - 1]), 250);
  const { p: mid, heading } = midOf(line.map((x) => [x.lat, x.lon]));
  const truck = await routeCoords('truck', from, to, costingOf(TRUCK));
  if (!truck) continue;
  const n = nearRoute(indexRoute(truck), mid, 15);
  if (!n || angleDiff(n.heading, heading) > 50) continue;
  const spot = `${mid.lat.toFixed(2)},${mid.lon.toFixed(2)}`;
  if (usedSpots.has(spot)) continue;
  usedSpots.add(spot);
  got++;
  out.push({ id: `closure-${e.id}`, kind: 'closure', label: `${e.title} (NDW ${e.id.slice(0, 24)})`, from, to, avoid: mid, avoidHeading: Math.round(heading), avoidM: 12, vehicle: TRUCK, eventId: e.id, now: NOW });
}
console.error('closure', got);
writeFileSync('test/fixtures/truck-scenarios.json', JSON.stringify(out, null, 1));
console.error('totaal', out.length);
