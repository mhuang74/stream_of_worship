# Vocal posture is persisted at the recording level but not surfaced in the webapp

Vocal posture — the 3-value "To God"/"About God"/"To Congregation" classification of a song component's lyrical addressee — is persisted at both the component and recording level in the admin schema (`song_components.vocal_posture` and `recordings.vocal_posture`), but is deliberately not surfaced as a label or badge in the webapp UI.

We chose to persist but not surface because posture is a transition-planning input meaningful to admins and the songset constructor, not to end users. Surfacing it would add visual noise to every SongCard for a concept users cannot act on. The column already exists, so a future UI change is sufficient without a schema migration if the need arises.

The cost is deferred UI work: if a product need later requires showing posture to users, no schema migration is needed (the column exists), but the UI work, a ThemeLabel-style component, and i18n keys would need to be added at that point. This is an admin/webapp boundary decision; the Android client follows the webapp's lead.
