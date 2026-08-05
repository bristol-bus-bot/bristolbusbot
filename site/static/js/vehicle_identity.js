/** Operator-safe policy for legacy and future vehicle description keys. */

function text(value) {
    return String(value ?? "").trim();
}

function registration(value) {
    return text(value).toUpperCase().replace(/[^A-Z0-9]+/g, "");
}

export function buildFleetLookups(fleet) {
    const codeGroups = new Map();
    const scopedRegistrations = {};
    const registrationGroups = new Map();
    for (const vehicle of Array.isArray(fleet) ? fleet : []) {
        const operator = text(vehicle?.operator_id).toUpperCase();
        const code = text(vehicle?.fleet_code || vehicle?.fleet_number);
        const reg = registration(vehicle?.reg);
        if (operator && code) {
            const key = `${operator}:${code}`;
            if (!codeGroups.has(key)) codeGroups.set(key, []);
            codeGroups.get(key).push(vehicle);
        }
        if (operator && reg) scopedRegistrations[`${operator}:${reg}`] = vehicle;
        if (reg) {
            if (!registrationGroups.has(reg)) registrationGroups.set(reg, []);
            registrationGroups.get(reg).push(vehicle);
        }
    }
    const byOperatorCode = {};
    for (const [key, records] of codeGroups) {
        const active = records.filter(vehicle => !vehicle?.withdrawn);
        const candidates = active.length ? active : records;
        const registrations = new Set(
            candidates.map(vehicle => registration(vehicle?.reg)).filter(Boolean),
        );
        if (candidates.length === 1 || registrations.size === 1)
            byOperatorCode[key] = candidates[candidates.length - 1];
    }
    const byUniqueRegistration = {};
    for (const [reg, records] of registrationGroups) {
        const identities = new Set(records.map(vehicle =>
            `${text(vehicle?.operator_id).toUpperCase()}\0${text(
                vehicle?.fleet_code || vehicle?.fleet_number)}`));
        if (identities.size === 1)
            byUniqueRegistration[reg] = records[records.length - 1];
    }
    return { byOperatorCode, scopedRegistrations, byUniqueRegistration };
}

export function ambiguousFleetCodes(fleet) {
    const groups = new Map();
    for (const vehicle of Array.isArray(fleet) ? fleet : []) {
        if (vehicle?.withdrawn) continue;
        const code = text(vehicle?.fleet_code || vehicle?.fleet_number);
        if (!code) continue;
        const operator = text(vehicle?.operator_id).toUpperCase();
        const registration = text(vehicle?.reg).toUpperCase()
            .replace(/[^A-Z0-9]+/g, "");
        if (!groups.has(code)) groups.set(code, new Set());
        groups.get(code).add(`${operator}\0${registration}`);
    }
    return new Set(
        [...groups.entries()]
            .filter(([, identities]) => identities.size > 1)
            .map(([code]) => code),
    );
}

export function vehicleDescription(
    pool, operator, fleetCode, ambiguousCodes = new Set(),
) {
    if (!pool || typeof pool !== "object") return null;
    const code = text(fleetCode);
    if (!code) return null;
    const noc = text(operator).toUpperCase();
    const scoped = noc ? `${noc}:${code}` : "";
    if (scoped && typeof pool[scoped] === "string") return pool[scoped];
    if (ambiguousCodes.has(code)) return null;
    return typeof pool[code] === "string" ? pool[code] : null;
}

if (typeof window !== "undefined") {
    window.BBB = window.BBB || {};
    Object.assign(window.BBB, {
        ambiguousFleetCodes,
        buildFleetLookups,
        vehicleDescription,
    });
}
