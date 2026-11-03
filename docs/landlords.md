# Landlord records and management agreements

W1-02 stores landlord records under a branch and dated management agreements
under each landlord.

## Access

The landlord API is available only to authenticated users with one of these
staff roles:

- Super Admin (Director)
- Branch Manager
- Accountant
- Property Officer / Agent

Results are restricted to the branches assigned to the signed-in user.
Super Admin (Director) can access all branches through the existing access
model. Requests for an inaccessible landlord return `404` to avoid exposing
whether another branch has a matching record.

## Data boundary

Landlord records store identity/contact data and a payout-details object.
Management agreements store effective dates, commission terms, payout day,
MRI flag, and additional agreed terms. The API does not transfer money or
approve landlord remittances; those accounting workflows are later-wave
features.

Landlord and agreement creates and administrative changes are written to the
append-only audit log. The API currently exposes read and create operations;
updates and deletion remain controlled through the audited Django admin until
their workflow-specific authorization rules are defined.
