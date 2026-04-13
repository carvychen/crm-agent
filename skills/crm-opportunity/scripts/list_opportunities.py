# List opportunities from Dynamics 365 CRM with optional OData filters.
# Returns a JSON array of formatted opportunity objects.
#
# Usage:
#   python scripts/list_opportunities.py
#   python scripts/list_opportunities.py --filter "opportunityratingcode eq 1"
#   python scripts/list_opportunities.py --filter "estimatedvalue gt 50000" --order-by "estimatedvalue desc" --top 5

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from dataverse_client import get_client, OpportunityClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List opportunities with optional OData filters, sorting, and limits.",
    )
    parser.add_argument("--filter", default="", help="OData $filter expression.")
    parser.add_argument("--order-by", default="", help="OData $orderby expression.")
    parser.add_argument("--top", type=int, default=None, help="Maximum number of records to return.")
    args = parser.parse_args()

    try:
        client = get_client()
        opps = client.list(
            filter_expr=args.filter or None,
            order_by=args.order_by or None,
            top=args.top,
        )
        formatted = [OpportunityClient.format_opportunity(o) for o in opps]
        print(json.dumps(formatted, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
