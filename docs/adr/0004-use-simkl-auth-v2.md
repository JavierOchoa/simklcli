# Use Simkl AUTH V2 with explicit reconnection

## Context

Simkl recommends AUTH V2 for new applications and expects AUTH V1 retirement
around April 2027; the exact date is not fixed. A V1 registration cannot be
upgraded and V1 tokens cannot be exchanged for V2 tokens. Our original client
used V1 PIN endpoints and stored only a long-lived Access Token.

Sources: [migration](https://api.simkl.org/guides/migrating-v1-to-v2),
[PIN Authorization](https://api.simkl.org/api-reference/oauth2-device),
[token refresh](https://api.simkl.org/api-reference/oauth2-tokens), and
[PKCE applicability](https://api.simkl.org/api-reference/oauth2-pkce).

Implementation and local verification tracking: [GitHub issue #14](https://github.com/JavierOchoa/simklcli/issues/14).

## Decision

- Cut over new online operations to AUTH V2 only. Keep legacy credentials
  readable for dated offline snapshots and explicit same-account reconnection.
- Register as **TV, devices & command line**. Use the device-based PIN flow;
  it has no PKCE, redirect URI, or client secret. Send the private device code
  only in the token-request body and show the complete approval URL.
- Request and validate `media:read media:write`. Persist both tokens together,
  with client provenance and expiry metadata, using the chosen storage backend.
- Coordinate refresh with the existing cross-process account lock. Re-read
  tokens inside that lock, validate the account after refresh, and persist
  before proceeding. Refresh once on a definitive authentication rejection;
  never replay a tracking write after an ambiguous failure.
- Require explicit sign-in for search and reference resolution. Retain public
  known-ID summaries and Episode lists.
- Keep snapshots and account metadata when a grant requires reauthorization.
  Delete them on explicit logout, consistent with ADR-0002. V2 logout requests
  revocation best-effort and exposes acknowledgement without claiming proof.
- Remove the embedded V1 app ID. Local setup supplies a real V2 public ID;
  each stored token remembers its issuer. Do not invent a replacement app ID.

## Consequences

This supersedes the original AUTH V1 assumptions in the build specification
and domain glossary about refresh, anonymous search, and local-only logout.
Reconnection is user-initiated and validates the account ID before replacement;
no Library or Watched State data is migrated on Simkl. Existing V1 apps can
remain registered while users reconnect.

Environment Access Token overrides remain non-persisted and do not use a
shadowed account's Refresh Token. Their owner must supply a fresh V2 token when
it expires. Merge-gating tests stay offline; local live verification needs an
actual V2 app registration and consent by the account owner.
