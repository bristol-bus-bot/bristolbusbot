import * as path from 'path';


export interface BotDataPaths {
    fleet: string;
    stopLocalities: string;
    stopEnrichment: string;
    localFlavour: string;
    routeDetails: string;
}

function configuredPath(
    environment: NodeJS.ProcessEnv,
    name: string,
    fallback: string,
): string {
    const value = environment[name]?.trim();
    return value || fallback;
}

/** Resolve immutable-release fallbacks and production durable overrides. */
export function resolveBotDataPaths(
    environment: NodeJS.ProcessEnv = process.env,
    workingDirectory = process.cwd(),
): BotDataPaths {
    return {
        fleet: configuredPath(
            environment, 'BBB_FLEET_JSON',
            path.join(workingDirectory, 'fbribuses.json')),
        stopLocalities: configuredPath(
            environment, 'BBB_LOCALITIES_JSON',
            path.join(workingDirectory, 'stop_localities.json')),
        stopEnrichment: configuredPath(
            environment, 'BBB_ENRICHMENT_JSON',
            path.join(workingDirectory, 'stop_enrichment.json')),
        localFlavour: configuredPath(
            environment, 'BBB_LOCAL_FLAVOUR_JSON',
            path.join(workingDirectory, 'local_flavour.json')),
        routeDetails: configuredPath(
            environment, 'BBB_ROUTE_DETAILS_JSON',
            path.join(workingDirectory, 'route_details.json')),
    };
}

export const BOT_DATA_PATHS = resolveBotDataPaths();
