// User-Assigned Managed Identity for the Function App.
//
// Separating the MI from the Function App (rather than using the system-
// assigned one) lets the AAD app's Federated Identity Credential trust a
// stable identity — a rebuild of the Function App keeps the same MI and
// therefore the same FIC, no AAD-side rework (ADR 0001). Role assignments
// for Foundry live in a different tenant and are documented as a manual
// post-deploy step; we cannot assign them from here.

param namePrefix string
param location string

resource mi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-mi'
  location: location
}

output managedIdentityId string = mi.id
output principalId string = mi.properties.principalId
output clientId string = mi.properties.clientId
