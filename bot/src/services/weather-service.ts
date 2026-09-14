// Bristol Bus Bot - Weather Service

import { httpFetch } from '../utils/http-client.js';
import { logger } from '../utils/logging.js';
import { DateTime } from 'luxon';

interface WeatherResponse {
    dt: number;
    name?: string;
    weather: {
        description: string;
        main: string;
    }[];
    main: {
        temp: number;
        feels_like: number;
        humidity: number;
        pressure: number;
    };
    wind: {
        speed: number;
        deg: number;
        gust?: number;
    };
    visibility?: number;
    clouds?: {
        all: number;
    };
    rain?: {
        '1h'?: number;
    };
}

interface AirQualityResponse {
    coord: number[];
    list: [{
        dt: number;
        main: {
            aqi: number; // 1=Good, 2=Fair, 3=Moderate, 4=Poor, 5=Very Poor
        };
        components: {
            co: number;
            no: number;
            no2: number;
            o3: number;
            so2: number;
            pm2_5: number;
            pm10: number;
            nh3: number;
        };
    }];
}

export class WeatherService {
    private weatherConfig: any;
    private weatherCache = new Map<string, { fetched: number; observed: number; text: string }>();
    private lastAirQualityFetch: number = 0;
    private cachedAirQuality: string | null = null;
    private cacheDuration: number = 10 * 60 * 1000; // 10 minutes

    constructor(weatherConfig: any, private readonly fetcher: typeof httpFetch = httpFetch) {
        this.weatherConfig = weatherConfig;
        logger.info('Weather Service initialized', {
            hasApiKey: !!weatherConfig.apiKey,
            baseUrl: weatherConfig.baseUrl
        });
    }

    async initialize(): Promise<void> {
        if (!this.weatherConfig.apiKey) {
            logger.warn('Weather Service: API key not provided. Weather context will be unavailable.');
        } else {
            logger.info('Weather Service is ready to fetch data.');
        }
    }

    public async getCurrentWeather(location?: { latitude: number; longitude: number }, includeAirQuality = false): Promise<string | null> {
        const now = Date.now();
        if (!this.weatherConfig.apiKey) return null;
        const { baseUrl, bristolLat, bristolLon, apiKey } = this.weatherConfig;
        const lat = location?.latitude ?? bristolLat;
        const lon = location?.longitude ?? bristolLon;
        if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
        const latitude = Number(lat.toFixed(2));
        const longitude = Number(lon.toFixed(2));
        const cacheKey = `${latitude},${longitude},${includeAirQuality}`;
        const cached = this.weatherCache.get(cacheKey);
        if (cached && now - cached.fetched < this.cacheDuration && now - cached.observed <= 60 * 60_000) {
            return cached.text;
        }
        const url = `${baseUrl}?lat=${latitude}&lon=${longitude}&appid=${encodeURIComponent(apiKey)}&units=metric`;

        try {
            logger.info('Fetching new weather data...');
            // Optional colour must not hold up the mandatory posting cycle.
            let deadline: ReturnType<typeof setTimeout> | undefined;
            const request = async (): Promise<WeatherResponse | null> => {
                const response = await this.fetcher(url, { timeoutMs: 5000, retries: 0 });
                if (!response.ok) {
                    logger.warn('OpenWeatherMap API request failed', { status: response.status });
                    return null;
                }
                return await response.json() as WeatherResponse;
            };
            const data = await Promise.race([
                request(),
                new Promise<null>(resolve => { deadline = setTimeout(() => resolve(null), 6000); }),
            ]).finally(() => clearTimeout(deadline));
            if (!data) return null;
            if (!Number.isFinite(data.dt) || data.dt * 1000 > now + 5 * 60_000
                || now - data.dt * 1000 > 60 * 60_000 || !Number.isFinite(data.main?.temp)) {
                logger.warn('Weather observation is stale or incomplete; omitting weather');
                return null;
            }

            // Build the weather summary.
            const parts: string[] = [];

            // Temperature and feels-like
            const temp = data.main?.temp.toFixed(0);
            const feelsLike = Number.isFinite(data.main.feels_like) ? data.main.feels_like.toFixed(0) : temp;
            if (temp !== feelsLike) {
                parts.push(`${temp}°C (feels like ${feelsLike}°C)`);
            } else {
                parts.push(`${temp}°C`);
            }

            // Weather description
            if (data.weather?.[0]?.description) {
                parts.push(`with ${data.weather[0].description}`);
            }

            // Wind information (convert m/s to mph: 1 m/s ≈ 2.237 mph)
            if (data.wind?.speed) {
                const windMph = (data.wind.speed * 2.237).toFixed(0);
                const windDirection = this.getWindDirection(data.wind.deg);
                if (data.wind.gust) {
                    const gustMph = (data.wind.gust * 2.237).toFixed(0);
                    parts.push(`wind ${windDirection} ${windMph}mph (gusts ${gustMph}mph)`);
                } else {
                    parts.push(`wind ${windDirection} ${windMph}mph`);
                }
            }

            // Humidity (only if notable)
            if (data.main?.humidity && (data.main.humidity > 85 || data.main.humidity < 30)) {
                parts.push(`humidity ${data.main.humidity}%`);
            }

            // Rain (if present)
            if (data.rain?.['1h']) {
                parts.push(`rain ${data.rain['1h']}mm/hr`);
            }

            // Visibility (only if poor - less than 5km)
            if (data.visibility && data.visibility < 5000) {
                const visKm = (data.visibility / 1000).toFixed(1);
                parts.push(`visibility ${visKm}km`);
            }

            // Preserve the legacy Bristol air-quality option without applying it to Bath or Wells.
            const airQuality = includeAirQuality && !location ? await this.getAirQuality() : null;
            if (airQuality) {
                parts.push(airQuality);
            }

            const observedAt = DateTime.fromSeconds(data.dt).setZone('Europe/London').setLocale('en-GB').toFormat('yyyy-MM-dd HH:mm ZZZZ');
            const area = typeof data.name === 'string' && data.name.trim()
                ? data.name.trim() : `${latitude}, ${longitude}`;
            const formattedWeather = `OpenWeather area observation near ${area} at ${observedAt}: ${parts.join(', ')}`;
            this.weatherCache.delete(cacheKey);
            this.weatherCache.set(cacheKey, { fetched: now, observed: data.dt * 1000, text: formattedWeather });
            if (this.weatherCache.size > 16) this.weatherCache.delete(this.weatherCache.keys().next().value!);
            logger.info(`Fetched and cached weather: ${formattedWeather}`);
            return formattedWeather;

        } catch (error: any) {
            logger.error('Failed to fetch weather data', {
                errorType: error instanceof Error ? error.name : 'unknown'
            });
            return null;
        }
    }

    private getWindDirection(degrees: number): string {
        const directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
        const index = Math.round(degrees / 22.5) % 16;
        return directions[index];
    }

    private async getAirQuality(): Promise<string | null> {
        const now = Date.now();

        // Check cache first
        if (this.cachedAirQuality && (now - this.lastAirQualityFetch < this.cacheDuration)) {
            return this.cachedAirQuality;
        }

        if (!this.weatherConfig.apiKey) return null;

        const { bristolLat, bristolLon, apiKey } = this.weatherConfig;
        const url = `https://api.openweathermap.org/data/2.5/air_pollution?lat=${bristolLat}&lon=${bristolLon}&appid=${apiKey}`;

        try {
            const response = await this.fetcher(url, { timeoutMs: 5000, retries: 0 });
            if (!response.ok) {
                logger.error('Air quality API request failed', { status: response.status });
                return null;
            }

            const data = await response.json() as AirQualityResponse;
            const aqi = data.list?.[0]?.main?.aqi;
            const components = data.list?.[0]?.components;

            if (!aqi) return null;

            // Format air quality based on AQI level
            const aqiLabels = ['', 'good', 'fair', 'moderate', 'poor', 'very poor'];
            const aqiLabel = aqiLabels[aqi] || 'unknown';

            // Only report if air quality is concerning (moderate or worse)
            if (aqi >= 3) {
                const parts: string[] = [`air quality ${aqiLabel}`];

                // Add specific pollutant info if notably high
                if (components) {
                    if (components.pm2_5 > 25) {
                        parts.push(`PM2.5 ${components.pm2_5.toFixed(0)}µg/m³`);
                    }
                    if (components.pm10 > 50) {
                        parts.push(`PM10 ${components.pm10.toFixed(0)}µg/m³`);
                    }
                }

                const result = parts.join(' ');
                this.cachedAirQuality = result;
                this.lastAirQualityFetch = now;
                logger.info(`Fetched air quality: ${result}`);
                return result;
            }

            // Good or fair air quality - don't report (saves space)
            this.cachedAirQuality = null;
            this.lastAirQualityFetch = now;
            return null;

        } catch (error: any) {
            logger.error('Failed to fetch air quality data', {
                errorType: error instanceof Error ? error.name : 'unknown'
            });
            return null;
        }
    }
}
