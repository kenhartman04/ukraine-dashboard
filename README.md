# ukraine-dashboard

# Ukraine Conflict Intelligence Dashboard

An open-source OSINT dashboard monitoring the Russia-Ukraine conflict using 
GDELT event data and Ukrainian military Telegram channels.

## Live Dashboard
[View Dashboard](https://yourusername.github.io/ukraine-osint-dashboard)

## Data Sources
- **GDELT Project** — Global Database of Events, Language and Tone
  - Table: `gdelt-bq.gdeltv2.events_partitioned`
  - Filter: EventRootCode IN (18, 19, 20), ActionGeo_CountryCode = 'UP'
- **Telegram** — Official Ukrainian military channels (General Staff, SBU, SOF, brigade channels)
  - Translation: Google Translate via deep-translator
  - Geoparsing: mordecai3 v3.4 neural geoparser

## Pipeline
```
GDELT BigQuery → ukraine_gdelt.csv
Telegram scraper → telegram_osint.json
Translation + mordecai3 geoparsing → telegram_geolocated.json
Combined output → ukraine_combined.json
Dashboard → index.html
```

## Tech Stack
- Python (pandas, google-cloud-bigquery, beautifulsoup4, mordecai3)
- Leaflet.js — interactive mapping
- GitHub Actions — daily automated updates
- GitHub Pages — static hosting

## Setup
```bash
git clone https://github.com/yourusername/ukraine-osint-dashboard
cd ukraine-osint-dashboard
pip install -r requirements.txt
python pipeline.py
```

## Author
Kendall Hartman — [Hartman Analytics LLC](https://hartmananalytics.org)

## License
MIT
