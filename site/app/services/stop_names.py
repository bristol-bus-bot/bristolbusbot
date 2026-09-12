"""Hand-curated stop-name corrections used by search and departures."""

# Maps (exact_stop_code, original_name) -> cleaned_name.
# Location corrections use reviewed NaPTAN codes rather than guessed prefixes.
STOP_NAME_EXACT = {
    # NaPTAN stop identities and NPTG localities checked 2026-09-12.
    ('sglmtap', 'Morrisons'): 'Yate Morrisons',
    ('sglpmpg', 'Morrisons'): 'Yate Morrisons',
    ('bthpdat', 'Tesco'): 'Old Mills Tesco',
    ('bthpdap', 'Tesco'): 'Old Mills Tesco',
    ('wsmdpmg', 'Tesco'): 'Clevedon Tesco',
    ('wsmdpmp', 'Tesco'): 'Clevedon Tesco',
    ('wsmjwgj', 'Dental Practice'): 'Portishead Dental Practice',
    ('wsmjwgp', 'Marina Healthcare Centre'): 'Portishead Marina Healthcare Centre',
    ('wsmpgwm', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjag', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjat', 'Public Transport Interchange'): 'Bristol Airport Interchange',
    ('wsmpjaw', 'Public Transport Interchange'): 'Bristol Airport Interchange',

    # North Somerset
    ('wsmpawp', 'Sainsburys'): 'Locking Sainsburys',
    ('wsmgwjg', 'Leisure Centre'): 'Backwell Leisure Centre',
    ('wsmgwjd', 'Leisure Centre'): 'Backwell Leisure Centre',
    ('wsmgapj', 'Tesco'): 'Congresbury Tesco',
    ('wsmgapm', 'Tesco'): 'Congresbury Tesco',
    ('wsmjdgm', 'Post Office'): 'Felton Post Office',
    ('wsmjdgd', 'Post Office'): 'Felton Post Office',
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
    # Bath and North East Somerset
    ('bthjdwg', "Sainsbury's"): "Odd Down Sainsbury's",
    ('bthjdwg', 'Sainsburys'): "Odd Down Sainsbury's",
    ('bthmwjt', "Sainsbury's"): "Odd Down Sainsbury's",
    ('bthmwjt', 'Sainsburys'): "Odd Down Sainsbury's",
    ('bthadgp', 'Post Office'): 'Compton Martin Post Office',
    ('bthadgt', 'Post Office'): 'Compton Martin Post Office',
    ('bthadtw', 'Post Office'): 'Chew Magna Post Office',
    ('bthadwa', 'Post Office'): 'Chew Magna Post Office',
    ('bthagat', 'Post Office'): 'Bishop Sutton Post Office',
    ('bthagaw', 'Post Office'): 'Bishop Sutton Post Office',
    ('bthjatw', 'Post Office'): 'Odd Down Post Office',
    ('bthpamj', 'Post Office'): 'Keynsham Post Office',
    ('bthajaj', 'Hillcrest'): 'Hillcrest Pensford',
    ('bthawjm', 'Recreation Ground'): 'Timsbury Recreation Ground',
    ('bthawmt', 'Two Headed Man'): 'Keynsham Motors',
    ('bthdjtp', 'Newbridge P&R'): 'Newbridge Park & Ride',
    # South Gloucestershire
    # NaPTAN stop area 017G0001, checked 2026-09-10.
    ('sglmtmg', 'Shopping Centre'): 'Yate Shopping Centre',
    ('sglmtma', 'Shopping Centre'): 'Yate Shopping Centre',
    ('sglmtmd', 'Shopping Centre'): 'Yate Shopping Centre',
    ('sglpwdj', 'Shopping Centre'): 'Yate Shopping Centre',
    ('sglpwdm', 'Shopping Centre'): 'Yate Shopping Centre',
    ('sglatjp', "Sainsbury's"): "Stoke Gifford Sainsbury's",
    ('sglatjp', 'Sainsburys'): "Stoke Gifford Sainsbury's",
    ('sgladam', 'Post Office'): 'Severn Beach Post Office',
    ('sgladaj', 'Post Office'): 'Severn Beach Post Office',
    ('sglpatd', 'Post Office'): 'Horton Post Office',
    ('sglpata', 'Post Office'): 'Horton Post Office',
    ('sglgwgp', "Sainsbury's"): "Emersons Green Sainsbury's",
    ('sgldgat', 'Leisure Centre'): 'Thornbury Leisure Centre',
    ('sgldgap', 'Leisure Centre'): 'Thornbury Leisure Centre',
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
    # Bristol
    ('bstg', 'Bus Station', 'Bristol Bus Station'),
    ('bstj', 'Temple Meads Stn', 'Bristol Temple Meads Station'),
    ('bstgw', 'Temple Meads Stn', 'Bristol Temple Meads Station'),
    # Bath
    ('bthm', 'Bus Station', 'Bath Bus Station'),
    ('bthj', 'Morrisons', 'Bath Morrisons'),
    # South Gloucestershire / Thornbury
    ('sglagt', 'Bus Station', 'Cribbs Causeway Bus Station'),
    ('sglag', 'Retail Park', 'Cribbs Causeway Retail Park'),
    ('sgldg', 'Tesco', 'Thornbury Tesco'),
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
