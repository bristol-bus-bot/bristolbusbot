import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';

import { resolveBotDataPaths } from '../dist/config/data-paths.js';


test('all five enrichment inputs accept explicit durable path overrides', () => {
    const paths = resolveBotDataPaths({
        BBB_FLEET_JSON: '/durable/fleet.json',
        BBB_LOCALITIES_JSON: '/durable/localities.json',
        BBB_ENRICHMENT_JSON: '/durable/stops.json',
        BBB_LOCAL_FLAVOUR_JSON: '/durable/flavour.json',
        BBB_ROUTE_DETAILS_JSON: '/durable/routes.json',
    }, '/release');

    assert.deepEqual(paths, {
        fleet: '/durable/fleet.json',
        stopLocalities: '/durable/localities.json',
        stopEnrichment: '/durable/stops.json',
        localFlavour: '/durable/flavour.json',
        routeDetails: '/durable/routes.json',
    });
});


test('development keeps backward-compatible working-directory defaults', () => {
    const paths = resolveBotDataPaths({}, '/release');

    assert.deepEqual(paths, {
        fleet: path.join('/release', 'fbribuses.json'),
        stopLocalities: path.join('/release', 'stop_localities.json'),
        stopEnrichment: path.join('/release', 'stop_enrichment.json'),
        localFlavour: path.join('/release', 'local_flavour.json'),
        routeDetails: path.join('/release', 'route_details.json'),
    });
});
