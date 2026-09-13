/* Leaflet owns the camera and overlays; MapLibre paints only the background. */
export const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, &copy; <a href="https://carto.com/attributions">CARTO</a>';
const STYLES = { day: 'voyager', night: 'dark-matter' };

export function cartoRequest(url, key) {
    const parsed = new URL(url);
    // Never send the project key to a host named by an unrelated resource.
    if (parsed.protocol === 'https:' && (parsed.hostname === 'basemaps.cartocdn.com'
        || parsed.hostname.endsWith('.basemaps.cartocdn.com'))) {
        parsed.searchParams.set('key', key);
        return { url: parsed.href };
    }
    return { url };
}

export function styleUrl(theme, key) {
    return cartoRequest(`https://basemaps.cartocdn.com/gl/${STYLES[theme] || STYLES.day}-gl-style/style.json`, key).url;
}

function rasterUrl(theme, key) {
    const path = theme === 'night' ? 'dark_all' : 'rastertiles/voyager';
    return `https://{s}.basemaps.cartocdn.com/${path}/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(key)}`;
}

async function loadVector() {
    // Avoid constructing a partially initialised MapLibre map on no-WebGL phones.
    const probe = document.createElement('canvas').getContext('webgl2');
    if (!probe) throw new Error('WebGL is unavailable');
    probe.getExtension('WEBGL_lose_context')?.loseContext();
    const maplibre = await import('../vendor/maplibre-gl-6.6.0/maplibre-gl.mjs');
    maplibre.setWorkerCount(2);
    window.maplibregl = maplibre;
    await import('../vendor/maplibre-gl-leaflet-0.1.4/leaflet-maplibre-gl.js');
}

export function createBasemap(map, { key, theme = 'day', L = window.L,
    load = loadVector, timeoutMs = 15000 } = {}) {
    if (!key) throw new Error('Map configuration is unavailable');
    let layer = null;
    let gl = null;
    let mode = 'loading';
    let timer;
    let disposed = false;
    const container = map.getContainer();
    map.attributionControl.addAttribution(ATTRIBUTION);

    function setMode(value) {
        mode = value;
        container.dataset.basemap = value;
    }
    function removeVector() {
        if (!layer) return;
        // A failed WebGL constructor leaves a bridge container but no GL map.
        if (!layer.getMaplibreMap()) {
            layer.getContainer()?.remove();
            layer.onRemove = () => {};
        }
        try { map.removeLayer(layer); }
        catch {
            // GPU allocation may fail after the probe succeeds. Upstream's
            // remove() then cannot destroy its absent painter; detach the
            // bridge and its Leaflet listeners without blocking the fallback.
            layer.getContainer()?.remove();
            layer.onRemove = () => {};
            map.removeLayer(layer);
        }
        layer = null;
        gl = null;
    }
    function fallback() {
        if (disposed || mode === 'raster') return;
        clearTimeout(timer);
        setMode('raster');
        removeVector();
        layer = L.tileLayer(rasterUrl(theme, key), { subdomains: 'abcd', maxZoom: 20 }).addTo(map);
        // Deliberately omit source errors/URLs because they may contain the key.
        console.warn('Vector background unavailable; using the fallback map.');
    }
    function armTimeout() {
        clearTimeout(timer);
        timer = setTimeout(fallback, timeoutMs);
    }
    function vectorError() {
        // Do not remove MapLibre in the middle of its event dispatch/render.
        queueMicrotask(fallback);
    }
    setMode('loading');
    armTimeout();
    Promise.resolve().then(load).then(() => {
        if (disposed || mode === 'raster') return;
        layer = L.maplibreGL({
            style: styleUrl(theme, key),
            transformRequest: url => cartoRequest(url, key),
            interactive: false,
            attributionControl: false,
            // Cap high-density canvas cost while retaining crisp map labels.
            pixelRatio: Math.min(globalThis.devicePixelRatio || 1, 2),
            canvasContextAttributes: { antialias: false },
        });
        layer.addTo(map);
        gl = layer.getMaplibreMap();
        gl.on('error', vectorError);
        gl.on('webglcontextlost', vectorError);
        gl.on('idle', () => {
            if (!disposed && mode !== 'raster') {
                clearTimeout(timer);
                setMode('vector');
            }
        });
    }).catch(fallback);

    return {
        get mode() { return mode; },
        get vectorMap() { return gl; },
        setTheme(value) {
            theme = value === 'night' ? 'night' : 'day';
            if (disposed) return;
            if (mode === 'raster') layer.setUrl(rasterUrl(theme, key));
            else if (gl) {
                armTimeout();
                try { gl.setStyle(styleUrl(theme, key)); }
                catch { fallback(); }
            }
        },
        destroy() {
            disposed = true;
            clearTimeout(timer);
            if (mode === 'raster') map.removeLayer(layer);
            else removeVector();
            map.attributionControl.removeAttribution(ATTRIBUTION);
        },
    };
}

if (typeof window !== 'undefined') {
    window.BBB = Object.assign(window.BBB || {}, { createBasemap });
}
