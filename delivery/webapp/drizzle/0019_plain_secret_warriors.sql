-- Hand-edited: the musical key columns on recordings/songs were already added
-- by 0017_improve_musical_key_accuracy_v2.sql (with IF NOT EXISTS). Drizzle-kit
-- regenerated those ALTER statements here, but re-adding them without
-- IF NOT EXISTS fails on a fresh DB. Only the new index below is needed.
CREATE INDEX "idx_songset_items_song_id" ON "songset_items" USING btree ("song_id");