from pathlib import Path

import pandas as pd
from gdelt_pipeline import GDELTPipeline
from user_input import load_config

# load config


BASE_DIR = Path(__file__).resolve().parent.parent

config = load_config(BASE_DIR / "config.json")
if __name__ == "__main__":
    pipeline = GDELTPipeline(
        project_id=config["project_id"],
        days_back=config["days_back"],
        max_gb=config.get("max_gb", 0.5),
        country_code=config["country_code"],
    )

    df = pipeline.fetch()

    # Convert DataFrame to JSON format
    events_json = pipeline.to_events()
    json_output = pd.json_normalize(events_json)

    print(
        json_output.to_json(
            BASE_DIR / "output" / "test.json", orient="records", indent=4
        )
    )
