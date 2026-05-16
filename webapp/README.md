# Stream of Worship Web App

Web application for rendering worship music transitions with synchronized lyrics videos.

## Prerequisites

- Node.js 18+
- pnpm
- PostgreSQL database
- Cloudflare R2 account

## Environment Setup

Copy `.env.example` to `.env.local` and configure:

- `DATABASE_URL` — PostgreSQL connection string
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` — Cloudflare R2 credentials
- `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL` — Better Auth configuration

## Development

```bash
pnpm dev          # Start dev server on http://localhost:8080
pnpm test         # Run tests
pnpm test:watch   # Run tests in watch mode
pnpm lint         # Lint code
pnpm build        # Production build
```

## Database Migrations

```bash
npx drizzle-kit push       # Push schema changes to DB
npx drizzle-kit generate   # Generate migration files
npx drizzle-kit migrate    # Run pending migrations
```

## Routes

| Path | Description |
|------|-------------|
| `/login` | User login |
| `/register` | User registration |
| `/songsets` | Songset list |
| `/songsets/[id]` | Songset detail |
| `/songsets/[id]/render` | Render configuration |
| `/songsets/[id]/play` | Playback view |

## Architecture

- **Framework**: Next.js 16 (App Router)
- **ORM**: Drizzle ORM with PostgreSQL
- **Auth**: Better Auth
- **Storage**: Cloudflare R2
- **Rendering**: FFmpeg (audio mixing, video encoding)
