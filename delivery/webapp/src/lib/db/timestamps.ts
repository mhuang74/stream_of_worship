import { sql, type SQLWrapper } from "drizzle-orm";

/** Read a timestamptz without truncating PostgreSQL's microseconds through Date. */
export function preciseTimestamp(column: SQLWrapper) {
  return sql<string>`to_char(${column} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
}
