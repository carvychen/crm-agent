"""
Opportunity CRUD examples — fields: Topic, Potential Customer,
Est. Close Date, Est. Revenue, Contact, Account, Probability, Rating.

Run from project root:
    python scripts/example.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from dataverse_client import build_client_from_env, OpportunityClient

client = build_client_from_env()


FV = "OData.Community.Display.V1.FormattedValue"


def print_opps(label: str, opps: list | dict):
    print(f"\n{'─' * 64}")
    print(f"  {label}")
    print('─' * 64)
    rows = [opps] if isinstance(opps, dict) else opps
    for o in rows:
        rating = o.get(f"opportunityratingcode@{FV}") or OpportunityClient.RATING.get(o.get("opportunityratingcode"), "-")
        rev = o.get("estimatedvalue")
        account = o.get(f"_parentaccountid_value@{FV}") or o.get("_parentaccountid_value", "-")
        contact = o.get(f"_parentcontactid_value@{FV}") or o.get("_parentcontactid_value", "-")
        customer = o.get(f"_customerid_value@{FV}") or o.get("_customerid_value", "-")
        print(
            f"  {o.get('name', '-')}\n"
            f"    ID       : {o.get('opportunityid', '-')}\n"
            f"    Customer : {customer}\n"
            f"    Close    : {o.get('estimatedclosedate', '-')}   "
            f"Revenue: {'${:,.0f}'.format(rev) if rev is not None else '-'}\n"
            f"    Account  : {account}\n"
            f"    Contact  : {contact}\n"
            f"    Prob     : {o.get('closeprobability', '-')}%   Rating: {rating}\n"
        )


# ── 1. List all opportunities ────────────────────────────────────────────────
print("\n=== 1. All Opportunities ===")
all_opps = client.list(order_by="estimatedvalue desc")
print_opps(f"{len(all_opps)} opportunities", all_opps)


# ── 2. Filter: revenue > $20,000 ────────────────────────────────────────────
print("\n=== 2. Revenue > $20,000 ===")
big = client.list(
    filter_expr="estimatedvalue gt 20000",
    order_by="estimatedvalue desc",
    top=5,
)
print_opps("Top 5 by revenue", big)


# ── 3. Filter: Hot rating ────────────────────────────────────────────────────
print("\n=== 3. Hot Rating ===")
hot = client.list(filter_expr="opportunityratingcode eq 1")  # 1 = Hot
print_opps("Hot opportunities", hot)


# ── 4. Filter: closing within 90 days ───────────────────────────────────────
print("\n=== 4. Closing Within 90 Days ===")
closing_soon = client.list(
    filter_expr=(
        "estimatedclosedate ge 2026-04-11 "
        "and estimatedclosedate le 2026-07-10"
    ),
    order_by="estimatedclosedate asc",
)
print_opps("Closing soon", closing_soon)


# ── 5. Search by Topic name ──────────────────────────────────────────────────
print("\n=== 5. Search by Topic ===")
found = client.list(
    filter_expr="contains(name, 'Product SKU')",
    top=3,
)
print_opps("Contains 'Product SKU'", found)


# ── 6. Get single record ─────────────────────────────────────────────────────
if all_opps:
    opp_id = all_opps[0]["opportunityid"]
    print(f"\n=== 6. Get Single Record ===")
    record = client.get(opp_id)
    print_opps("Single record", record)


# ── 7. Create ────────────────────────────────────────────────────────────────
# Replace with a real account GUID from your CRM (e.g. from step 1 output above)
ACCOUNT_GUID = "4001a8f4-2c2c-f111-88b3-6045bd057df7"  # Fourth Coffee (sample)
CONTACT_GUID = "a601a8f4-2c2c-f111-88b3-6045bd057df7"  # Contact (sample)

print("\n=== 7. Create Opportunity ===")
new_id = client.create({
    "name": "API Test — New Deal",
    "customerid_account@odata.bind": f"/accounts({ACCOUNT_GUID})",   # Potential Customer
    "estimatedclosedate": "2026-09-30",
    "estimatedvalue": 85000,
    "parentaccountid@odata.bind": f"/accounts({ACCOUNT_GUID})",
    "parentcontactid@odata.bind": f"/contacts({CONTACT_GUID})",
    "closeprobability": 60,
    "opportunityratingcode": 1,   # Hot
})
print(f"  Created: {new_id}")
print_opps("New record", client.get(new_id))


# ── 8. Update ────────────────────────────────────────────────────────────────
print("\n=== 8. Update Opportunity ===")
client.update(new_id, {
    "estimatedvalue": 95000,
    "closeprobability": 75,
    "opportunityratingcode": 1,   # still Hot
})
print_opps("After update", client.get(new_id))


# ── 9. Delete ────────────────────────────────────────────────────────────────
# print("\n=== 9. Delete Opportunity ===")
# client.delete(new_id)
# print(f"  Deleted: {new_id}")
