import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_config(fpath):
    with open(BASE_DIR / fpath) as f:
        config = json.load(f)
        return config


def load_countries():
    with open(BASE_DIR / "data" / "LOOKUP-COUNTRIES.json") as f:
        countries = json.load(f)
        return countries


def validate_country_code(country_code):
    countries = load_countries()
    valid_codes = {c["code"] for c in countries if "code" in c}
    if country_code not in valid_codes:
        raise ValueError(f"Invalid country code: {country_code}")
    return True


# load country code from config and validate from lookup table
def get_country_code():
    config = load_config("config.json")
    return validate_country_code(config["countryCode"])
