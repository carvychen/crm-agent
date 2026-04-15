# Delete an opportunity from Dynamics 365 CRM.
# Returns confirmation JSON on success.
#
# Usage:
#   python scripts/delete_opportunity.py --opportunity-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

import argparse
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "lib"))

from dotenv import load_dotenv
load_dotenv()

from dataverse_client import get_client


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete an opportunity by its GUID.",
    )
    parser.add_argument("--opportunity-id", required=True, help="Opportunity GUID to delete.")
    args = parser.parse_args()

    try:
        client = get_client()
        client.delete(args.opportunity_id)
        print(json.dumps({"deleted": args.opportunity_id}))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
