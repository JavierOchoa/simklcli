# Preserve Library state when unmarking standalone items

`simkl` gives **Mark Watched** and **Unmark Watched** the same target model: a
standalone Movie or Anime, a selected Episode or Season, or an explicit Bulk
Watched Update. Unmarking preserves the parent or standalone Media Item's
Library membership, List Status, and User Rating even though Simkl's item-level
history-removal operation also deletes that surrounding state.

This keeps the public command surface symmetrical and prevents Simkl transport
constraints from redefining the user's intent. For a standalone item, the CLI
must capture the surrounding Library state, perform the required removal and
restoration operations sequentially, and verify the complete final state. It
must report a partial or unknown outcome if preservation cannot be established;
it must never silently turn Unmark Watched into Remove from Library.

## Live-verified Movie constraint (2026-11-02)

Simkl couples a Movie's Completed List Status to Watched State. Restoring
Completed records a new Viewing, including when `watched_at: null` is supplied.
Moving the Movie explicitly to Plan to Watch or Dropped clears Watched State
while retaining its User Rating. This was confirmed by a reserved-Movie live
test and is consistent with Simkl's
[status conventions](https://api.simkl.org/conventions/list-statuses).

This narrows the original standalone-item promise for Movies: Unmark Watched
rejects a watched, Completed Movie before any write. The user must choose an
explicit List Status change with `library set-status`; the CLI never chooses
one implicitly. Episode unmarking and supported standalone-item preservation
continue to follow the original decision. The Movie smoke verifies the explicit
status move and subsequent idempotent unmark separately.
