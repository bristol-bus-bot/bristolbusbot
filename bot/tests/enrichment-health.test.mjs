import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';


test('health reports the exact fleet and locality bytes loaded at startup',
    async () => {
        const root = await mkdtemp(join(tmpdir(), 'bbb-enrichment-health-'));
        try {
            const fleetPath = join(root, 'fbribuses.json');
            const localityPath = join(root, 'stop_localities.json');
            const fleet = JSON.stringify([{
                id: 1,
                slug: 'fixture-bus',
                fleet_code: '36001',
                reg: 'YX26AAA',
                withdrawn: false,
                operator: { id: 'FBRI', slug: 'fbri', name: 'First' },
            }]);
            const localities = JSON.stringify({
                '0100A': {
                    stop_code: '0100A',
                    stop_name: 'Fixture Stop',
                    ward_name: 'Central',
                    ward_code: 'E0001',
                    area: 'Bristol',
                    lat: 51.45,
                    lon: -2.59,
                },
            });
            await writeFile(fleetPath, fleet);
            await writeFile(localityPath, localities);
            process.env.BBB_FLEET_JSON = fleetPath;
            process.env.BBB_LOCALITIES_JSON = localityPath;

            const { ApplicationState } = await import(
                '../dist/services/application-state.js');
            const { DatabaseManager } = await import(
                '../dist/services/database-manager.js');
            const { AICommentary } = await import(
                '../dist/services/ai-commentary.js');
            const { HealthMonitor } = await import(
                '../dist/services/health-monitor.js');
            const state = ApplicationState.getInstance();
            const manager = new DatabaseManager({
                timetablePath: '', appDataPath: '', maxConnections: 1,
            });
            await manager.loadBusDetailsLookup();
            new AICommentary({
                editorialContextPath: join(root, 'missing-editorial.json'),
                editorialUsagePath: join(root, 'editorial-usage.json'),
                model: 'disabled',
                dailyLimit: 0,
                timeout: 1,
            }, state, {});

            const health = new HealthMonitor(state).getHealthStatus();
            const reported = health.application.enrichmentData;

            assert.deepEqual(reported.fleet, {
                loaded: true,
                path: fleetPath,
                sha256: createHash('sha256').update(fleet).digest('hex'),
                records: 1,
            });
            assert.deepEqual(reported.localities, {
                loaded: true,
                path: localityPath,
                sha256: createHash('sha256').update(localities).digest('hex'),
                records: 1,
                error: undefined,
            });
        } finally {
            delete process.env.BBB_FLEET_JSON;
            delete process.env.BBB_LOCALITIES_JSON;
            await rm(root, { recursive: true, force: true });
        }
    });
