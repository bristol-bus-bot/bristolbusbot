"""Shared cache policy for the busiest public endpoints.

Cloudflare may hold a successful answer for 10 seconds (s-maxage), so many
visitors looking at the same thing cost the Pi one request; browsers still
revalidate every time (max-age=0), so the map behaves as before. It only
takes effect where a Cloudflare cache rule makes these paths eligible.
"""
EDGE_CACHE_10S = "public, max-age=0, s-maxage=10"
