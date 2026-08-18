CREATE TABLE "user_favorite_songs" (
	"user_id" bigint NOT NULL,
	"song_id" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_user_favorite_songs_user_song" UNIQUE("user_id","song_id")
);
--> statement-breakpoint
ALTER TABLE "user_favorite_songs" ADD CONSTRAINT "user_favorite_songs_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "user_favorite_songs" ADD CONSTRAINT "user_favorite_songs_song_id_songs_id_fk" FOREIGN KEY ("song_id") REFERENCES "public"."songs"("id") ON DELETE cascade ON UPDATE no action;