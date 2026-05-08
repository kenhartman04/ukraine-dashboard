import os
import json
import pandas as pd
from google.cloud import bigquery
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import time
import uuid

class NCMSMetadata:
    """
    NATO Core Metadata Specification (ADatP-5636 Ed A V1)
    compliant metadata for OSINT conflict events
    """
    
    def __init__(self, event: dict):
        self.event = event
    
    def build(self) -> dict:
        return {
            # ── SECURITY LAYER (all mandatory) ──────────────────────
            "metadataConfidentialityLabel": "NATO UNCLASSIFIED",
            "originatorConfidentialityLabel": "NATO UNCLASSIFIED",
            
            # ── COMMON LAYER — MANDATORY ─────────────────────────────
            "identifier": str(uuid.uuid4()),              # unique event UUID
            "title": self.event.get("title", 
                     f"OSINT Event {self.event.get('id')}"),
            "creator": "Kendall Hartman",
            "publisher": "Hartman Analytics LLC",
            "dateCreated": datetime.now(timezone.utc).isoformat(),
            
            # ── COMMON LAYER — OPTIONAL (geospatial) ─────────────────
            "countryCode": "UP",                          # ISO 3166-1 alpha-2
            "geographicReference": self.event.get("h3_cell"),
            "geographicEncodingScheme": "H3 v3.7.2",     # CONDITIONAL — required since geographicReference is used
            "placeName": self.event.get("place_name"),    # mordecai3 output
            "region": self.event.get("oblast"),           # e.g. "Kharkiv Oblast"
            "timePeriod": {
                "start": self.event.get("event_date"),
                "end": self.event.get("event_date")
            },
            
            # ── COMMON LAYER — OPTIONAL (provenance/source) ──────────
            "provenance": " → ".join([
                "GDELT v2 BigQuery",
                "mordecai3 geoparsing",
                "H3 r7 indexing",
                "Kuzu knowledge graph",
                "bge-m3 Qdrant embedding"
            ]),
            "source": self.event.get("source_url"),
            "contextActivity": "Ukraine Conflict OSINT Monitoring",
            
            # ── COMMON LAYER — OPTIONAL (description/subject) ────────
            "keyword": [
                "OSINT", "conflict", "Ukraine",
                "geospatial", "GDELT", "civilian harm"
            ],
            "subjectCategory": "PMESII:" + self.event.get("pmesii_domain", "UNKNOWN"),
            "language": self.event.get("language", "en"),
            "type": "OSINTConflictEvent",
            "accessRights": "PUBLIC",
            
            # ── INFORMATION LIFECYCLE SUPPORT LAYER ──────────────────
            "status": "active",
            "updatingFrequency": "daily",
            "version": "1.0"
        }


class GDELTPipeline:
    """Pulls Ukraine conflict events from GDELT via BigQuery."""

    EVENT_LABELS = {
        '18': 'Assault',
        '19': 'Use of Force',
        '20': 'Mass Violence'
    }

    def __init__(self, project_id: str, days_back: int = 7, max_gb: float = 0.5):
        self.project_id = project_id
        self.days_back = days_back
        self.max_gb = max_gb
        self.client = bigquery.Client(project=project_id)
        self.df = None
        print(f"[{self._ts()}] GDELTPipeline initialized — project: {project_id}")

    def _ts(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _build_query(self) -> str:
        start_date = (datetime.utcnow() - timedelta(days=self.days_back)).strftime('%Y-%m-%d')
        print(f"[{self._ts()}] Querying from: {start_date}")
        return f"""
        SELECT
            GLOBALEVENTID,
            SQLDATE,
            Actor1Name,
            Actor1CountryCode,
            Actor2Name,
            Actor2CountryCode,
            EventCode,
            EventBaseCode,
            EventRootCode,
            GoldsteinScale,
            NumMentions,
            NumSources,
            NumArticles,
            AvgTone,
            ActionGeo_FullName,
            ActionGeo_CountryCode,
            ActionGeo_ADM1Code,
            ActionGeo_Type,
            ActionGeo_Lat,
            ActionGeo_Long,
            DATEADDED,
            SOURCEURL
        FROM `gdelt-bq.gdeltv2.events_partitioned`
        WHERE _PARTITIONTIME >= TIMESTAMP('{start_date}')
            AND ActionGeo_CountryCode = 'UP'
            AND EventRootCode IN ('18', '19', '20')
            AND ActionGeo_Lat IS NOT NULL
            AND ActionGeo_Long IS NOT NULL
        ORDER BY SQLDATE DESC, NumMentions DESC
        """

    def dry_run(self) -> float:
        """Check bytes scanned before running. Returns GB."""
        query = self._build_query()
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        dry = self.client.query(query, job_config=job_config)
        gb = dry.total_bytes_processed / 1024**3
        print(f"[{self._ts()}] Query will scan: {gb:.3f} GB")
        return gb

    def fetch(self, auto_confirm: bool = False) -> pd.DataFrame:
        """Run the query and return a cleaned DataFrame."""
        gb = self.dry_run()
        if gb > self.max_gb:
            print(f"WARNING: Query will scan {gb:.3f} GB (limit: {self.max_gb} GB)")
            if not auto_confirm:
                confirm = input("Proceed? (y/n): ")
                if confirm.lower() != 'y':
                    raise RuntimeError("Query aborted by user")
            else:
                print("Auto-confirming (running in automated mode)")

        print(f"[{self._ts()}] Running query...")
        query = self._build_query()
        self.df = self.client.query(query).to_dataframe()
        print(f"[{self._ts()}] Events returned: {len(self.df)}")
        self._clean()
        return self.df

    def _clean(self):
        """Clean and normalize the DataFrame in place."""
        print(f"[{self._ts()}] Cleaning data...")
        self.df['Actor1Name'] = self.df['Actor1Name'].fillna('Unknown')
        self.df['Actor2Name'] = self.df['Actor2Name'].fillna('Unknown')
        self.df['Actor1CountryCode'] = self.df['Actor1CountryCode'].fillna('')
        self.df['Actor2CountryCode'] = self.df['Actor2CountryCode'].fillna('')
        self.df['ActionGeo_Lat'] = self.df['ActionGeo_Lat'].round(4)
        self.df['ActionGeo_Long'] = self.df['ActionGeo_Long'].round(4)
        self.df['SQLDATE'] = self.df['SQLDATE'].astype(str)
        self.df['DATEADDED'] = self.df['DATEADDED'].astype(str)

    def to_events(self) -> list[dict]:
        """Convert DataFrame to list of event dicts for the dashboard."""
        if self.df is None:
            raise RuntimeError("No data — call fetch() first")
        events = []
        for _, row in self.df.iterrows():
            events.append({
                'source': 'GDELT',
                'lat': float(row['ActionGeo_Lat']),
                'lng': float(row['ActionGeo_Long']),
                'location': str(row['ActionGeo_FullName']),
                'actor1': str(row['Actor1Name']),
                'actor2': str(row['Actor2Name']),
                'eventCode': str(row['EventRootCode']),
                'eventLabel': self.EVENT_LABELS.get(str(row['EventRootCode']), 'Unknown'),
                'goldstein': float(row['GoldsteinScale']),
                'mentions': int(row['NumMentions']),
                'tone': round(float(row['AvgTone']), 2),
                'url': str(row['SOURCEURL']),
                'date': str(row['SQLDATE'])
            })
        return events

    def to_summary(self) -> dict:
        """Build summary statistics for metadata."""
        if self.df is None:
            raise RuntimeError("No data — call fetch() first")
        return {
            'by_date': self.df.groupby('SQLDATE').size().to_dict(),
            'by_event_code': self.df['EventRootCode'].value_counts().to_dict(),
            'by_actor1': self.df['Actor1Name'].value_counts().head(10).to_dict(),
            'avg_goldstein': round(self.df['GoldsteinScale'].mean(), 2),
            'avg_tone': round(self.df['AvgTone'].mean(), 2),
            'total_mentions': int(self.df['NumMentions'].sum())
        }

class TelegramPipeline:
  # High value channels from the list
  DEFAULT_CHANNELS = [
      'GeneralStaffZSU',      # Official General Staff
      'landforcesofukraine',  # Ground Forces official
      'SBUkr',                # Security Service of Ukraine
      'ukr_sof',              # Special Operations Forces
      'ua_dshv',              # Air Assault Forces
      'HolodniyYar_93ombr',   # 93rd Mechanized Brigade
      'brigada92_war',        # 92nd Assault Brigade
      'brigade95',            # 95th Air Assault Brigade
      'azov_media',           # 12th Special Purpose Brigade
      'ab3army',              # 3rd Assault Brigade
  ]

  def __init__(self, channels: list = None, delay: int = 5):
      self.channels = channels or self.DEFAULT_CHANNELS
      self.delay = delay  # Delay between requests to avoid rate limits
      self.messages = []
      print(f"[{self._ts()}] TelegramPipeline initialized — channels: {len(self.channels)}")

  def _ts(self) -> str:
      return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

  def _scrape_channel(self, channel: str) -> list[dict]:
          """Scrape a single public Telegram channel via web preview."""
          url = f"https://t.me/s/{channel}"
          headers = {'User-Agent': 'Mozilla/5.0'}

          try:
              response = requests.get(url, headers=headers, timeout=10)
              if response.status_code != 200:
                  print(f"  [{channel}] HTTP {response.status_code} — skipping")
                  return []

              soup = BeautifulSoup(response.text, 'html.parser')
              messages = []
              seen_texts = set()  # deduplication

              for post in soup.find_all('div', class_='tgme_widget_message'):
                  text_div = post.find('div', class_='tgme_widget_message_text')
                  text = text_div.get_text(strip=True) if text_div else ''

                  # skip empty or duplicate messages
                  if not text or text in seen_texts:
                      continue
                  seen_texts.add(text)

                  date_tag = post.find('time')
                  date = date_tag.get('datetime', '') if date_tag else ''

                  link_tag = post.find('a', class_='tgme_widget_message_date')
                  msg_url = link_tag.get('href', '') if link_tag else ''

                  views_tag = post.find('span', class_='tgme_widget_message_views')
                  views = views_tag.get_text(strip=True) if views_tag else '0'

                  messages.append({
                      'source': 'Telegram',
                      'channel': channel,
                      'text': text[:500],
                      'date': date,
                      'url': msg_url,
                      'views': views,
                      'scraped_at': datetime.now(timezone.utc).isoformat()
                  })

              return messages[:self.limit_per_channel]

          except Exception as e:
              print(f"  [{channel}] ERROR: {e}")
              return []


  def fetch(self) -> list[dict]:
      """Scrape all channels and return combined message list."""
      print(f"[{self._ts()}] Step 2: Scraping Telegram channels...")
      self.messages = []

      for channel in self.channels:
          print(f"  Scraping {channel}...")
          msgs = self._scrape_channel(channel)
          self.messages.extend(msgs)
          print(f"  └─ {len(msgs)} messages")
          time.sleep(self.delay)

      print(f"[{self._ts()}] Telegram total: {len(self.messages)} messages")
      return self.messages

  def to_events(self) -> list[dict]:
      """Return messages formatted for the dashboard."""
      if not self.messages:
          raise RuntimeError("No data — call fetch() first")
      return self.messages
class OSINTPipeline:
    """Orchestrates all data sources and writes combined output."""

    def __init__(self, output_path: str = 'ukraine_combined.json'):
        self.output_path = Path(output_path)
        self.gdelt_events = []
        self.telegram_events = []

    def _ts(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def run_gdelt(self, project_id: str, days_back: int = 7, auto_confirm: bool = False):
        """Run the GDELT pipeline and store events."""
        print(f"[{self._ts()}] Step 1: GDELT pipeline...")
        gdelt = GDELTPipeline(project_id=project_id, days_back=days_back)
        gdelt.fetch(auto_confirm=auto_confirm)
        self.gdelt_events = gdelt.to_events()
        print(f"[{self._ts()}] GDELT events: {len(self.gdelt_events)}")

    def save(self):
        """Write combined JSON output."""
        print(f"[{self._ts()}] Saving combined output...")
        output = {
            'generated': datetime.now(timezone.utc).isoformat(),
            'summary': {
                'gdelt_count': len(self.gdelt_events),
                'telegram_count': len(self.telegram_events),
                'total': len(self.gdelt_events) + len(self.telegram_events)
            },
            'gdelt_events': self.gdelt_events,
            'telegram_events': self.telegram_events
        }
        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        size_kb = self.output_path.stat().st_size / 1024
        print(f"[{self._ts()}] Saved to {self.output_path} ({size_kb:.1f} KB)")


if __name__ == '__main__':
    load_dotenv()

    # Detect if running in GitHub Actions
    auto_confirm = os.environ.get('GITHUB_ACTIONS') == 'true'

    pipeline = OSINTPipeline(output_path='ukraine_combined.json')
    pipeline.run_gdelt(
        project_id='gdelt-reader-492021',
        days_back=7,
        auto_confirm=auto_confirm
    )
    pipeline.save()

    print(f"Pipeline complete — {pipeline.gdelt_events.__len__()} GDELT + {pipeline.telegram_events.__len__()} Telegram events")