/* Main browser application: map state, data refresh and user interactions. */
        let map = null;
        let busMarkers = new Map();
        let stopMarkers = new Map();
        let selectedStopCode = null;
        let refreshInterval = null;
        let departureRefreshInterval = null;
        let routeShapesData = {};  // "OPERATOR_line_direction" key -> { route, operator, direction, points }
        let routeShapeLayers = []; // Leaflet polyline layers
        let latestBusData = [];    // Raw bus array from last /api/buses fetch
        let busbotPosts = {};      // vehicleRef -> { postUrl, postText, timestamp } for featured buses
        let routeIndex = {};       // "OPERATOR_line" -> [{ key, operator, route, direction, points }]
        let activeRouteLine = null;       // Currently selected route key e.g. "FBRI_9"
        let activeRouteLineLayers = [];   // Polyline layers for route search view
        let activeRouteVehicleRefs = [];  // Vehicle refs highlighted in route view
        let activeRoutePathLoading = false;

        function initMap() {
            map = L.map('map', { zoomControl: true }).setView([51.4545, -2.5879], 13);
            L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; OpenStreetMap &copy; CARTO',
                subdomains: 'abcd',
                maxZoom: 20
            }).addTo(map);
        }

        // Extract first hex colour from a CSS gradient string
        function extractLiveryColor(livery) {
            if (!livery || !livery.left) return null;
            const matches = livery.left.match(/#[0-9a-fA-F]{3,8}/g) || [];
            // Pick the most visible color for use on a LIGHT map background
            let best = null, bestScore = -1;
            for (let hex of matches) {
                let c = hex.toLowerCase();
                // Skip transparent shorthand (#0000)
                if (c === '#0000') continue;
                // Expand 3-char shorthand (#fd0 -> #ffdd00)
                if (c.length === 4) c = '#' + c[1]+c[1] + c[2]+c[2] + c[3]+c[3];
                if (c.length !== 7) continue;
                // Parse RGB
                const r = parseInt(c.slice(1,3), 16), g = parseInt(c.slice(3,5), 16), b = parseInt(c.slice(5,7), 16);
                // Skip near-black, near-white, and very pale colors
                const lum = (r * 0.299 + g * 0.587 + b * 0.114);
                if (lum < 20 || lum > 215) continue;
                // Score by saturation — vibrant over grey — and prefer
                // mid-dark over bright (light ground: pale rings vanish)
                const max = Math.max(r, g, b), min = Math.min(r, g, b);
                const sat = max === 0 ? 0 : (max - min) / max;
                const score = sat * 100 + Math.min(255 - lum, 200);
                if (score > bestScore) { bestScore = score; best = c; }
            }
            return best;
        }

        // Extract multiple unique, visible colours from livery gradient
        function extractLiveryColors(livery) {
            if (!livery || !livery.left) return { primary: null, secondary: null, all: [] };
            const matches = livery.left.match(/#[0-9a-fA-F]{3,6}/g) || [];
            // Filter out transparent-ish and near-black/white colours, deduplicate
            const seen = new Set();
            const visible = matches.filter(c => {
                const lower = c.toLowerCase();
                if (seen.has(lower)) return false;
                if (lower === '#000' || lower === '#000000' || lower === '#0000') return false;
                if (lower === '#fff' || lower === '#ffffff') return false;
                seen.add(lower);
                return true;
            });
            return {
                primary: visible[0] || null,
                secondary: visible[1] || visible[0] || null,
                all: visible
            };
        }

        function createBusIcon(eventType, livery, bearing, isFeatured) {
            // UK transport palette: road sign green, GOV.UK red, amber, TfL blue
            const color = eventType === 'delayed' ? '#D4351C' :
                          eventType === 'early' ? '#eab308' :
                          eventType === 'waiting' ? '#1D70B8' : '#00703C';
            const liveryColor = extractLiveryColor(livery) || '#7E8582';

            const size = isFeatured ? 36 : 28;
            const center = size / 2;
            const coreR = isFeatured ? 10 : 8;
            const liveryR = coreR + 3;

            // Outer livery ring — solid, distinct per operator
            const liveryRing = `<circle cx="${center}" cy="${center}" r="${liveryR}" fill="none" stroke="${liveryColor}" stroke-width="3"/>`;
            const featuredRing = ''; // featured glow handled via CSS class
            // Subtle drop shadow for depth
            const shadow = `<circle cx="${center}" cy="${center + 0.8}" r="${coreR}" fill="#000" opacity="0.3"/>`;

            let bearingIndicator;
            if (bearing !== null && bearing !== undefined) {
                // Crisp geometric chevron pointing up, rotated by bearing
                const chevH = isFeatured ? 5.5 : 4.5;
                const chevW = isFeatured ? 5 : 4;
                bearingIndicator = `
                    <g transform="rotate(${bearing} ${center} ${center})">
                        <path d="M${center - chevW} ${center + chevH * 0.3} L${center} ${center - chevH} L${center + chevW} ${center + chevH * 0.3}"
                              fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="square" stroke-linejoin="miter"/>
                    </g>`;
            } else {
                // Solid central dot when no bearing
                bearingIndicator = `<circle cx="${center}" cy="${center}" r="2.5" fill="#fff"/>`;
            }

            const svg = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
                    ${featuredRing}
                    ${liveryRing}
                    ${shadow}
                    <circle cx="${center}" cy="${center}" r="${coreR}" fill="${color}"/>
                    ${bearingIndicator}
                </svg>`;

            return L.divIcon({
                html: svg,
                className: isFeatured ? 'bus-marker featured' : 'bus-marker',
                iconSize: [size, size],
                iconAnchor: [center, center],
                popupAnchor: [0, -center]
            });
        }

        function createDepotIcon(livery) {
            const liveryColor = extractLiveryColor(livery) || '#7E8582';
            const svg = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 22" width="22" height="22">
                    <circle cx="11" cy="11" r="9" fill="none" stroke="${liveryColor}" stroke-width="2" opacity="0.4"/>
                    <circle cx="11" cy="11" r="5.5" fill="#7E8582" opacity="0.6"/>
                    <circle cx="11" cy="11" r="2" fill="#555"/>
                </svg>
            `;
            return L.divIcon({
                html: svg,
                className: 'bus-marker',
                iconSize: [22, 22],
                iconAnchor: [11, 11],
                popupAnchor: [0, -11]
            });
        }

        function createStopIcon(isSelected) {
            const size = isSelected ? 10 : 6;
            const color = isSelected ? '#b48800' : '#8b949e';
            const svg = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="${size}" height="${size}">
                    <circle cx="5" cy="5" r="4" fill="${color}" stroke="#fff" stroke-width="1"/>
                </svg>
            `;
            return L.divIcon({
                html: svg,
                className: '',
                iconSize: [size, size],
                iconAnchor: [size/2, size/2],
                popupAnchor: [0, -size/2]
            });
        }

        // --- Route-snapped bus position system ---
        // Each bus tracks its last known shape index so we can detect forward/reverse
        const busShapeState = new Map(); // vehicleRef -> { shapeKey, idx }

        // Find nearest point index on a shape polyline
        function findNearestShapeIndex(lat, lon, points) {
            let minDist = Infinity;
            let minIdx = 0;
            for (let i = 0; i < points.length; i++) {
                const dl = lat - points[i][0];
                const dn = lon - points[i][1];
                const d = dl * dl + dn * dn;
                if (d < minDist) {
                    minDist = d;
                    minIdx = i;
                }
            }
            return { idx: minIdx, dist: Math.sqrt(minDist) };
        }

        // Get all shape variants matching operator + line + direction (keys now include variant: OPERATOR_line_dir_var)
        function getShapeVariants(line, directionId, operator) {
            if (!line || !routeShapesData) return [];
            const prefix = operator ? `${operator}_${line}_${directionId || 0}_` : null;
            const results = [];
            for (const [k, shape] of Object.entries(routeShapesData)) {
                if (!shape.points || shape.points.length < 2) continue;
                // Match by operator+line+direction (any variant)
                if (prefix && k.startsWith(prefix.slice(0, -1))) {
                    // Check the key pattern matches OPERATOR_LINE_DIR_VARIANT
                    const parts = k.split('_');
                    const kDir = parseInt(parts[parts.length - 2]);
                    const kLine = parts.slice(1, parts.length - 2).join('_');
                    const kOp = parts[0];
                    if (kOp === operator && kLine === line && kDir === (directionId || 0)) {
                        results.push({ key: k, points: shape.points });
                    }
                }
            }
            return results;
        }

        // Get the best route shape for a bus, picking the closest geographic variant
        function getShapeForBus(line, directionId, operator, busLat, busLon, scheduleStops) {
            if (!line || !routeShapesData) return null;

            // Collect all matching variants (operator-specific, then fallback)
            let variants = operator ? getShapeVariants(line, directionId, operator) : [];
            // Also try opposite direction if nothing found
            if (variants.length === 0 && operator) {
                variants = getShapeVariants(line, directionId === 0 ? 1 : 0, operator);
            }
            // Fallback: any operator
            if (variants.length === 0) {
                for (const [k, shape] of Object.entries(routeShapesData)) {
                    if (!shape.points || shape.points.length < 2) continue;
                    if (shape.route === line && shape.direction === (directionId || 0)) {
                        variants.push({ key: k, points: shape.points });
                    }
                }
            }

            if (variants.length === 0) return null;
            if (variants.length === 1) return variants[0];

            // Multiple variants — pick the one whose shape best matches the journey's stops.
            // scheduleStops is an array of {latitude, longitude} from the journey schedule.
            if (scheduleStops && scheduleStops.length > 0) {
                // Score each variant: how many schedule stops are within ~300m of the shape
                let best = variants[0], bestScore = -1;
                for (const v of variants) {
                    let score = 0;
                    // Sample up to 20 evenly-spaced stops for performance
                    const step = Math.max(1, Math.floor(scheduleStops.length / 20));
                    for (let i = 0; i < scheduleStops.length; i += step) {
                        const s = scheduleStops[i];
                        if (!s.latitude || !s.longitude) continue;
                        const nearest = findNearestShapeIndex(s.latitude, s.longitude, v.points);
                        if (nearest.dist < 0.003) score++; // ~300m
                    }
                    if (score > bestScore) { bestScore = score; best = v; }
                }
                return best;
            }

            // Fallback: use bus position to pick closest, preferring longer routes
            if (busLat && busLon) {
                // Filter to variants near the bus, then pick longest
                const nearby = variants.filter(v => {
                    const nearest = findNearestShapeIndex(busLat, busLon, v.points);
                    return nearest.dist < 0.014;
                });
                const pool = nearby.length > 0 ? nearby : variants;
                pool.sort((a, b) => b.points.length - a.points.length);
                return pool[0];
            }
            // No info — return longest variant
            variants.sort((a, b) => b.points.length - a.points.length);
            return variants[0];
        }

        // Smoothly animate a marker along route shape points (always forward)
        function animateAlongRoute(marker, path, duration) {
            if (path.length < 2) {
                marker.setLatLng([path[0][0], path[0][1]]);
                return;
            }
            const startTime = performance.now();

            // Calculate cumulative distances along the path segment
            const dists = [0];
            for (let i = 1; i < path.length; i++) {
                const dl = path[i][0] - path[i-1][0];
                const dn = path[i][1] - path[i-1][1];
                dists.push(dists[i-1] + Math.sqrt(dl*dl + dn*dn));
            }
            const totalDist = dists[dists.length - 1];
            if (totalDist < 0.00001) {
                marker.setLatLng([path[path.length-1][0], path[path.length-1][1]]);
                return;
            }

            function step(now) {
                const t = Math.min((now - startTime) / duration, 1);
                const targetDist = t * totalDist;

                let segIdx = 0;
                for (let i = 1; i < dists.length; i++) {
                    if (dists[i] >= targetDist) { segIdx = i - 1; break; }
                    segIdx = i - 1;
                }

                const segLen = dists[segIdx + 1] - dists[segIdx];
                const segT = segLen > 0 ? (targetDist - dists[segIdx]) / segLen : 0;
                const lat = path[segIdx][0] + (path[segIdx+1][0] - path[segIdx][0]) * segT;
                const lng = path[segIdx][1] + (path[segIdx+1][1] - path[segIdx][1]) * segT;

                marker.setLatLng([lat, lng]);
                if (t < 1) requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
        }

        // Smooth straight-line animation fallback (the classic hypnotic glide)
        function animateStraightLine(marker, targetLat, targetLng, duration) {
            const start = marker.getLatLng();
            const startTime = performance.now();
            function step(now) {
                const t = Math.min((now - startTime) / duration, 1);
                const eased = t * (2 - t); // ease-out for smooth deceleration
                const lat = start.lat + (targetLat - start.lat) * eased;
                const lng = start.lng + (targetLng - start.lng) * eased;
                marker.setLatLng([lat, lng]);
                if (t < 1) requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
        }

        // Core animation: snap bus to its route when possible, smooth glide otherwise
        function animateMarker(marker, targetLat, targetLng, duration, line, directionId, vehicleRef, operatorRef) {
            const start = marker.getLatLng();
            const dlat = targetLat - start.lat;
            const dlng = targetLng - start.lng;
            const dist = Math.sqrt(dlat * dlat + dlng * dlng);

            // No movement
            if (dist < 0.00001) return;

            // Cap wild jumps — if GPS jumped more than ~2km, just teleport (data glitch)
            const distMetres = dist * 111000;
            if (distMetres > 2000) {
                marker.setLatLng([targetLat, targetLng]);
                return;
            }

            // Try to snap to route shape
            const shapeInfo = getShapeForBus(line, directionId, operatorRef, targetLat, targetLng);
            if (shapeInfo) {
                const { key: shapeKey, points } = shapeInfo;
                const nearest = findNearestShapeIndex(targetLat, targetLng, points);
                const snapDistM = nearest.dist * 111000;

                // Only use shape if GPS is within ~500m of route
                if (snapDistM < 500) {
                    const newIdx = nearest.idx;
                    const prev = busShapeState.get(vehicleRef);

                    // If bus was on same shape before, check direction
                    if (prev && prev.shapeKey === shapeKey) {
                        const prevIdx = prev.idx;

                        if (newIdx === prevIdx) {
                            // Bus hasn't moved along shape — smooth glide to exact snap point
                            busShapeState.set(vehicleRef, { shapeKey, idx: newIdx });
                            animateStraightLine(marker, points[newIdx][0], points[newIdx][1], duration);
                            return;
                        }

                        if (newIdx < prevIdx) {
                            // REVERSE — smooth glide to new position (not jarring teleport)
                            busShapeState.set(vehicleRef, { shapeKey, idx: newIdx });
                            animateStraightLine(marker, points[newIdx][0], points[newIdx][1], duration);
                            return;
                        }

                        // FORWARD — animate along the route shape points
                        const hops = newIdx - prevIdx;
                        if (hops <= 80) {
                            const path = points.slice(prevIdx, newIdx + 1);
                            busShapeState.set(vehicleRef, { shapeKey, idx: newIdx });
                            animateAlongRoute(marker, path, duration);
                            return;
                        } else {
                            // Big jump forward — smooth glide rather than teleport
                            busShapeState.set(vehicleRef, { shapeKey, idx: newIdx });
                            animateStraightLine(marker, points[newIdx][0], points[newIdx][1], duration);
                            return;
                        }
                    } else {
                        // First time on this shape — smooth glide to snap point
                        busShapeState.set(vehicleRef, { shapeKey, idx: newIdx });
                        animateStraightLine(marker, points[newIdx][0], points[newIdx][1], duration);
                        return;
                    }
                }
            }

            // No shape data or too far from route — smooth straight-line glide
            animateStraightLine(marker, targetLat, targetLng, duration);
        }

        function updateBusMarkers(buses) {
            const current = new Set(buses.map(b => b.vehicleRef));

            busMarkers.forEach((marker, ref) => {
                if (!current.has(ref)) {
                    map.removeLayer(marker);
                    busMarkers.delete(ref);
                }
            });

            buses.forEach(bus => {
                const { vehicleRef, operatorRef, line, destination, latitude, longitude,
                        delayMinutes, eventType, waitingAtOrigin, livery, model, fleetNumber,
                        reg, lastStopName, bearing, description,
                        fuel, isDoubleDecker, isElectric, isCoach, specialFeatures, garage, branding,
                        atDepot, depotName, directionId, journeyCode, directionRef, originAimedDep, hasSchedule } = bus;

                // Build the marker and popup for this vehicle.
                let icon, zOffset;
                if (eventType === 'depot') {
                    icon = window.BBB.depotIcon(livery);
                    zOffset = 100;
                } else {
                    const isFeatured = !!busbotPosts[vehicleRef];
                    icon = window.BBB.busIcon(bus, isFeatured);
                    zOffset = isFeatured ? 1500 : 1000;
                }
                const popup = window.BBB.busPopup(bus, busbotPosts[vehicleRef]);

                if (busMarkers.has(vehicleRef)) {
                    const m = busMarkers.get(vehicleRef);
                    animateMarker(m, latitude, longitude, 12000, line, directionId, vehicleRef, operatorRef);
                    m.setIcon(icon);
                    m.setPopupContent(popup);
                } else {
                    const m = L.marker([latitude, longitude], {
                        icon: icon,
                        zIndexOffset: zOffset
                    }).addTo(map).bindPopup(popup);
                    busMarkers.set(vehicleRef, m);
                }

                // Maintain dimming if a route is focused
                if (activeRouteVehicleRef) {
                    const el = busMarkers.get(vehicleRef)?.getElement();
                    if (el) el.style.opacity = vehicleRef === activeRouteVehicleRef ? '1' : '0.2';
                }
            });

            // Update header status counts
            const punctualCount = buses.filter(b => b.eventType === 'punctual').length;
            const earlyCount = buses.filter(b => b.eventType === 'early').length;
            const delayedCount = buses.filter(b => b.eventType === 'delayed').length;
            const waitingCount = buses.filter(b => b.eventType === 'waiting' || b.waitingAtOrigin).length;
            const depotCount = buses.filter(b => b.eventType === 'depot').length;
            document.getElementById('count-punctual').textContent = punctualCount;
            document.getElementById('count-early').textContent = earlyCount;
            document.getElementById('count-delayed').textContent = delayedCount;
            document.getElementById('count-waiting').textContent = waitingCount;
            document.getElementById('count-depot').textContent = depotCount;
        }

        function updateStopMarkers(stops) {
            const zoom = map.getZoom();
            if (zoom < 15) {
                stopMarkers.forEach(m => map.removeLayer(m));
                stopMarkers.clear();
                return;
            }

            const bounds = map.getBounds();
            const visible = stops.filter(s => bounds.contains([s.latitude, s.longitude]));
            const visibleCodes = new Set(visible.map(s => s.stop_code));

            stopMarkers.forEach((m, code) => {
                if (!visibleCodes.has(code)) {
                    map.removeLayer(m);
                    stopMarkers.delete(code);
                }
            });

            visible.forEach(stop => {
                const isSel = stop.stop_code === selectedStopCode;
                if (stopMarkers.has(stop.stop_code)) {
                    stopMarkers.get(stop.stop_code).setIcon(createStopIcon(isSel));
                } else {
                    const m = L.marker([stop.latitude, stop.longitude], {
                        icon: createStopIcon(isSel),
                        zIndexOffset: isSel ? 500 : 0
                    }).addTo(map);

                    m.bindPopup(window.BBB.stopPopup(stop, selectStop));
                    m.on('click', () => selectStop(stop.stop_code));
                    stopMarkers.set(stop.stop_code, m);
                }
            });
        }

        let selectedStopLat = null;
        let selectedStopLon = null;

        window.selectStop = function(stopCode) {
            // Clear any active route view first so departures can load
            if (routeViewActive || activeRouteLayer) {
                clearBusRoute();
            }
            selectedStopCode = stopCode;
            // Store stop location for fly-to
            const stopInfo = searchStops.find(s => s.stop_code === stopCode);
            if (stopInfo) {
                selectedStopLat = stopInfo.lat;
                selectedStopLon = stopInfo.lon;
            } else {
                // Fallback: try allStops
                const s = allStops.find(s => s.stop_code === stopCode);
                if (s) { selectedStopLat = s.latitude; selectedStopLon = s.longitude; }
            }

            stopMarkers.forEach((m, code) => {
                m.setIcon(createStopIcon(code === stopCode));
                m.setZIndexOffset(code === stopCode ? 500 : 0);
            });

            document.getElementById('board-prompt').classList.add('hidden');
            document.getElementById('stop-header').classList.remove('hidden');
            loadDepartures(stopCode);

            if (departureRefreshInterval) clearInterval(departureRefreshInterval);
            departureRefreshInterval = setInterval(() => loadDepartures(stopCode), 15000);

            // Mobile: auto-peek the bottom sheet
            if (isMobile()) setSheetState('peek');
        }

        window.flyToSelectedStop = function() {
            if (selectedStopLat && selectedStopLon) {
                map.flyTo([selectedStopLat, selectedStopLon], 16);
                // Open the stop marker popup if it exists
                const marker = stopMarkers.get(selectedStopCode);
                if (marker) setTimeout(() => marker.openPopup(), 600);
            }
        }

        async function loadDepartures(stopCode) {
            // Don't overwrite sidebar if route view is active
            if (routeViewActive) return;
            try {
                const [liveRes, schedRes] = await Promise.all([
                    fetch(`/api/departures/${stopCode}`),
                    fetch(`/api/scheduled-departures/${stopCode}`)
                ]);

                if (!liveRes.ok) console.warn(`loadDepartures: /api/departures/${stopCode} returned HTTP ${liveRes.status}`);
                if (!schedRes.ok) console.warn(`loadDepartures: /api/scheduled-departures/${stopCode} returned HTTP ${schedRes.status}`);

                const liveData = liveRes.ok ? await liveRes.json() : { departures: [] };
                const schedData = schedRes.ok ? await schedRes.json() : { scheduled_departures: [] };

                const liveCount = (liveData.departures || []).length;
                const schedCount = (schedData.scheduled_departures || []).length;
                console.log(`Departures for ${stopCode}: ${liveCount} live, ${schedCount} scheduled`);

                displayDepartures(liveData, schedData);
            } catch (e) {
                console.error(`loadDepartures failed for stop ${stopCode}:`, e);
                if (routeViewActive) return;
                const errHost = document.getElementById('departures-list');
                errHost.replaceChildren(Object.assign(document.createElement('div'),
                    { className: 'board-empty board-error',
                      textContent: 'failed to load departures' }));
            }
        }

        function mergeDepartures(liveDeps, scheduledDeps) {
            // Start with scheduled, replace matches with live
            const merged = [];
            const usedScheduled = new Set();

            // For each live departure, find a matching scheduled one
            liveDeps.forEach(live => {
                let matched = false;
                for (let i = 0; i < scheduledDeps.length; i++) {
                    if (usedScheduled.has(i)) continue;
                    const sched = scheduledDeps[i];
                    if (sched.line === live.line && Math.abs(sched.eta_mins - live.eta_mins) <= 5) {
                        usedScheduled.add(i);
                        matched = true;
                        break;
                    }
                }
                merged.push({ ...live, source: 'live' });
            });

            // Add remaining unmatched scheduled departures
            scheduledDeps.forEach((sched, i) => {
                if (!usedScheduled.has(i)) {
                    merged.push(sched);
                }
            });

            // Sort by eta_mins
            merged.sort((a, b) => a.eta_mins - b.eta_mins);
            return merged.slice(0, 20);
        }

        function displayDepartures(liveData, schedData) {
            if (routeViewActive) return;
            if (window.BBB && window.BBB.renderBoard) {
                window.BBB.renderBoard(liveData, schedData,
                    { searchStops: searchStops, locateBus: locateBus });
            }
        }

        let boundaryLayer = null;
        async function fetchBoundary() {
            try {
                const res = await fetch('/api/boundary');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const geojson = await res.json();
                boundaryLayer = L.geoJSON(geojson, {
                    style: {
                        color: '#f59e0b',
                        weight: 1.5,
                        opacity: 0.4,
                        fillOpacity: 0.03,
                        dashArray: '6 4'
                    },
                    interactive: false
                }).addTo(map);
                boundaryLayer.bringToBack();
                console.log('Loaded WECA boundary layer');
            } catch (e) {
                console.error('fetchBoundary failed:', e);
            }
        }

        function toggleBoundary(show) {
            if (!boundaryLayer) return;
            if (show) { boundaryLayer.addTo(map); boundaryLayer.bringToBack(); }
            else { map.removeLayer(boundaryLayer); }
        }

        let _lastBusCount = 0;
        async function fetchBuses() {
            try {
                const res = await fetch('/api/buses');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                latestBusData = data.buses || [];
                updateBusMarkers(latestBusData);
                document.getElementById('last-updated').textContent = new Date().toLocaleTimeString();
                // Log on first load or significant count change
                if (_lastBusCount === 0 || Math.abs(latestBusData.length - _lastBusCount) > 20) {
                    console.log(`fetchBuses: ${latestBusData.length} buses (was ${_lastBusCount})`);
                }
                _lastBusCount = latestBusData.length;
                // If route view is active, maintain dimming and refresh sidebar
                if (activeRouteLine) {
                    applyRouteViewDimming();
                    refreshRouteViewSidebar();
                }
            } catch (e) {
                console.error('fetchBuses failed:', e);
            }
        }

        // Debug: type busShapeState in console to see all tracked bus positions on shapes
        window._busShapeState = busShapeState;

        // Locality emoji mapping
        const LOCALITY_EMOJIS = {
            // Bristol neighbourhoods
            'clifton': '🏛️', 'redland': '🌳', 'cotham': '🏘️', 'montpelier': '🎨',
            'st pauls': '🥁', 'st paul': '🥁', 'easton': '🚲', 'st george': '🏞️',
            'redcliffe': '⛪', 'southville': '🎭', 'bedminster': '🛒', 'totterdown': '🌈',
            'knowle': '🏠', 'brislington': '🧱', 'fishponds': '🐟', 'horfield': '🏟️',
            'bishopston': '🏏', 'henleaze': '☕', 'westbury-on-trym': '🍦', 'westbury on trym': '🍦',
            'stoke bishop': '🎓', 'avonmouth': '🚢', 'shirehampton': '⚓',
            'lawrence weston': '🏗️', 'henbury': '🏰',
            // South Gloucestershire
            'kingswood': '👑', 'hanham': '🍻', 'longwell green': '📽️',
            'oldland common': '🚂', 'oldland': '🚂', 'bitton': '🌷', 'warmley': '🏭',
            'mangotsfield': '⚽', 'staple hill': '🛣️', 'downend': '🏏',
            'frenchay': '🏥', 'bradley stoke': '🏢', 'patchway': '✈️',
            'filton': '🛰️', 'stoke gifford': '🚆', 'winterbourne': '🦆',
            'frampton cotterell': '🌾', 'yate': '🛍️', 'chipping sodbury': '🏮',
            'thornbury': '🏰',
            // Area-level fallbacks
            'north somerset': '🌊', 'bath and north east somerset': '🏛️', 'bath': '🏛️',
            'south gloucestershire': '🌿', 'bristol': '🌉',
        };

        function getLocalityEmoji(text) {
            if (!text) return '';
            const lower = text.toLowerCase();
            // Direct match first
            if (LOCALITY_EMOJIS[lower]) return LOCALITY_EMOJIS[lower];
            // Partial match — check if any key appears within the text
            for (const [key, emoji] of Object.entries(LOCALITY_EMOJIS)) {
                if (lower.includes(key)) return emoji;
            }
            return '';
        }

        let allStops = [];
        async function fetchStops(retries = 5) {
            try {
                const res = await fetch('/api/stops');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const stops = (await res.json()).stops;
                if ((!stops || stops.length === 0) && retries > 0) {
                    console.warn(`fetchStops: got 0 stops, retrying in 3s (${retries} retries left)`);
                    setTimeout(() => fetchStops(retries - 1), 3000);
                    return;
                }
                allStops = stops;
                console.log(`Loaded ${allStops.length} stops for map`);
                updateStopMarkers(allStops);
            } catch (e) {
                console.error(`fetchStops failed (${retries} retries left):`, e);
                if (retries > 0) setTimeout(() => fetchStops(retries - 1), 3000);
                else console.error('fetchStops: all retries exhausted — stops will not display');
            }
        }

        // --- Collapsible sidebar ---
        let sidebarCollapsed = false;
        function toggleSidebar() {
            if (isMobile()) return;
            sidebarCollapsed = !sidebarCollapsed;
            const sidebar = document.getElementById('sidebar');
            const icon = document.getElementById('collapse-icon');
            // arrow points where the panel will GO when clicked:
            // expanded -> right (collapse), collapsed -> left (expand)
            if (sidebarCollapsed) {
                sidebar.style.width = '20px';
                icon.innerHTML = '<polyline points="15 18 9 12 15 6"/>'; // safe: static constant
            } else {
                sidebar.style.width = '420px';
                icon.innerHTML = '<polyline points="9 18 15 12 9 6"/>'; // safe: static constant
            }
            // Leaflet needs to know the map size changed
            setTimeout(() => map.invalidateSize(), 350);
        }

        // --- Stop search ---
        let searchStops = [];
        let searchHighlightIndex = -1;

        // --- Fleet (bus) search ---
        // Full fleet from /api/fleet (all 2,592ish vehicles, active or not).
        // Built once at startup. Cross-referenced against latestBusData on every
        // search to mark which vehicles are currently on the road.
        let fleetData = [];
        let fleetByCode = {};   // fleet_code (string) -> fleet entry
        let fleetByReg = {};    // reg plate (uppercase, no spaces) -> fleet entry

        async function fetchSearchStops(retries = 5) {
            try {
                const res = await fetch('/api/stops-with-locality');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                searchStops = (await res.json()).stops || [];
                if (searchStops.length === 0 && retries > 0) {
                    console.warn(`fetchSearchStops: got 0 stops, retrying in 3s (${retries} retries left)`);
                    setTimeout(() => fetchSearchStops(retries - 1), 3000);
                    return;
                }
                console.log(`Loaded ${searchStops.length} stops for search`);
            } catch (e) {
                console.error(`fetchSearchStops failed (${retries} retries left):`, e);
                if (retries > 0) setTimeout(() => fetchSearchStops(retries - 1), 3000);
                else console.error('fetchSearchStops: all retries exhausted — search will not work');
            }
        }

        async function fetchFleet(retries = 5) {
            try {
                const res = await fetch('/api/fleet');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                fleetData = (await res.json()).fleet || [];
                if (fleetData.length === 0 && retries > 0) {
                    console.warn(`fetchFleet: got 0 vehicles, retrying in 3s (${retries} retries left)`);
                    setTimeout(() => fetchFleet(retries - 1), 3000);
                    return;
                }
                // Build lookup tables for instant active-bus matching
                fleetByCode = {};
                fleetByReg = {};
                fleetData.forEach(v => {
                    if (v.fleet_code) fleetByCode[String(v.fleet_code)] = v;
                    if (v.reg) {
                        const r = v.reg.toUpperCase().replace(/\s+/g, '');
                        fleetByReg[r] = v;
                    }
                });
                console.log(`Loaded ${fleetData.length} vehicles for fleet search`);
            } catch (e) {
                console.error(`fetchFleet failed (${retries} retries left):`, e);
                if (retries > 0) setTimeout(() => fetchFleet(retries - 1), 3000);
                else console.error('fetchFleet: all retries exhausted — bus search will not work');
            }
        }

        // --- AI bus descriptions ---
        // Three sets keyed by fleet_code: in_service, depot, waiting.
        // Picked per-vehicle based on state in pickDescriptionFor().
        let busDescriptions = { in_service: {}, depot: {}, waiting: {} };

        async function fetchBusDescriptions(retries = 3) {
            try {
                const res = await fetch('/api/bus-descriptions');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                busDescriptions = {
                    in_service: data.in_service || {},
                    depot:      data.depot      || {},
                    waiting:    data.waiting    || {},
                };
                console.log(`Loaded bus descriptions: in-service=${Object.keys(busDescriptions.in_service).length}, depot=${Object.keys(busDescriptions.depot).length}, waiting=${Object.keys(busDescriptions.waiting).length}`);
            } catch (e) {
                console.warn(`fetchBusDescriptions failed (${retries} retries left):`, e);
                if (retries > 0) setTimeout(() => fetchBusDescriptions(retries - 1), 3000);
                // Non-fatal — descriptions are flavour text. Panel still works without them.
            }
        }

        // Pick the right AI description for a vehicle based on its current state.
        // Returns a string, or null if no description is available.
        function pickDescriptionFor(vehicle, activeBus) {
            if (!vehicle || !vehicle.fleet_code) return null;
            const code = String(vehicle.fleet_code);
            if (activeBus) {
                if (activeBus.waitingAtOrigin && busDescriptions.waiting[code]) {
                    return busDescriptions.waiting[code];
                }
                if (activeBus.eventType === 'depot' && busDescriptions.depot[code]) {
                    return busDescriptions.depot[code];
                }
            }
            // Fall back to the in-service description as flavour text
            return busDescriptions.in_service[code] || null;
        }

        function onStopSearchFocus() {
            onStopSearch(document.getElementById('stop-search').value);
        }

        function setSearchOpen(open) {
            const results = document.getElementById('stop-search-results');
            const input = document.getElementById('stop-search');
            results.style.display = open ? 'block' : 'none';
            input.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (!open) input.removeAttribute('aria-activedescendant');
        }

        function getFleetMatches(query, limit = 8) {
            return window.BBB.getFleetMatches(
                fleetData, latestBusData, query, limit);
        }

        function onStopSearch(query) {
            const resultsEl = document.getElementById('stop-search-results');
            query = (query || '').trim().toLowerCase();

            // Check for route matches first (only when query looks like a route number)
            const routeMatches = query.length > 0 ? getRouteMatches(query) : [];

            // Route searches deliberately show fewer bus matches so the route
            // answer remains prominent.
            const fleetLimit = window.BBB.fleetResultLimit(routeMatches);
            const fleetMatches = query.length > 0
                ? getFleetMatches(query, fleetLimit)
                : { matches: [], total: 0 };

            // Filter stops — show all if empty, otherwise filter
            let matches;
            if (query.length === 0) {
                matches = searchStops;
            } else {
                matches = searchStops.filter(s =>
                    s.stop_name.toLowerCase().includes(query) ||
                    s.stop_code.toLowerCase().includes(query) ||
                    (s.ward && s.ward.toLowerCase().includes(query)) ||
                    (s.routes && s.routes.some(r => r.toLowerCase() === query))
                );
            }

            // Grouping for display: area > ward, stops sorted by name
            const grouped = {};
            matches.forEach(st => {
                const area = st.area || 'Other';
                const ward = st.ward || 'Other';
                if (!grouped[area]) grouped[area] = {};
                if (!grouped[area][ward]) grouped[area][ward] = [];
                grouped[area][ward].push(st);
            });
            const stopGroups = [];
            for (const area of Object.keys(grouped).sort()) {
                for (const ward of Object.keys(grouped[area]).sort()) {
                    grouped[area][ward].sort((a, b) => a.stop_name.localeCompare(b.stop_name));
                    stopGroups.push({ area, ward, stops: grouped[area][ward] });
                }
            }
            // Render the grouped search results.
            window.BBB.renderSearchResults(resultsEl, {
                fleetMatches, routeMatches, stopGroups,
                highlightIndex: searchHighlightIndex,
                getLocalityEmoji,
                handlers: { selectFleetVehicle, selectSearchRoute, selectSearchStop },
            });
            setSearchOpen(true);
            document.getElementById('search-status').textContent =
                window.BBB.searchAnnouncement(
                    routeMatches.length, matches.length, fleetMatches.total);
        }

        function selectSearchStop(stopCode, lat, lon) {
            setSearchOpen(false);
            document.getElementById('stop-search').value = '';
            // Pan map to stop
            if (lat && lon) {
                map.flyTo([lat, lon], 16);
            }
            // Select stop after a brief delay to let map pan
            setTimeout(() => selectStop(stopCode), 300);
        }

        function onSearchKeydown(e) {
            const resultsEl = document.getElementById('stop-search-results');
            const items = resultsEl.querySelectorAll('.search-result');
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                setSearchOpen(false);
                searchHighlightIndex = -1;
                return;
            }
            if (!items.length) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                searchHighlightIndex = Math.min(searchHighlightIndex + 1, items.length - 1);
                updateSearchHighlight(items);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                searchHighlightIndex = Math.max(searchHighlightIndex - 1, 0);
                updateSearchHighlight(items);
            } else if (e.key === 'Enter' && searchHighlightIndex >= 0 && items[searchHighlightIndex]) {
                e.preventDefault();
                items[searchHighlightIndex].click();
            }
        }

        function updateSearchHighlight(items) {
            items.forEach((el, i) => {
                el.classList.toggle('sr-active', i === searchHighlightIndex);
                el.setAttribute(
                    'aria-selected', i === searchHighlightIndex ? 'true' : 'false');
            });
            if (items[searchHighlightIndex]) {
                items[searchHighlightIndex].scrollIntoView({ block: 'nearest' });
                document.getElementById('stop-search').setAttribute(
                    'aria-activedescendant', items[searchHighlightIndex].id);
            }
        }

        // Close search dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#stop-search') && !e.target.closest('#stop-search-results')) {
                setSearchOpen(false);
            }
        });

        // --- Click live departure to locate bus on map ---
        window.locateBus = function(vehicleRef) {
            if (!vehicleRef) return;
            const marker = busMarkers.get(vehicleRef);
            if (marker) {
                // Expand sidebar if collapsed
                if (sidebarCollapsed) toggleSidebar();
                map.flyTo(marker.getLatLng(), 16);
                setTimeout(() => marker.openPopup(), 600);
            }
        }

        // Fetch and render route shape polylines on the map
        async function fetchRouteShapes(retries = 5) {
            try {
                const res = await fetch('/api/route-shapes');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                if (Object.keys(data).length === 0 && retries > 0) {
                    console.warn(`fetchRouteShapes: got 0 shapes, retrying in 3s (${retries} retries left)`);
                    setTimeout(() => fetchRouteShapes(retries - 1), 3000);
                    return;
                }
                routeShapesData = data;
                // Pre-build polyline layers (not added to map until toggled)
                routeShapeLayers = [];
                Object.values(routeShapesData).forEach(shape => {
                    if (!shape.points || shape.points.length < 2) return;
                    routeShapeLayers.push(L.polyline(shape.points, {
                        color: '#7E8582',
                        weight: 1.5,
                        opacity: 0.25,
                        interactive: false
                    }));
                });
                buildRouteIndex();
                console.log(`Loaded ${Object.keys(routeShapesData).length} route shapes, ${Object.keys(routeIndex).length} searchable routes`);
            } catch (e) {
                console.error(`fetchRouteShapes failed (${retries} retries left):`, e);
                if (retries > 0) setTimeout(() => fetchRouteShapes(retries - 1), 3000);
                else console.error('fetchRouteShapes: all retries exhausted — route shapes will not display');
            }
        }

        // --- Busbot Bluesky posts ---
        async function fetchBusbotPosts() {
            try {
                const res = await fetch('/api/busbot-posts');
                if (!res.ok) return;
                const data = await res.json();
                const posts = data.posts || [];
                const newPosts = {};

                // Posts arrive with exact vehicleRef from the busbot API
                posts.forEach(post => {
                    if (!post.postUrl || !post.vehicleRef) return;
                    if (!newPosts[post.vehicleRef]) {
                        newPosts[post.vehicleRef] = {
                            postUrl: post.postUrl,
                            postText: post.postText,
                            timestamp: post.timestamp
                        };
                    }
                });

                busbotPosts = newPosts;
                const count = Object.keys(busbotPosts).length;
                if (count > 0) console.log(`Matched ${count} busbot posts to active buses`);
            } catch (e) {
                console.error('fetch busbot posts failed:', e);
            }
        }

        let polylinesVisible = false;
        function togglePolylines(show) {
            polylinesVisible = show;
            routeShapeLayers.forEach(pl => {
                if (show && !map.hasLayer(pl)) pl.addTo(map);
                else if (!show && map.hasLayer(pl)) map.removeLayer(pl);
            });
            updateToggleBtn('toggle-polylines-btn', show);
        }
        function togglePolylinesBtn() {
            togglePolylines(!polylinesVisible);
        }

        let boundaryVisible = true;
        function toggleBoundaryBtn() {
            boundaryVisible = !boundaryVisible;
            toggleBoundary(boundaryVisible);
            updateToggleBtn('toggle-boundary-btn', boundaryVisible);
        }

        function updateToggleBtn(id, active) {
            const btn = document.getElementById(id);
            if (!btn) return;
            if (active) {
                btn.style.border = '1.5px solid #794400';
                btn.style.background = '#794400';
                btn.style.color = '#fff';
                btn.classList.add('toggle-active');
            } else {
                btn.style.border = '1.5px solid rgba(121,68,0,0.35)';
                btn.style.background = 'transparent';
                btn.style.color = '#794400';
                btn.classList.remove('toggle-active');
            }
            // Sync mobile FAB
            const fabId = id === 'toggle-polylines-btn' ? 'fab-polylines' : 'fab-boundary';
            const fab = document.getElementById(fabId);
            if (fab) {
                if (active) {
                    fab.style.background = '#794400';
                    fab.style.color = '#fff';
                } else {
                    fab.style.background = '#ffffff';
                    fab.style.color = '#794400';
                }
            }
        }

        // --- Route viewer ---
        let activeRouteLayer = null;
        let activeRouteStopMarkers = [];
        let routeViewActive = false;
        let activeRouteVehicleRef = null;

        async function showBusRoute(vehicleRef, line, directionId, journeyCode, destination, liveryAccent, directionRef, originAimedDep, delayMinutes, eventType, operatorRef, tripId) {
            const busPos = busMarkers.get(vehicleRef)?.getLatLng();
            const routeColor = liveryAccent || '#7E8582';

            // Clear any previous route
            clearBusRoute(true);

            // Track focused bus and dim others
            activeRouteVehicleRef = vehicleRef;
            busMarkers.forEach((m, ref) => {
                m.getElement()?.style.setProperty('opacity', ref === vehicleRef ? '1' : '0.2');
                m.getElement()?.style.setProperty('transition', 'opacity 0.3s');
            });

            // Preserve the departure board while the route view is open.
            if (!routeViewActive) {
                window.BBB.saveBoard(document.getElementById('departures-list'));
            }
            routeViewActive = true;
            const stopHeader = document.getElementById('stop-header');
            const boardPrompt = document.getElementById('board-prompt');
            stopHeader.classList.add('hidden');
            boardPrompt.classList.add('hidden');

            // Build delay badge - use actual delay, not coarse eventType
            const delay = parseInt(delayMinutes) || 0;
            const isWaiting = eventType === 'waiting';
            const statusText = isWaiting ? `departs in ${Math.abs(delay)}m` :
                              delay === 0 ? 'on time' :
                              delay < 0 ? `${Math.abs(delay)}m early` :
                              `${delay}m late`;
            const statusColor = isWaiting ? '#60a5fa' :
                               delay === 0 ? '#00703C' :
                               delay < 0 ? '#eab308' : '#D4351C';

            // Fetch journey schedule first — we need stops to pick the right shape variant
            let scheduleStops = [];
            let scheduleData = null;
            if (journeyCode || tripId) {
                try {
                    let url = `/api/journey-schedule/${journeyCode}`;
                    const params = new URLSearchParams();
                    if (tripId) params.set('tripId', tripId);
                    if (operatorRef) params.set('operator', operatorRef);
                    if (line) params.set('line', line);
                    if (directionRef) params.set('directionRef', directionRef);
                    if (originAimedDep) params.set('originAimedDep', originAimedDep);
                    if (params.toString()) url += '?' + params.toString();
                    const res = await fetch(url);
                    if (res.ok) {
                        scheduleData = await res.json();
                        scheduleStops = (scheduleData.stops || []).filter(s => s.latitude && s.longitude);
                    } else {
                        console.warn(`loadJourneySchedule: HTTP ${res.status} for journey ${journeyCode} (line ${line})`);
                    }
                } catch (e) {
                    console.error(`Failed to load journey schedule for ${journeyCode} (line ${line}):`, e);
                }
            }

            // Pick shape variant using schedule stops (best match), falling back to bus position
            const shapeInfo = getShapeForBus(line, directionId, operatorRef, busPos?.lat, busPos?.lng, scheduleStops);

            if (shapeInfo) {
                activeRouteLayer = L.polyline(shapeInfo.points, {
                    color: routeColor,
                    weight: 4,
                    opacity: 0.6,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(map);
                map.fitBounds(activeRouteLayer.getBounds(), { padding: [40, 40] });
            } else if (scheduleStops.length >= 2) {
                // No shape geometry for this route (TNDS-recovered routes —
                // 42-45, 70, 74, AZ*, N43 — have timetables but no GTFS
                // shapes). Fall back to the scheduled stops, DASHED to say
                // 'approximate path', so the isolated route is always
                // visible on the map rather than just a scatter of dots.
                console.log(`showBusRoute: no shape for ${operatorRef} ${line} dir ${directionId} — drawing stop-path fallback (${scheduleStops.length} stops)`);
                activeRouteLayer = L.polyline(
                    scheduleStops.map(st => [st.latitude, st.longitude]), {
                    color: routeColor,
                    weight: 3,
                    opacity: 0.55,
                    dashArray: '7 7',
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(map);
                map.fitBounds(activeRouteLayer.getBounds(), { padding: [40, 40] });
            }

            // Render the route header and stop list.
            const rvWrap = window.BBB.journeyHeader(
                document.getElementById('departures-list'), {
                    line, destination, eventType,
                    delayMinutes,
                    routeColor,
                    hasShape: !!activeRouteLayer,
                    onClose: () => clearBusRoute(),
                });
            if (isMobile()) setSheetState('expanded');

            if (!scheduleData) {
                window.BBB.journeyNoSchedule(rvWrap);
                return;
            }

            try {
                const stops = scheduleData.stops || [];

                // Find current bus position to determine which stop it's at
                let currentStopIdx = -1;
                const busMarker = busMarkers.get(vehicleRef);
                if (busMarker) {
                    const busPos = busMarker.getLatLng();
                    let minDist = Infinity;
                    stops.forEach((s, i) => {
                        if (s.latitude && s.longitude) {
                            const dl = busPos.lat - s.latitude;
                            const dn = busPos.lng - s.longitude;
                            const d = dl*dl + dn*dn;
                            if (d < minDist) { minDist = d; currentStopIdx = i; }
                        }
                    });
                }

                // Add stop markers on map
                stops.forEach((s, i) => {
                    if (!s.latitude || !s.longitude) return;
                    const isCurrent = i === currentStopIdx;
                    const isPast = i < currentStopIdx;
                    const markerColor = isCurrent ? '#FFDD00' : isPast ? routeColor : '#555';
                    const size = isCurrent ? 8 : 6;
                    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="${size}" height="${size}">
                        <circle cx="5" cy="5" r="4" fill="${markerColor}" stroke="#111" stroke-width="1" opacity="${isPast ? 0.6 : 0.8}"/>
                    </svg>`;
                    const marker = L.marker([s.latitude, s.longitude], {
                        icon: L.divIcon({ html: svg, className: '', iconSize: [size, size], iconAnchor: [size/2, size/2] }),
                        zIndexOffset: 500,
                        interactive: false
                    }).addTo(map);
                    activeRouteStopMarkers.push(marker);
                });

                // Group stops by ward
                const groups = [];
                let currentGroup = null;
                stops.forEach((s, i) => {
                    const ward = s.ward || 'Other';
                    if (!currentGroup || currentGroup.ward !== ward) {
                        currentGroup = { ward, stops: [] };
                        groups.push(currentGroup);
                    }
                    currentGroup.stops.push({ ...s, idx: i });
                });

                // Format time (GTFS HH:MM:SS → HH:MM, handle >24h)
                function fmtTime(t) {
                    if (!t) return '';
                    const parts = t.split(':');
                    let h = parseInt(parts[0]);
                    if (h >= 24) h -= 24;
                    return `${String(h).padStart(2,'0')}:${parts[1]}`;
                }

                // Add delay minutes to a GTFS time string, return formatted HH:MM
                function fmtTimeWithDelay(t, delayMins) {
                    if (!t) return '';
                    const parts = t.split(':');
                    let h = parseInt(parts[0]);
                    let m = parseInt(parts[1]);
                    if (h >= 24) h -= 24;
                    m += delayMins;
                    while (m >= 60) { m -= 60; h++; }
                    while (m < 0) { m += 60; h--; }
                    if (h >= 24) h -= 24;
                    if (h < 0) h += 24;
                    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
                }

                const busDelay = parseInt(delayMinutes) || 0;
                const isWaitingRoute = eventType === 'waiting';
                const hasDelay = busDelay !== 0 && !isWaitingRoute;

                window.BBB.journeyStops(rvWrap, {
                    groups, currentStopIdx, busDelay, hasDelay, routeColor,
                    fmtTime, fmtTimeWithDelay,
                    flyTo: (lat, lon) => map.flyTo([lat, lon], 17),
                });

            } catch (e) {
                console.error(`Failed to load journey schedule for ${journeyCode} (line ${line}):`, e);
            }
        }


        function clearBusRoute(keepSidebar) {
            // Also clear route search view if active
            if (activeRouteLine) clearRouteView(true);

            if (activeRouteLayer) {
                map.removeLayer(activeRouteLayer);
                activeRouteLayer = null;
            }
            activeRouteStopMarkers.forEach(m => map.removeLayer(m));
            activeRouteStopMarkers = [];

            // Restore all bus marker opacity
            if (activeRouteVehicleRef) {
                busMarkers.forEach((m) => {
                    const el = m.getElement();
                    if (el) { el.style.opacity = '1'; el.style.transition = 'opacity 0.3s'; }
                });
                activeRouteVehicleRef = null;
            }

            if (!keepSidebar && routeViewActive) {
                routeViewActive = false;
                window.BBB.restoreBoard(document.getElementById('departures-list'));
                // Restore the appropriate sidebar state
                if (selectedStopCode) {
                    document.getElementById('stop-header').classList.remove('hidden');
                    document.getElementById('board-prompt').classList.add('hidden');
                } else {
                    document.getElementById('stop-header').classList.add('hidden');
                    document.getElementById('board-prompt').classList.remove('hidden');
                }
                window.BBB.dropSavedBoard();
            }
        }

        // --- Vehicle detail panel ---
        // Find the active bus (from latestBusData) that matches a fleet entry,
        // matching either by fleet number or by reg plate. Returns null if not live.
        function findActiveBusForVehicle(v) {
            if (!Array.isArray(latestBusData) || !v) return null;
            const code = v.fleet_code ? String(v.fleet_code) : '';
            const regKey = v.reg ? v.reg.toUpperCase().replace(/\s+/g, '') : '';
            return latestBusData.find(b => {
                if (b.fleetNumber && code && String(b.fleetNumber) === code) return true;
                if (b.reg && regKey && String(b.reg).toUpperCase().replace(/\s+/g, '') === regKey) return true;
                return false;
            }) || null;
        }

        // Show the vehicle detail panel for a fleet entry by id
        function selectFleetVehicle(id) {
            const v = fleetData.find(x => x.id === id);
            if (!v) {
                console.warn('selectFleetVehicle: no vehicle with id', id);
                return;
            }
            // one-layer rule: modal replaces every other surface
            setSearchOpen(false);
            if (map) map.closePopup();
            if (isMobile() && sheetState !== 'collapsed') setSheetState('collapsed');

            const activeBus = findActiveBusForVehicle(v);
            const isLive = !!activeBus;

            const content = document.getElementById('vehicle-panel-content');
            window.BBB.renderVehiclePanel(content, v, isLive, activeBus,
                                          pickDescriptionFor(v, activeBus));

            // Show backdrop (centered flex)
            const backdrop = document.getElementById('vehicle-panel-backdrop');
            backdrop.style.display = 'flex';
        }

        function closeVehiclePanel(e) {
            // If invoked from the backdrop click handler, only close when click was on the backdrop itself
            if (e && e.target && e.target.id !== 'vehicle-panel-backdrop' && e.type === 'click') return;
            const backdrop = document.getElementById('vehicle-panel-backdrop');
            if (backdrop) backdrop.style.display = 'none';
        }

        // Track a fleet vehicle on the map: zoom to its current position and pop up its info
        function trackVehicleOnMap(id) {
            const v = fleetData.find(x => x.id === id);
            if (!v) {
                console.warn('trackVehicleOnMap: no vehicle with id', id);
                return;
            }
            const activeBus = findActiveBusForVehicle(v);
            if (!activeBus) {
                console.warn('trackVehicleOnMap: vehicle is not currently active', v.fleet_code, v.reg);
                // Defensive: shouldn't be reachable since the button only renders when live
                return;
            }
            const marker = busMarkers.get(activeBus.vehicleRef);
            if (!marker) {
                console.warn('trackVehicleOnMap: no marker found for', activeBus.vehicleRef);
                return;
            }
            // Close the panel and fly to the bus
            closeVehiclePanel();
            // Clear any active stop or route view so the popup isn't dimmed
            if (typeof clearRouteView === 'function' && activeRouteLine) {
                clearRouteView();
            }
            const lat = activeBus.lat;
            const lon = activeBus.lon;
            if (typeof lat === 'number' && typeof lon === 'number') {
                map.flyTo([lat, lon], 16, { duration: 0.8 });
                // Wait for the fly to settle before opening the popup
                setTimeout(() => {
                    try { marker.openPopup(); } catch (e) { console.warn('openPopup failed', e); }
                }, 850);
            } else {
                // No position — just open the popup
                try { marker.openPopup(); } catch (e) { console.warn('openPopup failed', e); }
            }
        }

        // ESC closes the vehicle panel (only if it's open)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const backdrop = document.getElementById('vehicle-panel-backdrop');
                if (backdrop && backdrop.style.display === 'flex') {
                    closeVehiclePanel();
                }
            }
        });

        // --- Operator display names ---
        const OPERATOR_NAMES = {
            'FBRI': 'First Bus', 'BFBC': 'First Bus',
            'SSWL': 'Stagecoach', 'SDVN': 'Stagecoach SW', 'SCGL': 'Stagecoach West',
            'NATX': 'National Express', 'KEMT': 'Kempsford', 'VITR': 'Kempsford',
            'FSRV': 'Faresaver', 'NWPT': 'Newport Bus', 'ABUS': 'ABUS',
            'BDOL': 'Bakers Dolphin', 'CTCO': 'CT Coaches', 'FRMN': 'FromeBus',
            'TDTR': 'Thamesdown', 'EZMT': 'WESTlink', 'PULH': 'Pulhams',
            'LEMB': 'Big Lemon',
        };

        // Build route index from shapes data: "OPERATOR_line" -> [variants]
        function buildRouteIndex() {
            routeIndex = {};
            for (const [key, shape] of Object.entries(routeShapesData)) {
                if (!shape.points || shape.points.length < 2) continue;
                const routeKey = `${shape.operator}_${shape.route}`;
                if (!routeIndex[routeKey]) routeIndex[routeKey] = [];
                routeIndex[routeKey].push({ key, operator: shape.operator, route: shape.route, direction: shape.direction, points: shape.points });
            }
        }

        // Get route matches for search query
        function getRouteMatches(query) {
            return window.BBB.getRouteMatches(
                routeIndex, latestBusData, query, OPERATOR_NAMES);
        }

        async function loadTimetableRouteVariants(routeKey) {
            const candidates = window.BBB.routeFallbackCandidates(
                latestBusData, routeKey);
            const { operator, line } = (() => {
                const parts = routeKey.split('_');
                return {
                    operator: parts[0],
                    line: parts.slice(1).join('_'),
                };
            })();
            const results = await Promise.all(candidates.map(async (bus, idx) => {
                try {
                    const code = encodeURIComponent(
                        bus.journeyCode || `route-search-${line}`);
                    const params = new URLSearchParams();
                    if (bus.tripId) params.set('tripId', bus.tripId);
                    params.set('operator', operator);
                    params.set('line', line);
                    if (bus.directionRef)
                        params.set('directionRef', bus.directionRef);
                    const response = await fetch(
                        `/api/journey-schedule/${code}?${params.toString()}`);
                    if (!response.ok) return null;
                    const data = await response.json();
                    const points = (data.stops || [])
                        .filter(stop => stop.latitude && stop.longitude)
                        .map(stop => [stop.latitude, stop.longitude]);
                    if (points.length < 2) return null;
                    return {
                        key: `timetable_${routeKey}_${idx}`,
                        operator,
                        route: line,
                        direction: bus.directionId ?? idx,
                        points,
                        approximate: true,
                    };
                } catch (error) {
                    console.warn(
                        `Could not build timetable path for ${routeKey}:`,
                        error);
                    return null;
                }
            }));

            const unique = [];
            const seen = new Set();
            for (const variant of results.filter(Boolean)) {
                const signature = variant.points
                    .map(point => point.join(',')).join(';');
                if (seen.has(signature)) continue;
                seen.add(signature);
                unique.push(variant);
            }
            return unique;
        }

        // Select a route from search
        async function selectSearchRoute(routeKey) {
            setSearchOpen(false);
            document.getElementById('stop-search').value = '';

            // Clear any existing route/bus view
            clearBusRoute(true);
            clearRouteView(true);

            let variants = routeIndex[routeKey] || [];
            const activeBuses = latestBusData.filter(
                bus => window.BBB.isBusOnRoute(bus, routeKey));
            if (!variants.length && !activeBuses.length) return;

            activeRouteLine = routeKey;
            activeRoutePathLoading = !variants.length;

            // Show the live buses immediately while a missing TNDS route path
            // is reconstructed from representative matched journeys.
            applyRouteViewDimming();
            buildRouteViewSidebar();
            const mobile = isMobile();
            if (mobile)
                setSheetState(window.BBB.routeSelectionSheetState(true));

            if (!variants.length) {
                variants = await loadTimetableRouteVariants(routeKey);
                if (activeRouteLine !== routeKey) return;
                activeRoutePathLoading = false;
                if (variants.length)
                    routeIndex[routeKey] = variants;
                buildRouteViewSidebar();
            }

            // Draw all direction variants on map
            const allBounds = [];
            variants.forEach(v => {
                const color = '#1D70B8';
                const layer = L.polyline(v.points, {
                    color,
                    weight: v.approximate ? 3 : 4,
                    opacity: v.approximate ? 0.55 : 0.6,
                    dashArray: v.approximate ? '7 7' : null,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(map);
                activeRouteLineLayers.push(layer);
                allBounds.push(...v.points);
            });

            // If no scheduled path could be recovered, still frame the live
            // buses instead of making the route result appear to do nothing.
            if (!allBounds.length) {
                activeBuses.forEach(bus => {
                    if (bus.latitude && bus.longitude)
                        allBounds.push([bus.latitude, bus.longitude]);
                });
            }

            // Fit after choosing the sheet state, reserving enough map space
            // for the visible mobile peek so the highlighted route stays seen.
            if (allBounds.length > 0) {
                map.fitBounds(
                    L.latLngBounds(allBounds.map(p => [p[0], p[1]])),
                    window.BBB.routeFitOptions(mobile, window.innerHeight));
            }
        }

        function applyRouteViewDimming() {
            if (!activeRouteLine) return;

            activeRouteVehicleRefs = [];
            busMarkers.forEach((m, ref) => {
                const bus = latestBusData.find(b => b.vehicleRef === ref);
                const isMatch = window.BBB.isBusOnRoute(bus, activeRouteLine);
                const el = m.getElement();
                if (el) {
                    el.style.opacity = isMatch ? '1' : '0.15';
                    el.style.transition = 'opacity 0.3s';
                }
                if (isMatch) activeRouteVehicleRefs.push(ref);
            });
        }

        function buildRouteViewSidebar() {
            if (!activeRouteLine) return;
            const parts = activeRouteLine.split('_');
            const operator = parts[0];
            const line = parts.slice(1).join('_');
            const variants = routeIndex[activeRouteLine] || [];
            const operatorName = OPERATOR_NAMES[operator] || operator;

            // Get matching active buses
            const buses = latestBusData.filter(b =>
                window.BBB.isBusOnRoute(b, activeRouteLine)
            );

            // Save current sidebar if not already in route view
            if (!routeViewActive) {
                window.BBB.saveBoard(document.getElementById('departures-list'));
            }
            routeViewActive = true;
            document.getElementById('stop-header').classList.add('hidden');
            document.getElementById('board-prompt').classList.add('hidden');

            // Render the route-search sidebar.
            window.BBB.routeSearchView(document.getElementById('departures-list'), {
                line,
                operatorName,
                variants: variants.length,
                approximate: variants.some(variant => variant.approximate),
                pathLoading: activeRoutePathLoading,
                buses,
                onClose: () => clearRouteView(),
                locateBus,
            });
        }

        function refreshRouteViewSidebar() {
            if (!activeRouteLine) return;
            applyRouteViewDimming();
            buildRouteViewSidebar();
        }

        function clearRouteView(keepSidebar) {
            // Remove route polylines
            activeRouteLineLayers.forEach(l => map.removeLayer(l));
            activeRouteLineLayers = [];
            activeRouteLine = null;
            activeRoutePathLoading = false;

            // Restore all bus marker opacity
            if (activeRouteVehicleRefs.length > 0) {
                busMarkers.forEach((m) => {
                    const el = m.getElement();
                    if (el) { el.style.opacity = '1'; el.style.transition = 'opacity 0.3s'; }
                });
                activeRouteVehicleRefs = [];
            }

            if (!keepSidebar && routeViewActive) {
                routeViewActive = false;
                window.BBB.restoreBoard(document.getElementById('departures-list'));
                if (selectedStopCode) {
                    document.getElementById('stop-header').classList.remove('hidden');
                    document.getElementById('board-prompt').classList.add('hidden');
                } else {
                    document.getElementById('stop-header').classList.add('hidden');
                    document.getElementById('board-prompt').classList.remove('hidden');
                }
                window.BBB.dropSavedBoard();
            }
        }

        // --- Mobile bottom sheet ---
        let sheetState = 'collapsed'; // 'collapsed' | 'peek' | 'expanded'

        function isMobile() {
            return window.matchMedia('(max-width: 768px)').matches;
        }

        function setSheetState(state) {
            if (!isMobile()) return;
            sheetState = state;
            if (state !== 'collapsed' && map) map.closePopup(); // one-layer rule
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.remove('sheet-peek', 'sheet-expanded');
            if (state === 'peek') sidebar.classList.add('sheet-peek');
            else if (state === 'expanded') sidebar.classList.add('sheet-expanded');
            setTimeout(() => map.invalidateSize(), 350);
        }

        function initSheetDrag() {
            const handle = document.getElementById('sheet-handle');
            if (!handle) return;
            let touchStartY = 0;
            let touchEndY = 0;

            handle.addEventListener('touchstart', (e) => {
                touchStartY = e.touches[0].clientY;
                touchEndY = touchStartY;
            }, { passive: true });

            handle.addEventListener('touchmove', (e) => {
                touchEndY = e.touches[0].clientY;
            }, { passive: true });

            handle.addEventListener('touchend', () => {
                const dy = touchEndY - touchStartY;
                if (dy < -40) {
                    // Swiped UP
                    if (sheetState === 'collapsed') setSheetState('peek');
                    else if (sheetState === 'peek') setSheetState('expanded');
                } else if (dy > 40) {
                    // Swiped DOWN
                    if (sheetState === 'expanded') setSheetState('peek');
                    else if (sheetState === 'peek') setSheetState('collapsed');
                }
            });

            // Also allow click to cycle: collapsed→peek, peek→expanded
            handle.addEventListener('click', () => {
                if (!isMobile()) return;
                if (sheetState === 'collapsed') setSheetState('peek');
                else if (sheetState === 'peek') setSheetState('expanded');
                else setSheetState('collapsed');
            });
        }

        // --- Geolocation ---
        let userLocationMarker = null;

        function geolocateUser() {
            if (!navigator.geolocation || !window.isSecureContext) {
                // browsers disable the location API on plain http:// hosts
                // (e.g. LAN-IP testing); it works on localhost and https
                alert(window.isSecureContext
                    ? 'Location is not supported by this browser'
                    : 'Location needs a secure (https) connection \u2014 it will work on the live site');
                return;
            }
            const btn = document.getElementById('geolocate-btn');
            btn.style.color = '#b48800';
            btn.dataset.activeColor = '#b48800';

            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    const { latitude, longitude } = pos.coords;
                    if (userLocationMarker) map.removeLayer(userLocationMarker);

                    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
                        <circle cx="12" cy="12" r="10" fill="#1D70B8" opacity="0.2" stroke="#1D70B8" stroke-width="2"/>
                        <circle cx="12" cy="12" r="5" fill="#1D70B8"/>
                    </svg>`;
                    userLocationMarker = L.marker([latitude, longitude], {
                        icon: L.divIcon({ html: svg, className: '', iconSize: [24, 24], iconAnchor: [12, 12] }),
                        zIndexOffset: 2000
                    }).addTo(map);

                    map.flyTo([latitude, longitude], 16);
                    btn.style.color = '#1D70B8';
                    btn.dataset.activeColor = '#1D70B8';

                    // Find nearest stop within 500m
                    if (allStops.length) {
                        let nearest = null, minDist = Infinity;
                        allStops.forEach(s => {
                            const dl = latitude - s.latitude;
                            const dn = longitude - s.longitude;
                            const d = dl * dl + dn * dn;
                            if (d < minDist) { minDist = d; nearest = s; }
                        });
                        if (nearest && Math.sqrt(minDist) * 111000 < 500) {
                            setTimeout(() => selectStop(nearest.stop_code), 800);
                        }
                    }
                },
                (err) => {
                    console.error('Geolocation error:', err);
                    btn.style.color = '#D4351C';
                    btn.dataset.activeColor = '#D4351C';
                    setTimeout(() => { btn.style.color = '#5b6672'; btn.dataset.activeColor = ''; }, 3000);
                },
                { enableHighAccuracy: true, timeout: 10000 }
            );
        }

        function wireStaticControls() {
            const on = (id, event, handler) => {
                const element = document.getElementById(id);
                if (element) element.addEventListener(event, handler);
            };

            on('toggle-polylines-btn', 'click', togglePolylinesBtn);
            on('fab-polylines', 'click', togglePolylinesBtn);
            on('toggle-boundary-btn', 'click', toggleBoundaryBtn);
            on('fab-boundary', 'click', toggleBoundaryBtn);
            on('geolocate-btn', 'click', geolocateUser);
            on('sidebar-toggle', 'click', toggleSidebar);

            const search = document.getElementById('stop-search');
            if (search) {
                search.addEventListener('input', () => {
                    searchHighlightIndex = -1;
                    search.removeAttribute('aria-activedescendant');
                    onStopSearch(search.value);
                });
                search.addEventListener('focus', onStopSearchFocus);
                search.addEventListener('keydown', onSearchKeydown);
            }
            const searchResults = document.getElementById('stop-search-results');
            if (searchResults) {
                searchResults.addEventListener('keydown', (event) => {
                    if (event.key !== 'Escape') return;
                    event.preventDefault();
                    event.stopPropagation();
                    searchHighlightIndex = -1;
                    setSearchOpen(false);
                    search?.focus();
                });
            }

            const stopHeader = document.getElementById('stop-header-sign');
            if (stopHeader) {
                stopHeader.addEventListener('click', window.flyToSelectedStop);
                stopHeader.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        window.flyToSelectedStop();
                    }
                });
            }

            on('vehicle-panel-backdrop', 'click', closeVehiclePanel);
            on('vehicle-panel', 'click', (event) => event.stopPropagation());
            on('vehicle-panel-close', 'click', () => closeVehiclePanel());

            const logo = document.getElementById('logo-sign');
            if (logo) {
                const border = logo.querySelector('#logo-border');
                logo.addEventListener('mouseenter', () => border?.setAttribute('stroke', '#1b2027'));
                logo.addEventListener('mouseleave', () => border?.setAttribute('stroke', '#7E8582'));
            }
            const bsky = document.getElementById('bsky-link');
            if (bsky) {
                bsky.addEventListener('mouseenter', () => { bsky.style.color = '#0085ff'; });
                bsky.addEventListener('mouseleave', () => { bsky.style.color = '#0069c9'; });
            }
            const geolocate = document.getElementById('geolocate-btn');
            if (geolocate) {
                geolocate.addEventListener('mouseenter', () => { geolocate.style.color = '#1b2027'; });
                geolocate.addEventListener('mouseleave', () => {
                    geolocate.style.color = geolocate.dataset.activeColor || '#5b6672';
                });
            }
            const close = document.getElementById('vehicle-panel-close');
            if (close) {
                close.addEventListener('mouseenter', () => {
                    close.style.background = 'rgba(0,0,0,0.45)';
                    close.style.color = '#fff';
                });
                close.addEventListener('mouseleave', () => {
                    close.style.background = 'rgba(0,0,0,0.28)';
                    close.style.color = 'rgba(255,255,255,0.75)';
                });
            }
        }

        // init
        console.log('Bristol Live Buses: initialising...');
        const _initStart = performance.now();
        wireStaticControls();
        initMap();
        fetchBoundary();
        fetchRouteShapes();
        fetchBuses();
        fetchStops();
        fetchSearchStops();
        fetchFleet();
        fetchBusDescriptions();
        fetchBusbotPosts();
        map.on('zoomend moveend', () => updateStopMarkers(allStops));
        refreshInterval = setInterval(fetchBuses, 15000);
        setInterval(fetchBusbotPosts, 120000);  // Refresh busbot posts every 2 minutes
        initSheetDrag();
        console.log(`Init dispatched in ${(performance.now() - _initStart).toFixed(0)}ms (data loading async)`);

        // ONE-LAYER RULE (mobile): only one surface above the map, ever.
        // Opening a popup collapses the sheet; raising the sheet closes
        // popups; the modal (z 5000) closes both when it opens.
        map.on('popupopen', () => {
            if (isMobile() && sheetState !== 'collapsed') setSheetState('collapsed');
        });

        // Mobile: collapse sheet when tapping map
        map.on('click', () => {
            // Clear any active route overlay when clicking the map
            if (activeRouteLine) { clearRouteView(); }
            else if (routeViewActive || activeRouteLayer) { clearBusRoute(); }
            if (isMobile() && sheetState !== 'collapsed') {
                setSheetState('collapsed');
            }
        });

        // Escape key clears active route or route view
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeVehiclePanel();
                if (activeRouteLine) { clearRouteView(); return; }
                if (routeViewActive || activeRouteLayer) { clearBusRoute(); }
            }
        });

        // Mobile: expand sheet when focusing search
        document.getElementById('stop-search').addEventListener('focus', () => {
            if (isMobile()) setSheetState('expanded');
        });

        // Handle resize/orientation changes
        window.addEventListener('resize', () => {
            setTimeout(() => map.invalidateSize(), 100);
            if (!isMobile()) {
                const sidebar = document.getElementById('sidebar');
                sidebar.classList.remove('sheet-peek', 'sheet-expanded');
                sheetState = 'collapsed';
            }
        });
