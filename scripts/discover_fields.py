"""
Discovers all available fields on the Opportunity entity by:
  1. Fetching one real record with all fields (no $select)
  2. Fetching Dataverse metadata (attribute definitions) for rich type info

Run from project root:
    python scripts/discover_fields.py
"""

import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv
from azure.identity import ClientSecretCredential

load_dotenv()

# ── Auth ─────────────────────────────────────────────────────────────────────
DATAVERSE_URL = os.environ["DATAVERSE_URL"].rstrip("/")
API_BASE = f"{DATAVERSE_URL}/api/data/v9.2"
SCOPE = f"{DATAVERSE_URL}/.default"

credential = ClientSecretCredential(
    tenant_id=os.environ["AZURE_TENANT_ID"],
    client_id=os.environ["AZURE_CLIENT_ID"],
    client_secret=os.environ["AZURE_CLIENT_SECRET"],
)

def headers():
    token = credential.get_token(SCOPE).token
    return {
        "Authorization": f"Bearer {token}",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Accept": "application/json",
    }


# ── Step 1: Fetch one real record (all fields) ───────────────────────────────
print("=" * 60)
print("STEP 1 — One real Opportunity record (all fields)")
print("=" * 60)

resp = requests.get(
    f"{API_BASE}/opportunities",
    headers=headers(),
    params={"$top": 1},
)
resp.raise_for_status()
records = resp.json().get("value", [])

if records:
    record = records[0]
    print(f"\nRecord: {record.get('name', record.get('opportunityid'))}\n")
    # Group into non-null and null fields
    non_null = {k: v for k, v in record.items() if v is not None and not k.startswith("@")}
    null_fields = [k for k, v in record.items() if v is None and not k.startswith("@")]

    print("── Non-null fields ──")
    for k, v in sorted(non_null.items()):
        print(f"  {k:<50} = {str(v)[:80]}")

    print(f"\n── Null fields ({len(null_fields)}) ──")
    for k in sorted(null_fields):
        print(f"  {k}")
else:
    print("No opportunity records found.")


# ── Step 2: Metadata — all attribute definitions ─────────────────────────────
print("\n" + "=" * 60)
print("STEP 2 — Metadata: all Opportunity attributes")
print("=" * 60)

meta_resp = requests.get(
    f"{DATAVERSE_URL}/api/data/v9.2/EntityDefinitions(LogicalName='opportunity')/Attributes",
    headers=headers(),
    params={
        "$select": "LogicalName,DisplayName,AttributeType,RequiredLevel,IsValidForCreate,IsValidForUpdate",
        "$filter": "IsValidForRead eq true",
        "$orderby": "LogicalName",
    },
)
meta_resp.raise_for_status()
attributes = meta_resp.json().get("value", [])

print(f"\nTotal readable attributes: {len(attributes)}\n")
print(f"{'LogicalName':<45} {'Type':<20} {'Required':<12} {'Create':<8} {'Update':<8} DisplayName")
print("-" * 130)

for attr in attributes:
    name = attr.get("LogicalName", "")
    atype = attr.get("AttributeType", "")
    required = attr.get("RequiredLevel", {}).get("Value", "") if isinstance(attr.get("RequiredLevel"), dict) else ""
    can_create = "✓" if attr.get("IsValidForCreate") else ""
    can_update = "✓" if attr.get("IsValidForUpdate") else ""
    display = ""
    dn = attr.get("DisplayName", {})
    if isinstance(dn, dict):
        lv = dn.get("UserLocalizedLabel") or {}
        display = lv.get("Label", "") if isinstance(lv, dict) else ""
    print(f"  {name:<43} {atype:<20} {required:<12} {can_create:<8} {can_update:<8} {display}")

# ── Step 3: Fetch top 5 records with key fields ──────────────────────────────
print("\n" + "=" * 60)
print("STEP 3 — First 5 Opportunity records (key fields)")
print("=" * 60)

resp2 = requests.get(
    f"{API_BASE}/opportunities",
    headers=headers(),
    params={
        "$select": "opportunityid,name,estimatedvalue,estimatedclosedate,statecode,statuscode,_parentaccountid_value,_parentcontactid_value,_ownerid_value,createdon",
        "$top": 5,
        "$orderby": "createdon desc",
    },
)
resp2.raise_for_status()
print(json.dumps(resp2.json().get("value", []), indent=2, ensure_ascii=False, default=str))
