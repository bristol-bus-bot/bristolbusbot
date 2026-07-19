"""Hand-curated stop-name corrections used by search and departures."""
import re

# Maps (stop_code_prefix_or_exact, original_name) -> cleaned_name
# Exact codes checked first, then prefixes (longest match wins)
STOP_NAME_EXACT = {
    # Weston-super-Mare
    ('wsmpawp', 'Sainsburys'): 'Locking Sainsburys',
    ('wsmgwjg', 'Leisure Centre'): 'Hutton Moor Leisure Centre',
    ('wsmgwjd', 'Leisure Centre'): 'Hutton Moor Leisure Centre',
    ('wsmgapj', 'Tesco'): 'Weston Station Road Tesco',
    ('wsmgapm', 'Tesco'): 'Weston Station Road Tesco',
    ('wsmjdgm', 'Post Office'): 'Weston-super-Mare Post Office',
    ('wsmjdgd', 'Post Office'): 'Weston-super-Mare Post Office',
    # Bristol
    ('bstpgmj', 'Transport Hub'): 'Bristol University North Village Transport Hub',
    ('bstpgmp', 'Transport Hub'): 'Bristol University North Village Transport Hub',
    ('bstpgdw', 'Skills Academy'): 'South Bristol Skills Academy',
    ('bstgpmt', 'Hengrove Leisure Pk'): 'Hengrove Leisure Park',
    ('bstgpwt', 'Hengrove Leisure Pk'): 'Hengrove Leisure Park',
    ('bstptdw', "Sainsbury's"): "Arno's Vale Sainsbury's",
    ('bstagpa', 'The Roman Villa'): 'Kingsweston Roman Villa',
    ('bstagpd', 'The Roman Villa'): 'Kingsweston Roman Villa',
    ('bstajdt', 'Woodleaze'): 'Woodleaze in Sea Mills',
    ('bstajmj', 'Riverleaze'): 'Bristol Manor Farm Football Club',
    ('bstdgjg', 'Students Union'): 'UoB Students Union',
    ('bstdgpt', 'Students Union'): 'UoB Students Union',
    ('bstdtmw', 'Whiteleaze'): 'Whiteleaze - Southmead Road',
    ('bstdwag', 'Charlton Rd Jct'): 'Charlton Road Junction',
    ('bstjdgp', 'Alverstoke'): 'Alverstoke Green',
    ('bstmdwp', 'Stapleton Baptist Ch'): 'Stapleton Baptist Church',
    ('bstmwga', 'Quadrant West'): 'Hillfields Quadrant West',
    ('bstpgja', 'Bridge Campus'): 'Bridge Learning Campus Secondary School',
    ('bstpgwa', 'Cater Road Rbt'): 'Cater Road Roundabout',
    ('bstpjmd', 'Third Way'): 'Third Way Avonmouth',
    ('bstpjtg', 'Filwood Grn Business Pk'): 'Filwood Green Business Park',
    ('bstpmwt', 'Portway P&R'): 'Portway Park & Ride',
    ('bstpgmj', 'Transport Hub'): 'Bristol University North Village Transport Hub',
    # Bath
    ('bthjdwg', "Sainsbury's"): "Bath Green Park Sainsbury's",
    ('bthjdwg', 'Sainsburys'): "Bath Green Park Sainsbury's",
    ('bthmwjt', "Sainsbury's"): "Bath Green Park Sainsbury's",
    ('bthmwjt', 'Sainsburys'): "Bath Green Park Sainsbury's",
    ('bthadgp', 'Post Office'): 'Bath Union Street Post Office',
    ('bthadgt', 'Post Office'): 'Bath Union Street Post Office',
    ('bthadtw', 'Post Office'): 'Bath Union Street Post Office',
    ('bthadwa', 'Post Office'): 'Bath Union Street Post Office',
    ('bthagat', 'Post Office'): 'Bath Moorland Road Post Office',
    ('bthagaw', 'Post Office'): 'Bath Moorland Road Post Office',
    ('bthjatw', 'Post Office'): 'Bath Green Park Post Office',
    ('bthpamj', 'Post Office'): 'Bath Weston Post Office',
    ('bthajaj', 'Hillcrest'): 'Hillcrest Pensford',
    ('bthawjm', 'Recreation Ground'): 'Timsbury Recreation Ground',
    ('bthawmt', 'Two Headed Man'): 'Keynsham Motors',
    ('bthdjtp', 'Newbridge P&R'): 'Newbridge Park & Ride',
    # South Gloucestershire
    ('sglatjp', "Sainsbury's"): "Gloucester Road Sainsbury's",
    ('sglatjp', 'Sainsburys'): "Gloucester Road Sainsbury's",
    ('sgladam', 'Post Office'): 'Thornbury High Street Post Office',
    ('sgladaj', 'Post Office'): 'Thornbury High Street Post Office',
    ('sglpatd', 'Post Office'): 'Emersons Green Post Office',
    ('sglpata', 'Post Office'): 'Emersons Green Post Office',
    ('sglgwgp', "Sainsbury's"): "Emersons Green Sainsbury's",
    ('sgldgat', 'Leisure Centre'): 'Kingswood Leisure Centre',
    ('sgldgap', 'Leisure Centre'): 'Kingswood Leisure Centre',
    ('sglagdg', 'Rugby Club'): 'Clifton Rugby Club',
    ('sglagdj', 'Rugby Club'): 'Clifton Rugby Club',
    ('sglmwma', 'The Clock'): 'Chipping Sodbury Clock Tower',
    ('sglmwtg', 'The Boot'): 'The Boot Inn',
    ('sglptwt', 'Park & Ride (B)'): 'Yate Park & Ride (B)',
    ('sglpwgt', 'Park & Ride (A)'): 'Yate Park & Ride (A)',
    ('sgltada', 'Park & Ride (C)'): 'Yate Park & Ride (C)',
    ('sgltadg', 'Amazon'): 'Amazon Distribution Centre BRS1',
    # Bristol Airport
    ('wsmpgwp', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpgwt', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjad', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjaj', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjam', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjap', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpdtw', 'Airport Bus Station'): 'Bristol Airport Bus Station',
}

# Prefix-based mappings: (prefix, original_name) -> cleaned_name
STOP_NAME_PREFIX = [
    # Weston-super-Mare
    ('wsmp', 'Public Transport Interchange', 'Weston-super-Mare Bus Station'),
    ('wsmga', 'Tesco', 'Weston-super-Mare Tesco'),
    ('wsmdp', 'Tesco', 'Worle Tesco'),
    ('wsmjw', 'Dental Practice', 'Weston Marina Dental Practice'),
    ('wsmjw', 'Marina Healthcare Centre', 'Weston Marina Healthcare Centre'),
    # Bristol
    ('bstg', 'Bus Station', 'Bristol Bus Station'),
    ('bstj', 'Temple Meads Stn', 'Bristol Temple Meads Station'),
    ('bstgw', 'Temple Meads Stn', 'Bristol Temple Meads Station'),
    # Bath
    ('bthm', 'Bus Station', 'Bath Bus Station'),
    ('bthj', 'Morrisons', 'Bath Morrisons'),
    ('bthp', 'Tesco', 'Bath Tesco'),
    # South Gloucestershire / Thornbury
    ('sglagt', 'Bus Station', 'Cribbs Causeway Bus Station'),
    ('sglag', 'Retail Park', 'Cribbs Causeway Retail Park'),
    ('sglat', 'Sainsburys', 'Thornbury Sainsburys'),
    ('sglat', "Sainsbury's", 'Thornbury Sainsburys'),
    ('sgldg', 'Tesco', 'Thornbury Tesco'),
    ('sglmt', 'Morrisons', 'Thornbury Morrisons'),
    ('sglpm', 'Morrisons', 'Thornbury Morrisons'),
    ('sglmt', 'Shopping Centre', 'Thornbury Shopping Centre'),
    ('sglpm', 'Shopping Centre', 'Thornbury Shopping Centre'),
    ('sglpw', 'Shopping Centre', 'Thornbury Shopping Centre'),
]

def clean_stop_name(stop_name, stop_code):
    """Clean generic stop names using stop code to add location context."""
    if not stop_code or not stop_name:
        return stop_name or 'Unknown'
    code = stop_code.lower()
    # Check exact code matches first
    key = (code, stop_name)
    if key in STOP_NAME_EXACT:
        return STOP_NAME_EXACT[key]
    # Check prefix matches (order matters - longer prefixes checked first via list order)
    for prefix, original, cleaned in STOP_NAME_PREFIX:
        if code.startswith(prefix) and stop_name == original:
            return cleaned
    return stop_name
