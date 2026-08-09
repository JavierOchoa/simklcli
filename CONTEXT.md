# Simkl Tracking

The language used by `simkl` to describe catalog media and a user's relationship with it. These terms deliberately separate stable domain concepts from Simkl API transport vocabulary.

## Authentication

**PIN Authorization**:
The interactive Simkl authorization process in which the CLI displays a short code, the user approves it on Simkl's website, and the CLI receives an Access Token.
_Avoid_: Device Flow, OAuth Device Flow

**Access Token**:
The long-lived bearer credential authorizing the CLI to act for one Simkl account; Simkl provides no refresh token.
_Avoid_: Session Token, API Key

**Authenticated Account**:
A Simkl account for which the CLI currently has an Access Token, identified by its stable Simkl account ID and display name.
_Avoid_: Profile, Session, User

## Media

**Media Item**:
The abstract umbrella for a Movie, Show, Anime, or Episode.
_Avoid_: Content, title (when referring to every kind of item)

**Catalog Metadata**:
The non-user-specific description of a Media Item in Simkl's catalog, such as its titles, year, kind, episode structure, and Community Rating; it excludes Library state, Watched State, User Rating, and the narrow Library Display Identity retained with a Library Snapshot.
_Avoid_: Metadata (unqualified), Item Details

**Movie**:
A standalone filmed work that can be tracked as a whole.
_Avoid_: Film

**Show**:
A non-anime episodic television program whose episodes may be tracked individually.
_Avoid_: TV, series

**Anime**:
An anime work, episodic or standalone, kept distinct from Show because its kinds and episode numbering follow anime-native conventions.
_Avoid_: Animated show, Japanese show

**Anime Kind**:
A secondary classification of an Anime as TV Anime, Special, OVA, Movie, Music Video, or ONA; an Anime whose kind is Movie remains an Anime.
_Avoid_: Media type, format

**Season**:
A numbered grouping of Episodes belonging to a Show; it is not part of the canonical structure of an Anime.
_Avoid_: Anime season

**Episode**:
An individually trackable installment of a Show or Anime, canonically located by Episode Number and, for a Show, Season.
_Avoid_: Chapter

**Episode Number**:
The stable number locating an Episode within a Show Season or within an Anime's anime-native episode sequence.
_Avoid_: Episode ID

**TVDB Episode Coordinates**:
An alternate season-and-episode location assigned by TVDB to an Anime Episode, not the Anime's canonical structure.
_Avoid_: Anime Season

## User Library

**Library**:
The complete collection of Media Items tracked on a user's Simkl account, across every List Status.
_Avoid_: Watchlist (when referring to the complete collection)

**Library Snapshot**:
The CLI's last reconciled representation of an Authenticated Account's Library state, including List Status, Watched State, User Ratings, Library Display Identity, and the activity positions needed to reconcile later changes; it excludes all other Catalog Metadata.
_Avoid_: Library cache, local Library

**Library Display Identity**:
The minimal identifying fields retained with a Library Snapshot so its entries remain understandable: Simkl ID, title, year, and media kind.
_Avoid_: Cached Catalog Metadata, item details

**List Status**:
The kind-constrained bucket that describes the user's current intent or relationship with a Library item; invalid combinations are not silently normalized.
_Avoid_: Watch state, list

Allowed combinations are explicit:

| Media kind | Allowed List Statuses |
| --- | --- |
| Movie | Plan to Watch, Dropped, Completed |
| Show | Watching, Plan to Watch, On Hold, Dropped, Completed |
| Anime | Watching, Plan to Watch, On Hold, Dropped, Completed |

**Plan to Watch**:
The List Status for a Media Item the user intends to watch later.
_Avoid_: Watchlist, backlog

**Watching**:
The List Status for a Show or Anime the user is actively following.
_Avoid_: In progress

**On Hold**:
The List Status for a Show or Anime the user has paused but may resume.
_Avoid_: Hold, paused

**Dropped**:
The List Status for a Media Item the user has stopped intending to finish.
_Avoid_: Abandoned

**Completed**:
The List Status for a Media Item the user regards as finished; it is not, by itself, evidence of a Viewing.
_Avoid_: Watched

**Viewing**:
A recorded occurrence of the user watching a Movie, Episode, or standalone Anime.
_Avoid_: History entry, play

**Watched State**:
Whether a Media Item or Episode has at least one recorded Viewing.
_Avoid_: History

**Last Watched At**:
The timestamp of the latest known Viewing, without implying that every earlier Viewing is available.
_Avoid_: History timestamp

**Bulk Watched Update**:
A change to the Watched State of multiple applicable Episodes, optionally accompanied by a parent List Status change; it is not one Viewing of the parent.
_Avoid_: Show viewing, mark-all viewing

**Unmark Watched**:
Clearing Watched State from a standalone Media Item, selected Episodes, or a Season while preserving the parent or standalone Media Item's Library membership, List Status, and User Rating.
_Avoid_: Remove from history

**Remove from Library**:
Deleting a Media Item from the Library together with its associated Watched State and User Rating.
_Avoid_: Unwatch, remove from history

**Rewatch**:
A Viewing after the first recorded Viewing of the same Movie, Episode, or standalone Anime.
_Avoid_: Replay

## Identity and Ratings

**Simkl ID**:
The permanent, globally unique identifier that canonically identifies a Media Item in Simkl.
_Avoid_: Internal ID, canonical ID

**External ID**:
An identifier assigned by another provider, always qualified by provider and by media kind when the provider's namespace requires it.
_Avoid_: Alternate ID, ID (when the provider is ambiguous)

**Media Reference**:
User-supplied information that resolves to a Media Item: a Simkl ID, a qualified External ID, or a title and year fallback.
_Avoid_: Media ID, query

**Episode Reference**:
A parent Media Reference plus canonical episode coordinates—Season and Episode Number for a Show, or Episode Number for an Anime—or a qualified TVDB or AniDB episode-level External ID.
_Avoid_: Episode ID (when no provider is named)

**Slug**:
A response-provided URL hint that is not unique and never establishes a Media Item's identity.
_Avoid_: Slug ID

**User Rating**:
The authenticated user's personal score for a Movie, Show, or Anime on Simkl's 1–10 scale; Episodes are not rateable in the initial domain model.
_Avoid_: Rating (when ownership is ambiguous)

**Community Rating**:
An aggregate catalog score from Simkl or another named provider.
_Avoid_: Public rating, rating (when its source is ambiguous)

## Simkl API Mapping

| Domain concept | Simkl representation |
| --- | --- |
| Movie | `movies[]` and `/movies/{simkl_id}` |
| Show | `shows[]`, Simkl type `tv`, and `/tv/{simkl_id}` |
| Anime | `anime[]` and `/anime/{simkl_id}`; some synchronization responses fold Anime into `shows[]` |
| Episode | Nested `seasons[].episodes[]` or anime-native `episodes[]` |
| Library / List Status | `/sync/all-items` and `watching`, `plantowatch`, `hold`, `dropped`, or `completed` status tokens |
| Viewing / Watched State | `/sync/history` and `/sync/watched`; `last_watched_at` maps to Last Watched At |
| Unmark Watched / Remove from Library | Different granularities of `/sync/history/remove` |
| User Rating | `/sync/ratings` |
| Community Rating | Catalog-detail rating fields |
| Simkl ID | `ids.simkl` or the response alias `ids.simkl_id` |
| External ID | Any other provider-qualified field under `ids` |
