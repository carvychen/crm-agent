import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from dataverse_client import get_client

client = get_client()
opps = client.list(filter_expr="contains(name, 'API Test')", select="opportunityid,name")

if not opps:
    print("No API Test records found.")
else:
    for o in opps:
        print(f"Deleting: {o['name']} ({o['opportunityid']})")
        client.delete(o["opportunityid"])
        print("  Done.")
