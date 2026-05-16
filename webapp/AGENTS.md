<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Development Commands

| Command | Description |
|---------|-------------|
| `pnpm dev` | Start dev server on `:8080` |
| `pnpm test` | Run tests |
| `pnpm test:watch` | Run tests in watch mode |
| `pnpm lint` | Lint code |
| `pnpm build` | Production build |

## Database Migrations

| Command | Description |
|---------|-------------|
| `npx drizzle-kit push` | Push schema to DB |
| `npx drizzle-kit generate` | Generate migration files |
| `npx drizzle-kit migrate` | Run pending migrations |

## Architecture

- **Framework**: Next.js 16 (App Router)
- **ORM**: Drizzle ORM with PostgreSQL
- **Auth**: Better Auth
- **Storage**: Cloudflare R2
