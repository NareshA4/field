"""Geographical reference data for Sites & Locations: countries, timezones, states, cities, and postal codes."""
import re

COUNTRIES = [
    ("IN", "India"),
    ("US", "United States"),
    ("GB", "United Kingdom"),
    ("AE", "United Arab Emirates"),
    ("SG", "Singapore"),
    ("AU", "Australia"),
    ("CA", "Canada"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("SA", "Saudi Arabia"),
    ("JP", "Japan"),
    ("LK", "Sri Lanka"),
    ("BR", "Brazil"),
    ("CN", "China"),
    ("ID", "Indonesia"),
    ("IT", "Italy"),
    ("MY", "Malaysia"),
    ("MX", "Mexico"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("PH", "Philippines"),
    ("QA", "Qatar"),
    ("ZA", "South Africa"),
    ("KR", "South Korea"),
    ("ES", "Spain"),
    ("CH", "Switzerland"),
    ("TH", "Thailand"),
    ("TR", "Turkey"),
    ("VN", "Vietnam"),
]

COUNTRY_CODES = {code for code, _ in COUNTRIES}

COUNTRY_DEFAULT_TIMEZONES = {
    "IN": "Asia/Kolkata",
    "US": "America/New_York",
    "GB": "Europe/London",
    "AE": "Asia/Dubai",
    "SG": "Asia/Singapore",
    "AU": "Australia/Sydney",
    "CA": "America/Toronto",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "SA": "Asia/Riyadh",
    "JP": "Asia/Tokyo",
    "LK": "Asia/Colombo",
    "BR": "America/Sao_Paulo",
    "CN": "Asia/Shanghai",
    "ID": "Asia/Jakarta",
    "IT": "Europe/Rome",
    "MY": "Asia/Kuala_Lumpur",
    "MX": "America/Mexico_City",
    "NL": "Europe/Amsterdam",
    "NZ": "Pacific/Auckland",
    "PH": "Asia/Manila",
    "QA": "Asia/Qatar",
    "ZA": "Africa/Johannesburg",
    "KR": "Asia/Seoul",
    "ES": "Europe/Madrid",
    "CH": "Europe/Zurich",
    "TH": "Asia/Bangkok",
    "TR": "Europe/Istanbul",
    "VN": "Asia/Ho_Chi_Minh",
}

COUNTRY_PHONE_PREFIXES = {
    "IN": "+91",
    "US": "+1",
    "GB": "+44",
    "AE": "+971",
    "SG": "+65",
    "AU": "+61",
    "CA": "+1",
    "DE": "+49",
    "FR": "+33",
    "SA": "+966",
    "JP": "+81",
    "LK": "+94",
    "BR": "+55",
    "CN": "+86",
    "ID": "+62",
    "IT": "+39",
    "MY": "+60",
    "MX": "+52",
    "NL": "+31",
    "NZ": "+64",
    "PH": "+63",
    "QA": "+974",
    "ZA": "+27",
    "KR": "+82",
    "ES": "+34",
    "CH": "+41",
    "TH": "+66",
    "TR": "+90",
    "VN": "+84",
}

TIMEZONES = [
    ("Asia/Kolkata", "Asia/Kolkata (IST, UTC+05:30)"),
    ("UTC", "UTC (UTC+00:00)"),
    ("Asia/Dubai", "Asia/Dubai (GST, UTC+04:00)"),
    ("Asia/Riyadh", "Asia/Riyadh (AST, UTC+03:00)"),
    ("Asia/Singapore", "Asia/Singapore (SGT, UTC+08:00)"),
    ("Asia/Tokyo", "Asia/Tokyo (JST, UTC+09:00)"),
    ("Asia/Colombo", "Asia/Colombo (UTC+05:30)"),
    ("Asia/Bangkok", "Asia/Bangkok (ICT, UTC+07:00)"),
    ("Asia/Hong_Kong", "Asia/Hong_Kong (HKT, UTC+08:00)"),
    ("Asia/Kuala_Lumpur", "Asia/Kuala_Lumpur (MYT, UTC+08:00)"),
    ("Asia/Jakarta", "Asia/Jakarta (WIB, UTC+07:00)"),
    ("Asia/Shanghai", "Asia/Shanghai (CST, UTC+08:00)"),
    ("Asia/Seoul", "Asia/Seoul (KST, UTC+09:00)"),
    ("Asia/Manila", "Asia/Manila (PST, UTC+08:00)"),
    ("Asia/Qatar", "Asia/Qatar (AST, UTC+03:00)"),
    ("Europe/London", "Europe/London (GMT/BST, UTC+00/+01)"),
    ("Europe/Paris", "Europe/Paris (CET/CEST, UTC+01/+02)"),
    ("Europe/Berlin", "Europe/Berlin (CET/CEST, UTC+01/+02)"),
    ("Europe/Amsterdam", "Europe/Amsterdam (CET/CEST, UTC+01/+02)"),
    ("Europe/Dublin", "Europe/Dublin (GMT/IST, UTC+00/+01)"),
    ("Europe/Madrid", "Europe/Madrid (CET/CEST, UTC+01/+02)"),
    ("Europe/Rome", "Europe/Rome (CET/CEST, UTC+01/+02)"),
    ("Europe/Zurich", "Europe/Zurich (CET/CEST, UTC+01/+02)"),
    ("Europe/Istanbul", "Europe/Istanbul (TRT, UTC+03:00)"),
    ("America/New_York", "America/New_York (EST/EDT, UTC-05/-04)"),
    ("America/Chicago", "America/Chicago (CST/CDT, UTC-06/-05)"),
    ("America/Denver", "America/Denver (MST/MDT, UTC-07/-06)"),
    ("America/Los_Angeles", "America/Los_Angeles (PST/PDT, UTC-08/-07)"),
    ("America/Phoenix", "America/Phoenix (MST, UTC-07:00)"),
    ("America/Toronto", "America/Toronto (EST/EDT, UTC-05/-04)"),
    ("America/Vancouver", "America/Vancouver (PST/PDT, UTC-08/-07)"),
    ("America/Mexico_City", "America/Mexico_City (CST, UTC-06:00)"),
    ("America/Sao_Paulo", "America/Sao_Paulo (BRT, UTC-03:00)"),
    ("Australia/Sydney", "Australia/Sydney (AEST/AEDT, UTC+10/+11)"),
    ("Australia/Melbourne", "Australia/Melbourne (AEST/AEDT, UTC+10/+11)"),
    ("Australia/Perth", "Australia/Perth (AWST, UTC+08:00)"),
    ("Pacific/Auckland", "Pacific/Auckland (NZST/NZDT, UTC+12/+13)"),
    ("Africa/Johannesburg", "Africa/Johannesburg (SAST, UTC+02:00)"),
    ("Africa/Cairo", "Africa/Cairo (EEST, UTC+03:00)"),
]

STATES_BY_COUNTRY = {
    "IN": [
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
        "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand",
        "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
        "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
        "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
        "Uttar Pradesh", "Uttarakhand", "West Bengal",
        "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
        "Delhi (NCT)", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"
    ],
    "US": [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming", "District of Columbia"
    ],
    "GB": [
        "England", "Scotland", "Wales", "Northern Ireland"
    ],
    "AE": [
        "Abu Dhabi", "Dubai", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah", "Umm Al-Quwain"
    ],
    "CA": [
        "Alberta", "British Columbia", "Manitoba", "New Brunswick",
        "Newfoundland and Labrador", "Nova Scotia", "Ontario",
        "Prince Edward Island", "Quebec", "Saskatchewan"
    ],
    "AU": [
        "New South Wales", "Victoria", "Queensland", "Western Australia",
        "South Australia", "Tasmania", "Australian Capital Territory", "Northern Territory"
    ],
    "DE": [
        "Baden-Württemberg", "Bavaria", "Berlin", "Brandenburg", "Bremen",
        "Hamburg", "Hesse", "Lower Saxony", "Mecklenburg-Vorpommern",
        "North Rhine-Westphalia", "Rhineland-Palatinate", "Saarland",
        "Saxony", "Saxony-Anhalt", "Schleswig-Holstein", "Thuringia"
    ],
}

# India Postal Circle (2-digit PIN prefixes) per India Post
INDIA_STATE_PIN_PREFIXES = {
    "Delhi": {"prefixes": ["11"], "sample": "110001"},
    "Delhi (NCT)": {"prefixes": ["11"], "sample": "110001"},
    "Haryana": {"prefixes": ["12", "13"], "sample": "122001"},
    "Punjab": {"prefixes": ["14", "15"], "sample": "141001"},
    "Chandigarh": {"prefixes": ["16"], "sample": "160017"},
    "Himachal Pradesh": {"prefixes": ["17"], "sample": "171001"},
    "Jammu and Kashmir": {"prefixes": ["18", "19"], "sample": "190001"},
    "Ladakh": {"prefixes": ["19"], "sample": "194101"},
    "Uttar Pradesh": {"prefixes": ["20", "21", "22", "23", "24", "25", "26", "27", "28"], "sample": "201301"},
    "Uttarakhand": {"prefixes": ["24", "26"], "sample": "248001"},
    "Rajasthan": {"prefixes": ["30", "31", "32", "33", "34"], "sample": "302001"},
    "Gujarat": {"prefixes": ["36", "37", "38", "39"], "sample": "380001"},
    "Dadra and Nagar Haveli and Daman and Diu": {"prefixes": ["39"], "sample": "396210"},
    "Maharashtra": {"prefixes": ["40", "41", "42", "43", "44"], "sample": "400001"},
    "Goa": {"prefixes": ["40"], "sample": "403001"},
    "Madhya Pradesh": {"prefixes": ["45", "46", "47", "48"], "sample": "452001"},
    "Chhattisgarh": {"prefixes": ["49"], "sample": "492001"},
    "Telangana": {"prefixes": ["50"], "sample": "500001"},
    "Andhra Pradesh": {"prefixes": ["51", "52", "53"], "sample": "530001"},
    "Karnataka": {"prefixes": ["56", "57", "58", "59"], "sample": "560001"},
    "Tamil Nadu": {"prefixes": ["60", "61", "62", "63", "64"], "sample": "600001"},
    "Puducherry": {"prefixes": ["60"], "sample": "605001"},
    "Kerala": {"prefixes": ["67", "68", "69"], "sample": "682001"},
    "Lakshadweep": {"prefixes": ["68"], "sample": "682555"},
    "West Bengal": {"prefixes": ["70", "71", "72", "73", "74"], "sample": "700001"},
    "Andaman and Nicobar Islands": {"prefixes": ["74"], "sample": "744101"},
    "Sikkim": {"prefixes": ["73"], "sample": "737101"},
    "Odisha": {"prefixes": ["75", "76", "77"], "sample": "751001"},
    "Assam": {"prefixes": ["78"], "sample": "781001"},
    "Arunachal Pradesh": {"prefixes": ["79"], "sample": "791111"},
    "Manipur": {"prefixes": ["79"], "sample": "795001"},
    "Meghalaya": {"prefixes": ["79"], "sample": "793001"},
    "Mizoram": {"prefixes": ["79"], "sample": "796001"},
    "Nagaland": {"prefixes": ["79"], "sample": "797001"},
    "Tripura": {"prefixes": ["79"], "sample": "799001"},
    "Bihar": {"prefixes": ["80", "81", "82", "83", "84", "85"], "sample": "800001"},
    "Jharkhand": {"prefixes": ["81", "82", "83"], "sample": "834001"},
}

CITIES_BY_STATE = {
    # India
    "Karnataka": [
        "Bengaluru", "Mysuru", "Hubballi-Dharwad", "Mangaluru", "Belagavi",
        "Kalaburagi", "Ballari", "Davanagere", "Shivamogga", "Tumakuru", "Udupi", "Bidar", "Hosapete"
    ],
    "Maharashtra": [
        "Mumbai", "Pune", "Nagpur", "Thane", "Nashik", "Kalyan-Dombivli",
        "Vasai-Virar", "Aurangabad", "Navi Mumbai", "Solapur", "Kolhapur", "Amravati", "Nanded"
    ],
    "Tamil Nadu": [
        "Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem",
        "Tiruppur", "Erode", "Tirunelveli", "Vellore", "Thoothukudi", "Dindigul", "Thanjavur"
    ],
    "Delhi": [
        "New Delhi", "Central Delhi", "North Delhi", "South Delhi", "East Delhi", "West Delhi", "Dwarka", "Rohini", "Connaught Place"
    ],
    "Delhi (NCT)": [
        "New Delhi", "Central Delhi", "North Delhi", "South Delhi", "East Delhi", "West Delhi", "Dwarka", "Rohini", "Connaught Place"
    ],
    "Telangana": [
        "Hyderabad", "Warangal", "Nizamabad", "Karimnagar", "Ramagundam", "Khammam", "Mahbubnagar", "Nalgonda"
    ],
    "Gujarat": [
        "Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar", "Junagadh", "Anand", "Navsari", "Morbi"
    ],
    "West Bengal": [
        "Kolkata", "Howrah", "Durgapur", "Asansol", "Siliguri", "Bardhaman", "Malda", "Kharagpur", "Haldia"
    ],
    "Uttar Pradesh": [
        "Noida", "Greater Noida", "Lucknow", "Kanpur", "Ghaziabad", "Agra", "Varanasi", "Meerut", "Prayagraj", "Bareilly", "Aligarh", "Moradabad", "Gorakhpur"
    ],
    "Kerala": [
        "Thiruvananthapuram", "Kochi", "Kozhikode", "Kollam", "Thrissur", "Kannur", "Alappuzha", "Palakkad", "Kottayam"
    ],
    "Andhra Pradesh": [
        "Visakhapatnam", "Vijayawada", "Guntur", "Nellore", "Kurnool", "Kakinada", "Rajahmundry", "Tirupati", "Kadapa", "Anantapur"
    ],
    "Rajasthan": [
        "Jaipur", "Jodhpur", "Kota", "Bikaner", "Ajmer", "Udaipur", "Bhilwara", "Alwar", "Bharatpur", "Sikar"
    ],
    "Haryana": [
        "Gurugram", "Faridabad", "Panipat", "Ambala", "Yamunanagar", "Rohtak", "Hisar", "Karnal", "Sonipat", "Panchkula"
    ],
    "Punjab": [
        "Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Bathinda", "Mohali", "Hoshiarpur", "Pathankot"
    ],
    "Madhya Pradesh": [
        "Indore", "Bhopal", "Jabalpur", "Gwalior", "Ujjain", "Sagar", "Dewas", "Satna", "Ratlam"
    ],
    "Bihar": [
        "Patna", "Gaya", "Bhagalpur", "Muzaffarpur", "Purnia", "Darbhanga", "Bihar Sharif", "Arrah", "Begusarai"
    ],
    "Odisha": [
        "Bhubaneswar", "Cuttack", "Rourkela", "Berhampur", "Sambalpur", "Puri", "Balasore", "Bhadrak"
    ],
    "Assam": [
        "Guwahati", "Silchar", "Dibrugarh", "Jorhat", "Nagaon", "Tinsukia", "Tezpur"
    ],
    "Goa": [
        "Panaji", "Margao", "Vasco da Gama", "Mapusa", "Ponda"
    ],
    "Jharkhand": [
        "Ranchi", "Jamshedpur", "Dhanbad", "Bokaro Steel City", "Deoghar", "Hazaribagh"
    ],
    "Chhattisgarh": [
        "Raipur", "Bhilai", "Bilaspur", "Korba", "Durg", "Rajnandgaon"
    ],
    "Uttarakhand": [
        "Dehradun", "Haridwar", "Roorkee", "Haldwani", "Rishikesh", "Nainital"
    ],
    "Himachal Pradesh": [
        "Shimla", "Dharamshala", "Solan", "Mandi", "Baddi", "Kullu"
    ],
    "Chandigarh": [
        "Chandigarh"
    ],
    "Jammu and Kashmir": [
        "Srinagar", "Jammu", "Anantnag", "Baramulla"
    ],
    "Ladakh": [
        "Leh", "Kargil"
    ],
    "Puducherry": [
        "Puducherry", "Karaikal", "Oulgaret"
    ],
    "Arunachal Pradesh": [
        "Itanagar", "Naharlagun", "Pasighat"
    ],
    "Manipur": [
        "Imphal", "Churachandpur", "Thoubal"
    ],
    "Meghalaya": [
        "Shillong", "Tura", "Jowai"
    ],
    "Mizoram": [
        "Aizawl", "Lunglei"
    ],
    "Nagaland": [
        "Kohima", "Dimapur", "Mokokchung"
    ],
    "Tripura": [
        "Agartala", "Dharmanagar"
    ],
    "Sikkim": [
        "Gangtok", "Namchi"
    ],
    "Andaman and Nicobar Islands": [
        "Port Blair"
    ],
    "Dadra and Nagar Haveli and Daman and Diu": [
        "Daman", "Silvassa", "Diu"
    ],
    "Lakshadweep": [
        "Kavaratti"
    ],

    # US
    "California": ["Los Angeles", "San Francisco", "San Diego", "San Jose", "Sacramento", "Fresno", "Oakland", "Long Beach", "Irvine"],
    "Texas": ["Houston", "Dallas", "Austin", "San Antonio", "Fort Worth", "El Paso", "Arlington", "Plano", "Irving"],
    "New York": ["New York City", "Buffalo", "Rochester", "Yonkers", "Syracuse", "Albany", "White Plains"],
    "Florida": ["Miami", "Orlando", "Tampa", "Jacksonville", "Fort Lauderdale", "St. Petersburg", "Tallahassee"],
    "Illinois": ["Chicago", "Aurora", "Naperville", "Joliet", "Rockford", "Springfield"],
    "Washington": ["Seattle", "Spokane", "Tacoma", "Vancouver", "Bellevue", "Everett", "Olympia"],

    # UAE
    "Dubai": ["Dubai", "Jebel Ali", "Deira", "Bur Dubai", "Business Bay", "Dubai South", "Al Quoz"],
    "Abu Dhabi": ["Abu Dhabi", "Al Ain", "Al Dhafra", "Mussafah", "Khalifa City"],
    "Sharjah": ["Sharjah", "Khor Fakkan", "Kalba"],

    # UK
    "England": ["London", "Manchester", "Birmingham", "Leeds", "Liverpool", "Bristol", "Sheffield", "Newcastle"],
    "Scotland": ["Edinburgh", "Glasgow", "Aberdeen", "Dundee"],
    "Wales": ["Cardiff", "Swansea", "Newport"],
    "Northern Ireland": ["Belfast", "Derry", "Lisburn"],
}

# Primary PIN / Postal code suggestions per city
CITY_PINCODES = {
    # Karnataka
    "Bengaluru": "560001", "Mysuru": "570001", "Hubballi-Dharwad": "580001",
    "Mangaluru": "575001", "Belagavi": "590001", "Kalaburagi": "585101",
    "Ballari": "583101", "Davanagere": "577001", "Shivamogga": "577201",
    "Tumakuru": "572101", "Udupi": "576101", "Bidar": "585401", "Hosapete": "583201",

    # Maharashtra
    "Mumbai": "400001", "Pune": "411001", "Nagpur": "440001", "Thane": "400601",
    "Nashik": "422001", "Kalyan-Dombivli": "421201", "Vasai-Virar": "401201",
    "Aurangabad": "431001", "Navi Mumbai": "400703", "Solapur": "413001",
    "Kolhapur": "416001", "Amravati": "444601", "Nanded": "431601",

    # Tamil Nadu
    "Chennai": "600001", "Coimbatore": "641001", "Madurai": "625001",
    "Tiruchirappalli": "620001", "Salem": "636001", "Tiruppur": "641601",
    "Erode": "638001", "Tirunelveli": "627001", "Vellore": "632001",

    # Delhi
    "New Delhi": "110001", "Central Delhi": "110001", "North Delhi": "110007",
    "South Delhi": "110017", "East Delhi": "110092", "West Delhi": "110027",
    "Dwarka": "110075", "Rohini": "110085", "Connaught Place": "110001",

    # Telangana & AP
    "Hyderabad": "500001", "Warangal": "506001", "Nizamabad": "503001", "Karimnagar": "505001",
    "Visakhapatnam": "530001", "Vijayawada": "520001", "Guntur": "522001", "Tirupati": "517501",

    # Gujarat
    "Ahmedabad": "380001", "Surat": "395001", "Vadodara": "390001", "Rajkot": "360001", "Gandhinagar": "382010",

    # West Bengal
    "Kolkata": "700001", "Howrah": "711101", "Durgapur": "713201", "Asansol": "713301", "Siliguri": "734001",

    # UP & Haryana
    "Noida": "201301", "Greater Noida": "201310", "Lucknow": "226001", "Kanpur": "208001",
    "Ghaziabad": "201001", "Agra": "282001", "Varanasi": "221001", "Meerut": "250001",
    "Gurugram": "122001", "Faridabad": "121001", "Panipat": "132103",

    # Rajasthan & Punjab
    "Jaipur": "302001", "Jodhpur": "342001", "Udaipur": "313001", "Kota": "324001",
    "Ludhiana": "141001", "Amritsar": "143001", "Mohali": "160055", "Chandigarh": "160017",

    # Kerala
    "Kochi": "682001", "Thiruvananthapuram": "695001", "Kozhikode": "673001",

    # Others
    "Indore": "452001", "Bhopal": "462001", "Patna": "800001", "Bhubaneswar": "751001",
    "Guwahati": "781001", "Panaji": "403001", "Ranchi": "834001", "Raipur": "492001",
    "Dehradun": "248001", "Shimla": "171001",
}

# Reverse mapping: City -> State
CITY_TO_STATE = {}
for state_name, cities in CITIES_BY_STATE.items():
    for city_name in cities:
        CITY_TO_STATE[city_name] = state_name


def validate_india_pincode(state: str, postal_code: str) -> tuple[bool, str]:
    """Validate 6-digit Indian PIN code and check whether it belongs to the state's postal circle."""
    code = (postal_code or "").strip()
    if not code:
        return False, "PIN code is required."
    if not re.fullmatch(r"^[1-9][0-9]{5}$", code):
        return False, "Indian PIN code must be exactly 6 digits (cannot start with 0, e.g. 560001)."

    state_norm = (state or "").strip()
    # Normalize Delhi
    if state_norm in ("Delhi", "Delhi (NCT)"):
        state_info = INDIA_STATE_PIN_PREFIXES.get("Delhi (NCT)")
    else:
        state_info = INDIA_STATE_PIN_PREFIXES.get(state_norm)

    if state_info:
        first_two = code[:2]
        allowed = state_info["prefixes"]
        if first_two not in allowed:
            expected = ", ".join(allowed)
            return False, (
                f"PIN code {code} does not match {state_norm}. "
                f"Valid PIN codes for {state_norm} must start with {expected} (e.g. {state_info['sample']})."
            )

    return True, ""
