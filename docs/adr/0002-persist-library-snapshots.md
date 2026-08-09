# Persist per-account Library Snapshots

## Context

Simkl's activity-gated synchronization protocol requires retained state to
provide complete, efficient Library reads and detect removals. Focused write
commands must still work before a full Library has ever been loaded, and stale
local state must never be presented as current without the user's knowledge.

## Decision

Persist one versioned JSON Library Snapshot per Authenticated Account. It holds
List Status, Watched State, User Ratings, synchronization activity positions,
and only the Library Display Identity needed to understand each entry: Simkl ID,
title, year, and media kind. Do not persist other Catalog Metadata.

Store snapshots in the platform user-data directory using owner-only
permissions, a per-account cross-process lock, and atomic replacement. Rebuild
missing, corrupt, or incompatible snapshots instead of migrating them. Delete
the account's snapshot when its persisted account metadata is cleared.

### Reads

- Before every online Library-state read, query Simkl's activities endpoint and
  reconcile only changed state.
- Include the required IDs-only comparison when detecting removals.
- Fail if freshness cannot be established; do not silently return stale data.
- Explicit offline mode may read the last complete snapshot and must report its
  age.
- State learned through focused operations before the first complete Library
  read is partial and must not be presented as a complete Library.

### Writes

- List Status, Watched State, and User Rating writes operate directly on a Media
  Reference and do not require snapshot initialization.
- After a successful write, reconcile the affected state from Simkl and update
  the snapshot. Simkl's resulting state is authoritative because it may
  normalize status or apply secondary Library changes.
- If the remote write succeeds but focused reconciliation fails, report the
  write as successful, mark local state dirty, and reconcile before the next
  online read. Do not encourage retrying the write.
- A failed or outcome-unknown POST follows the read-back verification policy in
  the build specification.

## Consequences

Synchronization stays internal: there is no user-facing `sync` command. A full
rebuild occurs only on the first Library read, when state is missing, corrupt,
or incompatible, or through explicit `library repair`. `library list --offline`
is the only path that deliberately returns a dated snapshot.
