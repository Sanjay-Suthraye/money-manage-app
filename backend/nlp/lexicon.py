"""Static keyword tables for the tier-1 parser. Pure data, stdlib only.

Same shape as backend/importer/moneymanager.py's SYNONYMS/TYPE_VALUES tables:
module-level dicts, matched case-insensitively on word boundaries.

Category targets name the SEEDED defaults from backend/seed.py. They are matched
against the user's real category names loosely (emoji stripped, case-folded, see
parser.match_merchant), because a user's "🍜 Food" must still match the "Food"
target and a user who renamed or deleted a category simply gets no match.

This is a global prior, not a rule: whatever a user confirms for a phrase is
stored per-user in ParsePattern (tier 2) and overrides everything here.
"""

# ---- merchant / keyword -> (parent category, child category or None) ----
# Keys are matched longest-first (see parser._MERCHANT_KEYS), so a multi-word key
# like "swiggy instamart" wins over "swiggy".
MERCHANTS: dict[str, tuple[str, str | None]] = {
    # Food > Delivery
    "swiggy": ("Food", "Delivery"), "zomato": ("Food", "Delivery"),
    "eatsure": ("Food", "Delivery"), "box8": ("Food", "Delivery"),
    "faasos": ("Food", "Delivery"), "dominos": ("Food", "Delivery"),
    "domino's": ("Food", "Delivery"), "pizza hut": ("Food", "Delivery"),
    "mcdonalds": ("Food", "Delivery"), "kfc": ("Food", "Delivery"),
    "burger king": ("Food", "Delivery"), "subway": ("Food", "Delivery"),
    "magicpin": ("Food", "Delivery"), "takeaway": ("Food", "Delivery"),
    # Food > Restaurants
    "restaurant": ("Food", "Restaurants"), "lunch": ("Food", "Restaurants"),
    "dinner": ("Food", "Restaurants"), "breakfast": ("Food", "Restaurants"),
    "brunch": ("Food", "Restaurants"), "cafe": ("Food", "Restaurants"),
    "starbucks": ("Food", "Restaurants"), "ccd": ("Food", "Restaurants"),
    "cafe coffee day": ("Food", "Restaurants"), "third wave": ("Food", "Restaurants"),
    "barbeque nation": ("Food", "Restaurants"), "haldiram": ("Food", "Restaurants"),
    "biryani": ("Food", "Restaurants"), "thali": ("Food", "Restaurants"),
    "tiffin": ("Food", "Restaurants"), "mess": ("Food", "Restaurants"),
    "dhaba": ("Food", "Restaurants"), "buffet": ("Food", "Restaurants"),
    # Food > Snacks
    "chai": ("Food", "Snacks"), "tea": ("Food", "Snacks"),
    "coffee": ("Food", "Snacks"), "snack": ("Food", "Snacks"),
    "samosa": ("Food", "Snacks"), "vada pav": ("Food", "Snacks"),
    "pani puri": ("Food", "Snacks"), "chaat": ("Food", "Snacks"),
    "ice cream": ("Food", "Snacks"), "juice": ("Food", "Snacks"),
    "bakery": ("Food", "Snacks"), "biscuit": ("Food", "Snacks"),
    # Groceries
    "swiggy instamart": ("Groceries", None), "instamart": ("Groceries", None),
    "bigbasket": ("Groceries", None), "big basket": ("Groceries", None),
    "blinkit": ("Groceries", None), "grofers": ("Groceries", None),
    "zepto": ("Groceries", None), "dmart": ("Groceries", None),
    "d mart": ("Groceries", None), "dunzo": ("Groceries", None),
    "jiomart": ("Groceries", None), "reliance fresh": ("Groceries", None),
    "more supermarket": ("Groceries", None), "spencers": ("Groceries", None),
    "natures basket": ("Groceries", None), "kirana": ("Groceries", None),
    "grocery": ("Groceries", None), "groceries": ("Groceries", None),
    "sabzi": ("Groceries", None), "vegetable": ("Groceries", None),
    "milk": ("Groceries", None), "amul": ("Groceries", None),
    "country delight": ("Groceries", None), "supermarket": ("Groceries", None),
    "provisions": ("Groceries", None),
    # Transport > Cab
    "uber": ("Transport", "Cab"), "ola": ("Transport", "Cab"),
    "rapido": ("Transport", "Cab"), "cab": ("Transport", "Cab"),
    "taxi": ("Transport", "Cab"), "auto": ("Transport", "Cab"),
    "rickshaw": ("Transport", "Cab"), "namma yatri": ("Transport", "Cab"),
    "blusmart": ("Transport", "Cab"),
    # Transport > Metro
    "metro": ("Transport", "Metro"), "bmtc": ("Transport", "Metro"),
    "best bus": ("Transport", "Metro"), "bus": ("Transport", "Metro"),
    "local train": ("Transport", "Metro"), "bus pass": ("Transport", "Metro"),
    # Transport > Fuel
    "petrol": ("Transport", "Fuel"), "diesel": ("Transport", "Fuel"),
    "fuel": ("Transport", "Fuel"), "hp petrol": ("Transport", "Fuel"),
    "indian oil": ("Transport", "Fuel"), "iocl": ("Transport", "Fuel"),
    "bharat petroleum": ("Transport", "Fuel"), "cng": ("Transport", "Fuel"),
    # Transport (parent only)
    "fastag": ("Transport", None), "toll": ("Transport", None),
    "parking": ("Transport", None), "puncture": ("Transport", None),
    "servicing": ("Transport", None),
    # Shopping
    "amazon": ("Shopping", None), "flipkart": ("Shopping", None),
    "nykaa": ("Shopping", None), "ikea": ("Shopping", None),
    "pepperfry": ("Shopping", None), "urban ladder": ("Shopping", None),
    "myntra": ("Shopping", "Clothes"), "ajio": ("Shopping", "Clothes"),
    "meesho": ("Shopping", "Clothes"), "zara": ("Shopping", "Clothes"),
    "h&m": ("Shopping", "Clothes"), "uniqlo": ("Shopping", "Clothes"),
    "decathlon": ("Shopping", "Clothes"), "shoe": ("Shopping", "Clothes"),
    "shoes": ("Shopping", "Clothes"), "clothes": ("Shopping", "Clothes"),
    "shirt": ("Shopping", "Clothes"), "tshirt": ("Shopping", "Clothes"),
    "jeans": ("Shopping", "Clothes"), "kurta": ("Shopping", "Clothes"),
    "croma": ("Shopping", "Electronics"),
    "reliance digital": ("Shopping", "Electronics"),
    "vijay sales": ("Shopping", "Electronics"),
    "apple store": ("Shopping", "Electronics"),
    "laptop": ("Shopping", "Electronics"), "headphone": ("Shopping", "Electronics"),
    "headphones": ("Shopping", "Electronics"), "charger": ("Shopping", "Electronics"),
    "mobile phone": ("Shopping", "Electronics"),
    # Bills & Utilities
    "electricity": ("Bills & Utilities", "Electricity"),
    "bescom": ("Bills & Utilities", "Electricity"),
    "mseb": ("Bills & Utilities", "Electricity"),
    "adani electricity": ("Bills & Utilities", "Electricity"),
    "tata power": ("Bills & Utilities", "Electricity"),
    "current bill": ("Bills & Utilities", "Electricity"),
    "airtel fiber": ("Bills & Utilities", "Internet"),
    "act fibernet": ("Bills & Utilities", "Internet"),
    "hathway": ("Bills & Utilities", "Internet"),
    "broadband": ("Bills & Utilities", "Internet"),
    "wifi": ("Bills & Utilities", "Internet"),
    "internet": ("Bills & Utilities", "Internet"),
    "jio": ("Bills & Utilities", "Mobile"), "airtel": ("Bills & Utilities", "Mobile"),
    "vodafone": ("Bills & Utilities", "Mobile"), "bsnl": ("Bills & Utilities", "Mobile"),
    "recharge": ("Bills & Utilities", "Mobile"),
    "mobile bill": ("Bills & Utilities", "Mobile"),
    "gas cylinder": ("Bills & Utilities", None), "lpg": ("Bills & Utilities", None),
    "indane": ("Bills & Utilities", None), "water bill": ("Bills & Utilities", None),
    "maintenance": ("Bills & Utilities", None), "dth": ("Bills & Utilities", None),
    "tata sky": ("Bills & Utilities", None),
    # Entertainment
    "netflix": ("Entertainment", "Subscriptions"),
    "prime video": ("Entertainment", "Subscriptions"),
    "hotstar": ("Entertainment", "Subscriptions"),
    "jiohotstar": ("Entertainment", "Subscriptions"),
    "sonyliv": ("Entertainment", "Subscriptions"),
    "zee5": ("Entertainment", "Subscriptions"),
    "spotify": ("Entertainment", "Subscriptions"),
    "youtube premium": ("Entertainment", "Subscriptions"),
    "apple music": ("Entertainment", "Subscriptions"),
    "subscription": ("Entertainment", "Subscriptions"),
    "pvr": ("Entertainment", "Movies"), "inox": ("Entertainment", "Movies"),
    "cinepolis": ("Entertainment", "Movies"), "bookmyshow": ("Entertainment", "Movies"),
    "movie": ("Entertainment", "Movies"), "cinema": ("Entertainment", "Movies"),
    "concert": ("Entertainment", None), "steam": ("Entertainment", None),
    # Health
    "pharmeasy": ("Health", "Pharmacy"), "1mg": ("Health", "Pharmacy"),
    "tata 1mg": ("Health", "Pharmacy"), "netmeds": ("Health", "Pharmacy"),
    "apollo pharmacy": ("Health", "Pharmacy"), "medicine": ("Health", "Pharmacy"),
    "medicines": ("Health", "Pharmacy"), "pharmacy": ("Health", "Pharmacy"),
    "chemist": ("Health", "Pharmacy"), "tablets": ("Health", "Pharmacy"),
    "doctor": ("Health", "Doctor"), "clinic": ("Health", "Doctor"),
    "hospital": ("Health", "Doctor"), "consultation": ("Health", "Doctor"),
    "dentist": ("Health", "Doctor"), "practo": ("Health", "Doctor"),
    "lab test": ("Health", None), "thyrocare": ("Health", None),
    "gym": ("Health", None), "cult fit": ("Health", None), "cultfit": ("Health", None),
    # Travel
    "irctc": ("Travel", None), "indigo": ("Travel", None),
    "makemytrip": ("Travel", None), "goibibo": ("Travel", None),
    "cleartrip": ("Travel", None), "yatra": ("Travel", None),
    "oyo": ("Travel", None), "airbnb": ("Travel", None), "hotel": ("Travel", None),
    "flight": ("Travel", None), "train ticket": ("Travel", None),
    "redbus": ("Travel", None), "vistara": ("Travel", None),
    "air india": ("Travel", None), "visa fee": ("Travel", None),
    # Rent / Education / Other
    "rent": ("Rent", None), "house rent": ("Rent", None),
    "nobroker": ("Rent", None), "landlord": ("Rent", None),
    "school fee": ("Education", None), "college fee": ("Education", None),
    "tuition": ("Education", None), "coaching": ("Education", None),
    "udemy": ("Education", None), "coursera": ("Education", None),
    "stationery": ("Education", None), "exam fee": ("Education", None),
    "salon": ("Other", None), "haircut": ("Other", None), "barber": ("Other", None),
    "laundry": ("Other", None), "dry clean": ("Other", None),
    "donation": ("Other", None), "temple": ("Other", None),
    "courier": ("Other", None), "maid": ("Other", None), "cook": ("Other", None),
    "insurance": ("Other", None), "lic": ("Other", None), "sip": ("Other", None),
}

# Consulted only when the detected type is "income".
INCOME_MERCHANTS: dict[str, tuple[str, str | None]] = {
    "salary": ("Salary", None), "payroll": ("Salary", None),
    "stipend": ("Salary", None), "bonus": ("Salary", None),
    "freelance": ("Business", None), "client payment": ("Business", None),
    "invoice": ("Business", None), "consulting": ("Business", None),
    "interest": ("Interest", None), "fd interest": ("Interest", None),
    "dividend": ("Interest", None), "gift received": ("Gifts", None),
    "shagun": ("Gifts", None), "cashback": ("Other Income", None),
    "refund": ("Other Income", None), "reimbursement": ("Other Income", None),
    "reimbursed": ("Other Income", None),
}

# A match on the key is suppressed when any trap phrase is also present, e.g.
# "auto debit" is a standing instruction, not an auto-rickshaw.
AMBIGUOUS_GUARDS: dict[str, tuple[str, ...]] = {
    "auto": ("autopay", "auto debit", "auto-debit", "automatic", "autodebit"),
    "steam": ("steam iron", "steamer"),
    "tea": ("tea kettle",),
    "mess": ("messed", "message"),
}

# .title() mangles these, and the matched key becomes the transaction note.
DISPLAY: dict[str, str] = {
    "bigbasket": "BigBasket", "big basket": "BigBasket", "dmart": "DMart",
    "d mart": "DMart", "jiomart": "JioMart", "irctc": "IRCTC", "ccd": "CCD",
    "lpg": "LPG", "cng": "CNG", "dth": "DTH", "sip": "SIP", "lic": "LIC",
    "1mg": "1mg", "tata 1mg": "Tata 1mg", "h&m": "H&M", "bmtc": "BMTC",
    "iocl": "IOCL", "bsnl": "BSNL", "pvr": "PVR", "oyo": "OYO",
    "kfc": "KFC", "mcdonalds": "McDonald's", "dominos": "Domino's",
    "domino's": "Domino's", "hdfc": "HDFC", "icici": "ICICI", "sbi": "SBI",
    "upi": "UPI", "gpay": "GPay", "phonepe": "PhonePe", "paytm": "Paytm",
    "act fibernet": "ACT Fibernet", "mseb": "MSEB", "bescom": "BESCOM",
    "zee5": "ZEE5", "sonyliv": "SonyLIV", "jiohotstar": "JioHotstar",
    "pharmeasy": "PharmEasy", "makemytrip": "MakeMyTrip", "redbus": "redBus",
    "nobroker": "NoBroker", "cultfit": "Cult.fit", "cult fit": "Cult.fit",
}

# Payment-method words -> an ACCOUNT TYPE. Resolved to a concrete account only
# when the user has exactly one non-archived account of that type; a wrong guess
# here is one tap to fix on the confirmation chip.
# Mirrors the substring sniffing at backend/importer/moneymanager.py:404-410.
ACCOUNT_TYPE_HINTS: dict[str, str] = {
    "cash": "cash", "in cash": "cash", "by cash": "cash", "nakad": "cash",
    "upi": "bank", "gpay": "bank", "google pay": "bank", "phonepe": "bank",
    "bhim": "bank", "netbanking": "bank", "net banking": "bank",
    "imps": "bank", "neft": "bank", "rtgs": "bank", "bank transfer": "bank",
    "debit card": "bank", "cheque": "bank",
    "credit card": "credit_card", "cc": "credit_card",
    "on card": "credit_card", "card": "credit_card",
    "paytm": "wallet", "wallet": "wallet", "amazon pay": "wallet",
    "mobikwik": "wallet", "freecharge": "wallet", "jio money": "wallet",
}

# (regex, transaction type, confidence). FIRST MATCH WINS, so order is the whole
# design: most specific first. "raj paid me back" must be tested before the
# broader "paid ... back", which would otherwise claim it as repay_out and get the
# direction of the money exactly backwards. Likewise both repayment forms come
# before the bare "paid"/"gave" expense patterns at the bottom.
TYPE_PATTERNS: tuple[tuple[str, str, float], ...] = (
    (r"\bpaid me back\b|\brepaid me\b|\bgave (it )?back\b|\breturned the money\b"
     r"|\bsettled with me\b|\bpaid\s+me\b", "repay_in", 0.90),
    (r"\bpaid\b.*\bback\b|\brepaid\b|\bsettled up\b|\breturned to\b"
     r"|\bpaid off\b.*\bloan\b", "repay_out", 0.90),
    (r"\blent\b|\bloaned\b|\bspotted\b|\bowes me\b|\bgave\b.+\bto\b", "lend", 0.85),
    (r"\bborrowed\b|\btook\b.+\bfrom\b|\bi owe\b|\budhaar\b", "borrow", 0.85),
    (r"\btransferr?ed\b|\bmoved\b|\bwithdrew\b|\bwithdrawal\b|\batm\b"
     r"|\bdeposited\b|\bcard bill\b|\bbill payment\b|\btopped? up\b", "transfer", 0.90),
    (r"\bsalary\b|\breceived\b|\bcredited\b|\bgot paid\b|\brefund(ed)?\b"
     r"|\bcashback\b|\bdividend\b|\bbonus\b|\bstipend\b|\breimburs", "income", 0.85),
    (r"\bspent\b|\bbought\b|\bpurchased\b|\bpaid\b", "expense", 0.95),
)

# ~85% of real entries. Used when no TYPE_PATTERNS row matches.
DEFAULT_TYPE = ("expense", 0.90)

WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Dropped when building the tier-2 phrase key, so "spent 450 on groceries at
# bigbasket" and "450 groceries bigbasket" collapse to the same signature.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "for", "on", "at", "in", "to", "of", "from", "with",
    "and", "or", "my", "me", "i", "it", "this", "that", "was", "is", "were",
    "spent", "paid", "pay", "bought", "buy", "purchased", "got", "gave",
    "give", "some", "rs", "rupees", "inr", "just", "today", "yesterday",
    "about", "around", "approx", "also", "then", "did", "do", "have", "has",
    "am", "are", "be", "been", "via", "by", "using", "used", "worth",
})

# Words that signal "there was a date phrase here I could not parse", so the
# result is escalated rather than silently defaulting to today.
DATEISH = (
    r"\b(last|next|this|ago|weekend|month|week|fortnight|eve|night|morning"
    r"|evening|afternoon|diwali|holi|christmas|eid|pongal|onam|dussehra"
    r"|birthday|anniversary)\b"
)
