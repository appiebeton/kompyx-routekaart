// Start valhalla_service (pyvalhalla 3.9.1) met precies de config die de app op het toestel gebruikt (lib/truck/valhalla.ts truckConfig),
// alleen aangevuld met httpd. Voor de regressietests: VALHALLA_URL=http://127.0.0.1:8002 npm test
// Gebruik: node --import tsx scripts/truck/local-service.mjs <tiles.tar> [venv-bin-dir] [poort]
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { truckConfig } from '../../lib/truck/valhalla.ts';

const [tar, bin = '.truck-build/venv/bin', port = '8002'] = process.argv.slice(2);
if (!tar) { console.error('gebruik: local-service.mjs <tiles.tar> [venv-bin] [poort]'); process.exit(2); }
const c = truckConfig(tar);
c.httpd = { service: { listen: `tcp://127.0.0.1:${port}`, loopback: 'ipc:///tmp/kompyx-truck-loopback', interrupt: 'ipc:///tmp/kompyx-truck-interrupt', drain_seconds: 1, shutdown_seconds: 1, timeout_seconds: -1 } };
const cfg = `/tmp/kompyx-truck-valhalla-${port}.json`;
writeFileSync(cfg, JSON.stringify(c));
const p = spawn(`${bin}/valhalla_service`, [cfg, '2'], { stdio: 'inherit', env: { ...process.env, PATH: `${bin}:${process.env.PATH}` } });
p.on('exit', (code) => process.exit(code ?? 0));
