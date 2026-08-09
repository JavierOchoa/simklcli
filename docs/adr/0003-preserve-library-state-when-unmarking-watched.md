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
