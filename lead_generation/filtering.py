from __future__ import annotations

import re
from .models import ATSJob

REGIONAL_LOCATION_ALIASES: dict[str, set[str]] = {
    "remote": {
        "remote", "worldwide", "global", "anywhere", "work from anywhere",
        "wfa", "wfh", "distributed", "fully remote", "100% remote",
        "remote global", "remote worldwide", "emea", "apac", "americas",
    },
    "worldwide": {
        "remote", "worldwide", "global", "anywhere", "work from anywhere",
        "wfa", "wfh", "distributed", "fully remote", "100% remote",
    },
    "global": {
        "remote", "worldwide", "global", "anywhere", "work from anywhere",
        "wfa", "wfh", "distributed", "fully remote", "100% remote",
    },
    "uk": {
        "uk", "u.k.", "gb", "great britain", "united kingdom", "england",
        "scotland", "wales", "northern ireland", "london", "manchester",
        "edinburgh", "bristol", "cambridge", "oxford", "birmingham",
        "leeds", "glasgow", "belfast", "remote uk", "uk remote", "emea",
    },
    "united kingdom": {
        "uk", "u.k.", "gb", "great britain", "united kingdom", "england",
        "scotland", "wales", "northern ireland", "london", "manchester",
        "edinburgh", "bristol", "cambridge", "oxford", "birmingham",
        "leeds", "glasgow", "belfast", "remote uk", "uk remote", "emea",
    },
    "australia": {
        "australia", "au", "aus", "sydney", "melbourne", "brisbane",
        "perth", "adelaide", "canberra", "gold coast", "nsw", "victoria",
        "queensland", "anz", "apac", "remote australia", "new zealand",
        "nz", "auckland", "wellington",
    },
    "au": {
        "australia", "au", "aus", "sydney", "melbourne", "brisbane",
        "perth", "adelaide", "canberra", "anz", "apac", "new zealand", "nz",
    },
    "aus": {
        "australia", "au", "aus", "sydney", "melbourne", "brisbane",
        "perth", "adelaide", "canberra", "anz", "apac", "new zealand", "nz",
    },
    "new zealand": {
        "new zealand", "nz", "auckland", "wellington", "christchurch", "anz", "apac",
    },
    "nz": {
        "new zealand", "nz", "auckland", "wellington", "christchurch", "anz", "apac",
    },
    "us": {
        "us", "u.s.", "usa", "united states", "new york", "san francisco",
        "austin", "seattle", "boston", "chicago", "los angeles", "remote us",
        "bay area", "nyc", "silicon valley", "denver", "atlanta", "miami",
        "washington", "dallas", "san diego", "north america", "americas",
    },
    "usa": {
        "us", "u.s.", "usa", "united states", "new york", "san francisco",
        "austin", "seattle", "boston", "chicago", "los angeles", "remote us",
        "bay area", "nyc", "silicon valley", "denver", "atlanta", "miami",
        "washington", "dallas", "san diego", "north america", "americas",
    },
    "united states": {
        "us", "u.s.", "usa", "united states", "new york", "san francisco",
        "austin", "seattle", "boston", "chicago", "los angeles", "remote us",
        "bay area", "nyc", "silicon valley", "denver", "atlanta", "miami",
        "washington", "dallas", "san diego", "north america", "americas",
    },
    "canada": {
        "canada", "ca", "toronto", "vancouver", "montreal", "ottawa",
        "calgary", "waterloo", "ontario", "british columbia", "quebec",
        "remote canada", "north america", "americas",
    },
    "ca": {
        "canada", "ca", "toronto", "vancouver", "montreal", "ottawa",
        "calgary", "waterloo", "remote canada", "north america",
    },
    "uae": {
        "uae", "u.a.e.", "united arab emirates", "dubai", "abu dhabi",
        "sharjah", "mena", "middle east", "gcc", "remote uae", "remote dubai",
        "emea",
    },
    "dubai": {
        "uae", "u.a.e.", "united arab emirates", "dubai", "abu dhabi",
        "sharjah", "mena", "middle east", "gcc", "remote uae", "remote dubai",
        "emea",
    },
    "united arab emirates": {
        "uae", "u.a.e.", "united arab emirates", "dubai", "abu dhabi",
        "sharjah", "mena", "middle east", "gcc", "remote uae", "remote dubai",
        "emea",
    },
    "saudi arabia": {
        "saudi arabia", "ksa", "saudi", "riyadh", "jeddah", "dammam",
        "mena", "middle east", "gcc", "emea",
    },
    "ksa": {
        "saudi arabia", "ksa", "saudi", "riyadh", "jeddah", "mena", "gcc",
    },
    "qatar": {
        "qatar", "doha", "mena", "middle east", "gcc", "emea",
    },
    "mena": {
        "uae", "united arab emirates", "dubai", "abu dhabi", "saudi arabia",
        "ksa", "riyadh", "qatar", "doha", "bahrain", "kuwait", "oman",
        "cairo", "egypt", "amman", "jordan", "mena", "middle east", "gcc", "emea",
    },
    "germany": {
        "germany", "de", "deutschland", "berlin", "munich", "munchen",
        "hamburg", "frankfurt", "cologne", "stuttgart", "dach", "eu", "europe", "emea",
    },
    "de": {
        "germany", "de", "berlin", "munich", "hamburg", "frankfurt", "dach", "eu", "europe", "emea",
    },
    "netherlands": {
        "netherlands", "nl", "holland", "amsterdam", "rotterdam", "utrecht",
        "eindhoven", "the hague", "benelux", "eu", "europe", "emea",
    },
    "nl": {
        "netherlands", "nl", "amsterdam", "rotterdam", "utrecht", "eindhoven", "eu", "europe", "emea",
    },
    "ireland": {
        "ireland", "ie", "dublin", "cork", "galway", "limerick", "eu", "europe", "emea",
    },
    "switzerland": {
        "switzerland", "ch", "zurich", "geneva", "basel", "lausanne", "zug", "bern", "dach", "europe", "emea",
    },
    "france": {
        "france", "fr", "paris", "lyon", "toulouse", "marseille", "nantes", "eu", "europe", "emea",
    },
    "spain": {
        "spain", "es", "madrid", "barcelona", "valencia", "malaga", "bilbao", "eu", "europe", "emea",
    },
    "portugal": {
        "portugal", "pt", "lisbon", "lisboa", "porto", "braga", "eu", "europe", "emea",
    },
    "sweden": {
        "sweden", "se", "stockholm", "gothenburg", "malmo", "nordics", "eu", "europe", "emea",
    },
    "denmark": {
        "denmark", "dk", "copenhagen", "aarhus", "nordics", "eu", "europe", "emea",
    },
    "norway": {
        "norway", "no", "oslo", "bergen", "nordics", "europe", "emea",
    },
    "finland": {
        "finland", "fi", "helsinki", "espoo", "tampere", "nordics", "eu", "europe", "emea",
    },
    "estonia": {
        "estonia", "ee", "tallinn", "tartu", "baltics", "eu", "europe", "emea",
    },
    "poland": {
        "poland", "pl", "warsaw", "krakow", "wroclaw", "gdansk", "poznan", "cee", "eu", "europe", "emea",
    },
    "austria": {
        "austria", "at", "vienna", "wien", "graz", "linz", "dach", "eu", "europe", "emea",
    },
    "belgium": {
        "belgium", "be", "brussels", "antwerp", "ghent", "benelux", "eu", "europe", "emea",
    },
    "italy": {
        "italy", "it", "milan", "milano", "rome", "roma", "turin", "eu", "europe", "emea",
    },
    "eu": {
        "eu", "europe", "european union", "emea", "remote eu", "remote europe",
        "berlin", "germany", "amsterdam", "netherlands", "dublin", "ireland",
        "paris", "france", "madrid", "barcelona", "spain", "lisbon", "portugal",
        "stockholm", "sweden", "tallinn", "estonia", "warsaw", "poland",
        "vienna", "austria", "brussels", "belgium", "copenhagen", "denmark",
        "helsinki", "finland", "zurich", "switzerland",
    },
    "europe": {
        "eu", "europe", "european union", "emea", "remote eu", "remote europe",
        "uk", "united kingdom", "london", "berlin", "germany", "amsterdam",
        "netherlands", "dublin", "ireland", "paris", "france", "madrid",
        "barcelona", "spain", "lisbon", "portugal", "stockholm", "sweden",
        "tallinn", "estonia", "warsaw", "poland", "zurich", "switzerland",
    },
    "singapore": {
        "singapore", "sg", "apac", "sea", "southeast asia", "remote singapore",
    },
    "sg": {
        "singapore", "sg", "apac", "sea",
    },
    "hong kong": {
        "hong kong", "hk", "apac",
    },
    "japan": {
        "japan", "jp", "tokyo", "osaka", "fukuoka", "kyoto", "apac",
    },
    "india": {
        "india", "in", "bangalore", "bengaluru", "mumbai", "hyderabad",
        "pune", "delhi", "gurgaon", "gurugram", "noida", "chennai", "apac",
    },
    "latam": {
        "latam", "latin america", "south america", "brazil", "sao paulo",
        "mexico", "mexico city", "argentina", "buenos aires", "colombia",
        "bogota", "chile", "santiago", "americas",
    },
    "apac": {
        "apac", "asia pacific", "australia", "sydney", "melbourne",
        "new zealand", "auckland", "singapore", "japan", "tokyo",
        "hong kong", "india", "bangalore", "seoul", "south korea",
    },
}

GENERIC_ROLE_FILLERS = {
    "a", "an", "the", "developer", "dev", "engineer", "eng", "software",
    "programmer", "specialist", "senior", "sr", "junior", "jr", "lead",
    "mid", "staff", "principal", "role", "job", "position",
}

NON_DECISION_MAKER_ROLE_PATTERNS = re.compile(
    r"\b(?:intern|internship|graduate|grad\s+scheme|apprentice|apprenticeship|trainee|working\s+student|werkstudent|co-op)\b",
    re.IGNORECASE,
)


def _compact(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9+#.\s]", " ", value.lower())
    return " ".join(cleaned.split())


def parse_company_size_bounds(size_str: str | None) -> tuple[int, int] | None:
    """
    Parses a company size string (e.g., '10-50', 'under 25', 'up to 30', '50-200', '50+',
    'seed stage', 'series a', 'around 40') into (lower, upper) employee bounds.
    """
    if not size_str:
        return None
    raw = str(size_str).lower().replace(",", "").strip()
    if not raw:
        return None

    numbers = [int(n) for n in re.findall(r"\d+", raw)]
    if len(numbers) >= 2:
        low, high = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
        return max(1, low), max(1, high)

    if len(numbers) == 1:
        val = max(1, numbers[0])
        if any(k in raw for k in ("under", "below", "less than", "up to", "max", "at most", "<=", "<")) or raw.startswith("<"):
            return (1, val)
        if any(k in raw for k in ("over", "above", "more than", "at least", "min", "+", ">=", ">")) or raw.endswith("+"):
            return (val, max(val * 5, 500))
        if any(k in raw for k in ("around", "about", "approx", "~")):
            return (max(1, int(val * 0.6)), max(val + 10, int(val * 1.4)))
        return (val, val)

    # Named startup stage presets when no digits are provided
    if any(k in raw for k in ("pre-seed", "preseed", "micro", "tiny")):
        return (1, 10)
    if any(k in raw for k in ("seed", "early stage", "early-stage")):
        return (1, 25)
    if "series a" in raw:
        return (15, 100)
    if any(k in raw for k in ("series b", "growth", "scaleup", "scale-up")):
        return (50, 250)
    if any(k in raw for k in ("small", "startup", "smb")):
        return (10, 50)
    if any(k in raw for k in ("mid", "medium")):
        return (50, 250)
    return None


def parse_company_size_upper_bound(size_str: str | None) -> int | None:
    bounds = parse_company_size_bounds(size_str)
    return bounds[1] if bounds else None


def filter_jobs(
    jobs: list[ATSJob],
    role: str,
    location: str,
    min_company_size: int | None = None,
    max_company_size: int | None = None,
) -> list[ATSJob]:
    """
    Filters ATS jobs by:
    1. Role keywords (normalizing 'python dev' -> core skill 'python' across title and description),
       while excluding intern/graduate/apprentice roles unless explicitly requested.
    2. Regional location aliases across all global developer markets (US, UK, AU, UAE/Dubai, Canada, EU, Singapore, Remote, etc.).
    3. Company size range (min_company_size .. max_company_size) + dynamic open headcount proxy guard.
    """
    raw_terms = _compact(role).split()
    core_terms = [t for t in raw_terms if t not in GENERIC_ROLE_FILLERS]
    role_terms = core_terms if core_terms else raw_terms
    user_wants_intern = bool(NON_DECISION_MAKER_ROLE_PATTERNS.search(role))

    norm_loc = _compact(location)
    location_aliases = set(REGIONAL_LOCATION_ALIASES.get(norm_loc, {norm_loc}))
    location_aliases.discard("")

    # Count total open postings per board as an honest proxy when explicit headcount is absent:
    # A startup with max_company_size employees will rarely have >50% of its entire headcount as simultaneous open roles.
    board_posting_counts: dict[tuple[str, str], int] = {}
    for j in jobs:
        key = (j.platform, j.board_slug)
        board_posting_counts[key] = board_posting_counts.get(key, 0) + 1

    max_open_postings_guard = max(5, int(max_company_size * 0.5)) if max_company_size is not None else None

    matches: list[ATSJob] = []
    for job in jobs:
        # Exclude intern/graduate/student postings when looking for B2B engineering hiring signals
        if not user_wants_intern and NON_DECISION_MAKER_ROLE_PATTERNS.search(job.title):
            continue

        if job.company_size:
            bounds = parse_company_size_bounds(job.company_size)
            if bounds is not None:
                lower_size, upper_size = bounds
                if max_company_size is not None and lower_size > max_company_size:
                    continue
                if max_company_size is not None and upper_size > max_company_size and lower_size > max_company_size:
                    continue
                if max_company_size is not None and upper_size > max_company_size:
                    continue
                if min_company_size is not None and lower_size < min_company_size and upper_size < min_company_size:
                    continue
        elif max_open_postings_guard is not None and max_company_size is not None and max_company_size <= 250:
            if board_posting_counts.get((job.platform, job.board_slug), 0) > max_open_postings_guard:
                continue

        title_loc_text = _compact(f"{job.title} {job.location}")
        # Also allow matching specific technical stack keywords (like 'python') in job description when title is 'Backend Engineer'
        raw_desc = ""
        if isinstance(job.raw, dict):
            raw_desc = _compact(
                str(job.raw.get("descriptionPlain") or job.raw.get("description") or job.raw.get("content") or "")[:1500]
            )
        is_engineering_title = any(
            w in title_loc_text for w in ("engineer", "developer", "python", "backend", "full stack", "fullstack", "software", "data", "ml", "ai", "platform", "devops", "cloud", "frontend")
        )
        searchable_role_text = f"{title_loc_text} {raw_desc}" if is_engineering_title else title_loc_text

        role_matches = not role_terms or all(term in searchable_role_text for term in role_terms)
        location_matches = (
            not location_aliases
            or any(alias in title_loc_text for alias in location_aliases)
            or "remote" in title_loc_text
            or "anywhere" in title_loc_text
            or "worldwide" in title_loc_text
        )
        if role_matches and location_matches:
            matches.append(job)
    return matches

