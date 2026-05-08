from datetime import datetime, timedelta

import pandas as pd
from google.cloud import bigquery
from user_input import get_country_code


class GDELTPipeline:
    """Pulls Ukraine conflict events from GDELT via BigQuery."""

    EVENT_LABELS = {"18": "Assault", "19": "Use of Force", "20": "Mass Violence"}

    def __init__(
        self,
        project_id: str,
        days_back: int = 7,
        max_gb: float = 0.5,
        country_code: str = None,
    ):
        self.project_id = project_id
        self.days_back = days_back
        self.max_gb = max_gb
        self.country_code = country_code or get_country_code()
        self.client = bigquery.Client(project=project_id)
        self.df = None
        print(f"[{self._ts()}] GDELTPipeline initialized — project: {project_id}")

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _build_query(self) -> str:
        start_date = (datetime.utcnow() - timedelta(days=self.days_back)).strftime(
            "%Y-%m-%d"
        )
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
            AND ActionGeo_CountryCode = '{self.country_code}'
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
                if confirm.lower() != "y":
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
        self.df["Actor1Name"] = self.df["Actor1Name"].fillna("Unknown")
        self.df["Actor2Name"] = self.df["Actor2Name"].fillna("Unknown")
        self.df["Actor1CountryCode"] = self.df["Actor1CountryCode"].fillna("")
        self.df["Actor2CountryCode"] = self.df["Actor2CountryCode"].fillna("")
        self.df["ActionGeo_Lat"] = self.df["ActionGeo_Lat"].round(4)
        self.df["ActionGeo_Long"] = self.df["ActionGeo_Long"].round(4)
        self.df["SQLDATE"] = self.df["SQLDATE"].astype(str)
        self.df["DATEADDED"] = self.df["DATEADDED"].astype(str)

    def to_events(self) -> list[dict]:
        """Convert DataFrame to list of event dicts for the dashboard."""
        if self.df is None:
            raise RuntimeError("No data — call fetch() first")
        events = []
        for _, row in self.df.iterrows():
            events.append(
                {
                    "source": "GDELT",
                    "lat": float(row["ActionGeo_Lat"]),
                    "lng": float(row["ActionGeo_Long"]),
                    "location": str(row["ActionGeo_FullName"]),
                    "actor1": str(row["Actor1Name"]),
                    "actor2": str(row["Actor2Name"]),
                    "eventCode": str(row["EventRootCode"]),
                    "eventLabel": self.EVENT_LABELS.get(
                        str(row["EventRootCode"]), "Unknown"
                    ),
                    "goldstein": float(row["GoldsteinScale"]),
                    "mentions": int(row["NumMentions"]),
                    "tone": round(float(row["AvgTone"]), 2),
                    "url": str(row["SOURCEURL"]),
                    "date": str(row["SQLDATE"]),
                }
            )
        return events

    def to_summary(self) -> dict:
        """Build summary statistics for metadata."""
        if self.df is None:
            raise RuntimeError("No data — call fetch() first")
        return {
            "by_date": self.df.groupby("SQLDATE").size().to_dict(),
            "by_event_code": self.df["EventRootCode"].value_counts().to_dict(),
            "by_actor1": self.df["Actor1Name"].value_counts().head(10).to_dict(),
            "avg_goldstein": round(self.df["GoldsteinScale"].mean(), 2),
            "avg_tone": round(self.df["AvgTone"].mean(), 2),
            "total_mentions": int(self.df["NumMentions"].sum()),
        }
