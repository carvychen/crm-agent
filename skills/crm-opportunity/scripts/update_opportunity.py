# Update an existing opportunity in Dynamics 365 CRM.
# Returns the updated opportunity as JSON.
#
# Usage:
#   python scripts/update_opportunity.py --opportunity-id "xxxx-guid" --closeprobability 75
#   python scripts/update_opportunity.py --opportunity-id "xxxx-guid" --name "New Name" --estimatedvalue 95000

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "lib"))

from dotenv import load_dotenv
load_dotenv()

from dataverse_client import get_client, OpportunityClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update fields on an existing opportunity.",
    )
    parser.add_argument("--opportunity-id", required=True, help="Opportunity GUID.")
    parser.add_argument("--name", default="", help="New topic/title.")
    parser.add_argument("--estimatedvalue", type=float, default=None, help="New expected deal value.")
    parser.add_argument("--estimatedclosedate", default="", help="New close date (YYYY-MM-DD).")
    parser.add_argument("--closeprobability", type=int, default=None, help="New win probability 0-100.")
    parser.add_argument("--opportunityratingcode", type=int, default=None, help="New rating: 1=Hot, 2=Warm, 3=Cold.")
    args = parser.parse_args()

    try:
        client = get_client()
        data: dict[str, Any] = {}
        if args.name:
            data["name"] = args.name
        if args.estimatedvalue is not None:
            data["estimatedvalue"] = args.estimatedvalue
        if args.estimatedclosedate:
            data["estimatedclosedate"] = args.estimatedclosedate
        if args.closeprobability is not None:
            data["closeprobability"] = args.closeprobability
        if args.opportunityratingcode is not None:
            data["opportunityratingcode"] = args.opportunityratingcode

        client.update(args.opportunity_id, data)
        opp = client.get(args.opportunity_id)
        print(json.dumps({"updated": OpportunityClient.format_opportunity(opp)}, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
