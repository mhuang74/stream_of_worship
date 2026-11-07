CREATE TABLE "lyrics_review_set_songs" (
	"set_id" text NOT NULL,
	"song_id" text NOT NULL,
	"added_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "lyrics_review_set_songs_set_id_song_id_pk" PRIMARY KEY("set_id","song_id")
);
--> statement-breakpoint
CREATE TABLE "lyrics_review_sets" (
	"id" text PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "lyrics_review_sets_name_unique" UNIQUE("name")
);
--> statement-breakpoint
ALTER TABLE "lyrics_review_set_songs" ADD CONSTRAINT "lyrics_review_set_songs_set_id_lyrics_review_sets_id_fk" FOREIGN KEY ("set_id") REFERENCES "public"."lyrics_review_sets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "lyrics_review_set_songs" ADD CONSTRAINT "lyrics_review_set_songs_song_id_songs_id_fk" FOREIGN KEY ("song_id") REFERENCES "public"."songs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_lyrics_review_set_songs_song" ON "lyrics_review_set_songs" USING btree ("song_id");