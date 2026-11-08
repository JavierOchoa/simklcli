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
  readable for dated offline snapshots and explicit same-account reconnection
  (withdrawn; see the amendment below).
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
- Remove the embedded V1 app ID. Each stored token remembers its issuer. Do not
  invent a replacement app ID; embed only the maintainer's registered V2 one.

## Amendment: embedded V2 app ID

The maintainer has registered simklcli's AUTH V2 app as TV, devices & command
line, and the package embeds its public client ID as the default. Simkl
documents V2 client IDs as safe to publish: a bare ID reaches only public
catalog data, and quotas are per user, not per app. `SIMKL_CLIENT_ID` still
overrides it, and existing credentials keep their stored issuer, so replacing
the embedded ID later does not invalidate earlier logins.

## Amendment: no AUTH V1 compatibility

The V2 cutover landed before the first public release, so v0.1.0 shipped AUTH V2
only and no installed copy ever wrote an AUTH V1 credential. Reading legacy
credentials for offline snapshots and same-account reconnection served only
pre-release development installs, so it is removed. A leftover version-1
credential file is reported as unsupported with instructions to log out;
logout deletes it and its snapshot without attempting revocation. `auth status
--json` no longer reports `auth_version`. The early rejection of 64-character
hexadecimal `SIMKL_ACCESS_TOKEN` values stays as a cheap guard against V1
tokens copied from other tools.

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
