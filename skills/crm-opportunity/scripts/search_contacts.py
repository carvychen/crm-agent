# Search for contacts by name in Dynamics 365 CRM.
# Returns a JSON list of {id, fullname} matches.
#
# Usage:
#   python scripts/search_contacts.py --name "John"

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
        description="Search for contacts by name. Returns matching contact GUIDs.",
    )
    parser.add_argument("--name", required=True, help="Contact name search keyword.")
    args = parser.parse_args()

    try:
        client = get_client()
        results = client.search_contacts(args.name)
        print(json.dumps(results, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
