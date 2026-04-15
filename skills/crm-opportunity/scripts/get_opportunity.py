# Get a single opportunity by GUID from Dynamics 365 CRM.
# Returns the formatted opportunity object as JSON.
#
# Usage:
#   python scripts/get_opportunity.py --opportunity-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

import argparse
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "lib"))

from dotenv import load_dotenv
load_dotenv()

from dataverse_client import get_client, OpportunityClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Get a single opportunity by its GUID.",
    )
    parser.add_argument("--opportunity-id", required=True, help="Opportunity GUID.")
    args = parser.parse_args()

    try:
        client = get_client()
        opp = client.get(args.opportunity_id)
        print(json.dumps(OpportunityClient.format_opportunity(opp), ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
