# Create a new opportunity in Dynamics 365 CRM.
# Returns the created opportunity as JSON.
#
# Usage:
#   python scripts/create_opportunity.py --name "Enterprise Deal" --account-id "Fourth Coffee"
#   python scripts/create_opportunity.py --name "New Deal" --account-id "xxxx-guid" --estimatedvalue 85000 --closeprobability 60

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from dataverse_client import get_client, OpportunityClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a new opportunity. Requires name and account-id (GUID or account name).",
    )
    parser.add_argument("--name", required=True, help="Opportunity topic/title.")
    parser.add_argument("--account-id", default="", help="Account GUID or name (auto-resolved).")
    parser.add_argument("--contact-id", default="", help="Contact GUID or name (auto-resolved). Use instead of account-id.")
    parser.add_argument("--estimatedvalue", type=float, default=None, help="Expected deal value.")
    parser.add_argument("--estimatedclosedate", default="", help="Expected close date (YYYY-MM-DD).")
    parser.add_argument("--closeprobability", type=int, default=None, help="Win probability 0-100.")
    parser.add_argument("--opportunityratingcode", type=int, default=None, help="Rating: 1=Hot, 2=Warm, 3=Cold.")
    parser.add_argument("--parentcontactid", default="", help="Related contact GUID or name.")
    args = parser.parse_args()

    try:
        client = get_client()
        data: dict[str, Any] = {"name": args.name}

        if args.account_id:
            resolved = client.resolve_account_id(args.account_id)
            data["customerid_account@odata.bind"] = f"/accounts({resolved})"
            data["parentaccountid@odata.bind"] = f"/accounts({resolved})"
        elif args.contact_id:
            resolved = client.resolve_contact_id(args.contact_id)
            data["customerid_contact@odata.bind"] = f"/contacts({resolved})"
            data["parentcontactid@odata.bind"] = f"/contacts({resolved})"

        if args.estimatedvalue is not None:
            data["estimatedvalue"] = args.estimatedvalue
        if args.closeprobability is not None:
            data["closeprobability"] = args.closeprobability
        if args.opportunityratingcode is not None:
            data["opportunityratingcode"] = args.opportunityratingcode
        if args.estimatedclosedate:
            data["estimatedclosedate"] = args.estimatedclosedate
        if args.parentcontactid:
            resolved = client.resolve_contact_id(args.parentcontactid)
            data["parentcontactid@odata.bind"] = f"/contacts({resolved})"

        new_id = client.create(data)
        opp = client.get(new_id)
        print(json.dumps({"created": OpportunityClient.format_opportunity(opp)}, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
