"""
Generate cute one-liner descriptions for buses waiting at their first stop
before their scheduled departure time, using the Bristol Bus Bot persona
via Gemini 3 Pro.

These appear when a bus is sitting at its origin stop, engine running,
ready to go but the clock hasn't ticked over yet. Different vibe from
depot (sleeping) or in-service (working) descriptions.

Outputs waiting-descriptions.json keyed by fleet_code.

Usage: python generate_waiting_descriptions.py
"""

import json
import os
import time
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional; a real environment variable also works

# Read the Gemini key from the environment.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise SystemExit(f"{__file__}: GEMINI_API_KEY not set. Add it to .env next to this script.")
# The model can be overridden through the environment.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

FLEET_PATH = os.path.join(os.path.dirname(__file__), "fbribuses.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "waiting-descriptions.json")

BATCH_SIZE = 80

MODEL_BLURBS = {
    "Volvo B9TL Wright Eclipse Gemini": "Mid-2000s double-decker, curved front, spacious low-floor. Iconic UK workhorse.",
    "Scania N280UD ADL Enviro400 City CBG": "Compressed biogas double-decker, 280bhp, quiet and low-emission.",
    "ADL Enviro400 MMC": "2014 'Major Model Change' double-decker. Up to 100 passengers. Diesel, gas, hybrid or electric options.",
    "Yutong U11DD": "Battery-electric double-decker, 220kW motor, ~500km range. Chinese-built for UK market.",
    "Wright StreetDeck Ultroliner": "Wrightbus integral double-decker with micro-hybrid braking energy recovery.",
    "ADL Enviro400": "Original 2005-2018 low-floor double-decker. Set the benchmark. Cummins engines.",
    "Scania N250UD ADL Enviro400 MMC": "Scania 250bhp chassis with Enviro400 MMC body. Some have coach-style interiors and tables.",
    "ADL Enviro200 MMC": "2014+ single-decker midibus, 8.9-11.8m long. Diesel or electric. Some used in autonomous trials.",
    "Volvo B7RLE Wright Eclipse 2": "2008 single-decker with modernised styling on Volvo low-entry chassis.",
    "Wright StreetLite DF": "Compact 10m midibus from 2010. Door-forward layout, 33-45 seats. Known for vibration.",
    "ADL Enviro200": "First-gen midibus 2003-2018. Replaced the Dennis Dart SLF. 8.9-11.8m.",
    "Volvo B7RLE Wright Eclipse Urban": "2003 single-decker, Wrightbus body on Volvo chassis. Reliable workhorse.",
    "Volvo B9TL Wright Eclipse Gemini 2": "2008 double-decker update with new panels. Available on hybrid B5LH chassis too.",
    "Optare Solo SR": "Sleek low-floor midibus, 7.2-9.6m. Curved side glazing. Diesel, hybrid or electric.",
    "Yutong E12": "Chinese-built 12m electric single-decker. 215kW motor, ~370km range, 39 seats. Air suspension.",
    "ADL Enviro400 City": "Premium double-decker with glass staircase and restyled front. Launched 2015.",
    "Volvo B9R Plaxton Panther": "High-floor touring coach, ~53 seats. Built for long-distance express work.",
    "Volvo B7TL Wright Eclipse Gemini": "2001 original Gemini double-decker. Sweeping front end, popular with London operators.",
    "VDL DB300 Wright Gemini 2 HEV": "Rare 2007-08 diesel-electric hybrid double-decker. Only a handful built.",
    "Wright StreetDeck Electroliner": "Battery-electric double-decker. 454kWh battery, up to 200 miles range. CCS2 rapid charging.",
    "ADL Enviro100EV": "Tiny 8.5m battery-electric midibus from 2024. 45 passengers, up to 285 miles range.",
    "Yutong E9L": "9.5m electric midibus. 120kW motor, narrow 2.42m width. Good for tight routes.",
    "Scania N230UD OmniCity": "2006 integral double-decker. 230bhp, 9-litre engine. Ethanol fuel option available.",
    "Volvo B5TL MCV EvoSeti": "2015 Euro 6 double-decker. 1000kg lighter than predecessor. 75-83 seats.",
}

SYSTEM_PROMPT = """You are the Bristol Bus Bot. You document Bristol's privatised bus network with dry British wit and cold fury underneath. Public transport should serve the public good. Electric buses are progress. Bristol gets FirstBus PLC: profit extraction over service, £140m to shareholders while fares rise.

These descriptions appear on a live bus map when a bus is waiting at its first stop before departure — not sleeping at depot, not in service. It's sitting at the origin, engine on, doors open, watching the clock.

Your task: write a SHORT one-liner (max 15 words) for each bus. These show when the bus is waiting to depart.

CRITICAL RULES:
- Max 15 words each. Shorter is better. Aim for 8-12 words.
- ALWAYS reference what type of bus it is — the model matters. Is it a tiny midibus idling nervously? A massive double-decker blocking the view? A Chinese-built electric sitting in judgmental silence? USE this.
- DO NOT default to livery/branding jokes. Only mention livery if genuinely absurd.
- Reference specific technical details when they're funny (glass staircase, biogas, coach-style interiors on a city bus, vibration-prone StreetLites rattling before they even move)
- Electric buses: sitting in smug silence. not even idling. just existing, quietly.
- Ancient diesels: rattling at the stop. engine grumbling. warming up for another thankless shift.
- Biogas buses: the quiet fermenting before the storm
- Coaches doing city routes: the indignity of waiting at a bus stop
- The "almost time" moment: driver's coffee, the first passenger, the timetable breathing down its neck
- No emojis. No hashtags. British spelling.
- Don't be repetitive — each description must take a different angle

You will receive bus details including a MODEL CONTEXT field with technical info about that bus type. Use this knowledge to make specific, informed observations rather than generic comments.

Examples of the right tone:
- "Yutong E12. silent at the terminus. judging the diesel behind it."
- "StreetLite DF. already vibrating. hasn't even left the stop."
- "Enviro400 City. glass staircase gleaming. passengers already climbing it wrong."
- "2007 Gemini. idling. engine sounds like it's been doing this since 2007."
- "biogas Scania waiting to depart. the methane is ready if you are."
- "Plaxton Panther at a bus stop. dignity: none remaining."
- "Enviro200. doors open. heater on. driver finishing a Greggs. standard."
- "electric double-decker. sitting in silence. unnerving the queue."

Return a JSON object mapping each fleet_code to its waiting description. ONLY valid JSON, no markdown, no code fences."""


def load_fleet():
    with open(FLEET_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("Expected fleet data to be a JSON array")
    buses = [b for b in raw if not b.get('withdrawn', False)]
    print(f"Loaded {len(buses)} active buses from fleet data")
    return buses


def summarise_bus(bus):
    """Create a compact summary of a bus for the prompt."""
    vtype = bus.get('vehicle_type', {}) or {}
    livery = bus.get('livery', {}) or {}
    garage = bus.get('garage', {}) or {}
    features = bus.get('special_features') or []
    model_name = vtype.get('name', 'Unknown')

    parts = []
    parts.append(f"fleet_code: {bus.get('fleet_code', '?')}")
    parts.append(f"MODEL: {model_name}")

    blurb = MODEL_BLURBS.get(model_name)
    if blurb:
        parts.append(f"MODEL CONTEXT: {blurb}")

    type_tags = []
    if vtype.get('electric'):
        type_tags.append("ELECTRIC")
    if vtype.get('double_decker'):
        type_tags.append("double-decker")
    elif not vtype.get('coach'):
        type_tags.append("single-decker")
    if vtype.get('coach'):
        type_tags.append("coach")
    if vtype.get('fuel'):
        type_tags.append(f"fuel: {vtype['fuel']}")
    if type_tags:
        parts.append(" | ".join(type_tags))

    if garage.get('name'):
        parts.append(f"depot: {garage['name']}")
    if features:
        parts.append(f"features: {', '.join(features)}")

    return " | ".join(parts)


def call_gemini(bus_summaries, batch_label=""):
    """Send a batch of bus summaries to Gemini and get descriptions back."""
    prompt = f"""Here are the buses. Write a dry, model-specific one-liner for each — they're sitting at their first stop, waiting for departure time.

{json.dumps(bus_summaries, indent=None)}

Return a JSON object mapping fleet_code to waiting description. ONLY JSON, nothing else."""

    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 1.0,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json"
        }
    }

    prompt_chars = len(json.dumps(bus_summaries))
    print(f"  [{batch_label}] Sending {len(bus_summaries)} buses ({prompt_chars:,} chars) to Gemini...")
    start = time.time()

    resp = requests.post(GEMINI_URL, json=payload, timeout=120)
    elapsed = time.time() - start
    print(f"  [{batch_label}] Response received in {elapsed:.1f}s (HTTP {resp.status_code})")
    resp.raise_for_status()

    data = resp.json()
    candidate = data['candidates'][0]
    finish_reason = candidate.get('finishReason', 'unknown')
    usage = data.get('usageMetadata', {})
    prompt_tokens = usage.get('promptTokenCount', '?')
    output_tokens = usage.get('candidatesTokenCount', '?')
    print(f"  [{batch_label}] Finish: {finish_reason} | Tokens: {prompt_tokens} in / {output_tokens} out")

    text = candidate['content']['parts'][0]['text']
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        brace_depth = 0
        in_string = False
        escape_next = False
        end_idx = None

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    end_idx = i + 1
                    break

        if end_idx:
            truncated = text[:end_idx]
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass

        fixed = text.rstrip()
        if not fixed.endswith('}'):
            fixed = fixed.rstrip(',') + '"}'
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass

        raise


def process_batch(batch_num, total_batches, batch_summaries):
    label = f"Batch {batch_num}/{total_batches}"
    print(f"\n--- {label} ({len(batch_summaries)} buses) ---")
    try:
        descriptions = call_gemini(batch_summaries, batch_label=label)
        print(f"  [{label}] SUCCESS: {len(descriptions)} descriptions generated")
        for code in list(descriptions.keys())[:3]:
            print(f"    #{code}: {descriptions[code]}")
        return descriptions
    except Exception as e:
        print(f"  [{label}] FAILED: {e}")
        return {}


def main():
    buses = load_fleet()

    summaries = {}
    for bus in buses:
        code = str(bus.get('fleet_code') or bus.get('fleet_number', ''))
        if code:
            summaries[code] = summarise_bus(bus)

    print(f"Prepared {len(summaries)} bus summaries")

    # Load existing to allow resuming
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        print(f"Loaded {len(existing)} existing waiting descriptions (will skip these)")

    todo = {k: v for k, v in summaries.items() if k not in existing}
    # Limit generation to vehicles observed in WECA when scope data is present.
    import json as _json
    from pathlib import Path as _Path
    _scope_file = _Path(__file__).parent / "blurb_scope.json"
    if _scope_file.exists():
        _scope = set(_json.loads(_scope_file.read_text()).get("codes", []))
        before = len(todo)
        todo = {k: v for k, v in todo.items() if k in _scope}
        print(f"WECA scope fence: {before} -> {len(todo)} "
              f"(skipping {before - len(todo)} never-seen-in-WECA vehicles)")

    print(f"{len(todo)} buses need waiting descriptions")

    if not todo:
        print("All buses already have waiting descriptions!")
        return

    codes = list(todo.keys())
    batches = []
    for i in range(0, len(codes), BATCH_SIZE):
        batch_codes = codes[i:i + BATCH_SIZE]
        batch_summaries = {code: todo[code] for code in batch_codes}
        batches.append(batch_summaries)

    total_batches = len(batches)
    print(f"Sending {total_batches} batches in parallel...")

    # Bound parallel requests to respect API limits.
    with ThreadPoolExecutor(max_workers=min(int(os.getenv("GEMINI_PARALLEL", "3")),
                                            total_batches)) as executor:
        futures = {
            executor.submit(process_batch, i + 1, total_batches, batch): i
            for i, batch in enumerate(batches)
        }

        for future in as_completed(futures):
            batch_idx = futures[future]
            try:
                descriptions = future.result()
                existing.update(descriptions)
            except Exception as e:
                print(f"ERROR collecting batch {batch_idx + 1}: {e}")

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    print(f"\nDone! {len(existing)} waiting descriptions saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
