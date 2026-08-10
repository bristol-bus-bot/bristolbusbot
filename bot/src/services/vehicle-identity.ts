/** Operator-scoped lookup for fleet records whose public codes can collide. */

export interface FleetIdentityIndex {
    results: any[];
    scoped: Map<string, any[]>;
    registrationsScoped: Map<string, any>;
    byCode: Map<string, any[]>;
    codeOwners: Map<string, Set<string>>;
}

function text(value: unknown): string {
    return String(value ?? '').trim();
}

function operator(record: any): string {
    const value = record?.operator;
    return text(typeof value === 'object' ? value?.id : value).toUpperCase();
}

function code(record: any): string {
    return text(record?.fleet_code ?? record?.fleet_number);
}

function registration(value: unknown): string {
    return text(value).toUpperCase().replace(/[^A-Z0-9]+/g, '');
}

function registrationFor(record: any): string {
    return registration(record?.reg ?? record?.registration);
}

function key(...parts: string[]): string {
    return parts.join('\0');
}

function preferred(records: any[]): any | null {
    const active = records.filter(record => !record?.withdrawn);
    const candidates = active.length ? active : records;
    const registrations = new Set(
        candidates.map(registrationFor).filter(Boolean),
    );
    return candidates.length === 1 || registrations.size === 1
        ? candidates[candidates.length - 1]
        : null;
}

function possibleCodes(vehicleRef: string): string[] {
    const raw = text(vehicleRef);
    if (!raw) return [];
    const values = [raw];
    if (raw.includes('-')) values.push(raw.slice(raw.lastIndexOf('-') + 1));
    return [...new Set(values.filter(Boolean))];
}

export function emptyFleetIdentityIndex(): FleetIdentityIndex {
    return {
        results: [],
        scoped: new Map(),
        registrationsScoped: new Map(),
        byCode: new Map(),
        codeOwners: new Map(),
    };
}

export function buildFleetIdentityIndex(records: any[]): FleetIdentityIndex {
    const index = emptyFleetIdentityIndex();
    index.results = Array.isArray(records) ? records.filter(
        record => record && typeof record === 'object') : [];
    for (const record of index.results) {
        const noc = operator(record);
        const fleetCode = code(record);
        const reg = registrationFor(record);
        if (fleetCode) {
            if (!index.byCode.has(fleetCode)) index.byCode.set(fleetCode, []);
            index.byCode.get(fleetCode)!.push(record);
            if (noc) {
                const scopedKey = key(noc, fleetCode);
                if (!index.scoped.has(scopedKey)) index.scoped.set(scopedKey, []);
                index.scoped.get(scopedKey)!.push(record);
                if (!record?.withdrawn) {
                    if (!index.codeOwners.has(fleetCode))
                        index.codeOwners.set(fleetCode, new Set());
                    index.codeOwners.get(fleetCode)!.add(noc);
                }
            }
        }
        if (noc && reg) index.registrationsScoped.set(key(noc, reg), record);
    }
    return index;
}

export function resolveFleetVehicle(
    index: FleetIdentityIndex | null | undefined,
    vehicleRef: string,
    operatorRef = '',
): any | null {
    if (!index || !vehicleRef) return null;
    const noc = text(operatorRef).toUpperCase();
    if (noc) {
        // Some SIRI feeds prefix registrations with the operator code.
        // Resolve both the complete reference and its suffix as registrations.
        for (const value of possibleCodes(vehicleRef)) {
            const direct = index.registrationsScoped.get(
                key(noc, registration(value)));
            if (direct) return direct;
        }
    }
    for (const fleetCode of possibleCodes(vehicleRef)) {
        if (!noc) break;
        const records = index.scoped.get(key(noc, fleetCode));
        if (records) return preferred(records);
    }
    for (const fleetCode of possibleCodes(vehicleRef)) {
        const records = index.byCode.get(fleetCode) || [];
        const record = preferred(records);
        const owners = index.codeOwners.get(fleetCode) || new Set<string>();
        if (record && owners.size <= 1
                && (!noc || owners.size === 0 || owners.has(noc))) {
            return record;
        }
    }
    return null;
}
