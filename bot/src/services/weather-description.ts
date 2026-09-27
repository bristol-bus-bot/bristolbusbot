/** Translate the fresh WeatherService observation; never infer rain from visibility. */
export function weatherDescription(raw: string): string | null {
    const parts = raw.match(/^OpenWeather area observation near (.{1,100}) at (\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?: [^:]+)?: (-?\d+(?:\.\d+)?)°C.*?, with ([^,]+)/);
    if (!parts) return null;
    const [, area, observed, number, condition] = parts;
    const temperature = Number(number);
    const sky = condition.toLowerCase();
    const descriptions: [RegExp, string][] = [[/thunderstorm/, 'thunderstorms'], [/heavy.*rain/, 'heavy rain'],
        [/drizzle|light rain/, 'light rain'], [/rain/, 'rain'], [/snow/, 'snow'], [/fog/, 'fog'],
        [/mist/, 'mist'], [/clear/, 'clear skies'], [/cloud/, 'cloudy skies']];
    const description = descriptions.find(([pattern]) => pattern.test(sky))?.[1];
    if (!description) return null;
    const wind = Number(raw.match(/wind [A-Z]+ (\d+)mph/)?.[1] || 0);
    const notable = temperature <= 8 || temperature >= 22 || wind >= 25 || !['cloudy skies'].includes(description);
    if (!notable) return null;
    const feel = temperature <= 3 ? 'cold' : temperature <= 8 ? 'chilly' : temperature >= 25 ? 'hot' : temperature >= 22 ? 'warm' : 'mild';
    return `Area observation near ${area} at ${observed}: ${feel}, ${description}${wind >= 25 ? ', strong winds' : ''}${temperature <= 3 || temperature >= 25 ? ` (${temperature}°C)` : ''}. Area conditions, not a measurement at the stop.`;
}
