// Bristol Bus Bot - Weather Service

import { httpFetch } from '../utils/http-client.js';
import { logger } from '../utils/logging.js';

interface WeatherResponse {
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
    private lastFetch: number = 0;
    private cachedWeather: string | null = null;
    private lastAirQualityFetch: number = 0;
    private cachedAirQuality: string | null = null;
    private cacheDuration: number = 10 * 60 * 1000; // 10 minutes

    constructor(weatherConfig: any) {
        this.weatherConfig = weatherConfig;
        logger.info('Weather Service initialized', {
            hasApiKey: !!weatherConfig.apiKey,
            apiKeyLength: weatherConfig.apiKey ? weatherConfig.apiKey.length : 0,
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

    public async getCurrentWeather(): Promise<string | null> {
        const now = Date.now();
        if (this.cachedWeather && (now - this.lastFetch < this.cacheDuration)) {
            logger.info('Returning cached weather data.');
            return this.cachedWeather;
        }

        if (!this.weatherConfig.apiKey) return null;

        const { baseUrl, bristolLat, bristolLon, apiKey } = this.weatherConfig;
        const url = `${baseUrl}?lat=${bristolLat}&lon=${bristolLon}&appid=${apiKey}&units=metric`;

        try {
            logger.info('Fetching new weather data...');
            const response = await httpFetch(url, { timeoutMs: 15000 });
            if (!response.ok) {
                logger.error('OpenWeatherMap API request failed', { status: response.status });
                return null;
            }
            const data = await response.json() as WeatherResponse;

            // Build the weather summary.
            const parts: string[] = [];

            // Temperature and feels-like
            const temp = data.main?.temp.toFixed(0);
            const feelsLike = data.main?.feels_like.toFixed(0);
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

            // Fetch air quality data (non-blocking)
            const airQuality = await this.getAirQuality();
            if (airQuality) {
                parts.push(airQuality);
            }

            const formattedWeather = parts.join(', ');

            this.cachedWeather = formattedWeather;
            this.lastFetch = now;
            logger.info(`Fetched and cached weather: ${formattedWeather}`);
            return formattedWeather;

        } catch (error: any) {
            logger.error('Failed to fetch weather data', { error: error.message });
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
            const response = await httpFetch(url, { timeoutMs: 10000 });
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
            logger.error('Failed to fetch air quality data', { error: error.message });
            return null;
        }
    }
}
