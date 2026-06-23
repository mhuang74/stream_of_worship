"""Connection health checker for sow-app.

Re-exports the shared check_database_connection function from the db module.
The old Turso sync infrastructure has been removed as part of the
Neon/PostgreSQL migration.
"""

from stream_of_worship.db.connection import check_database_connection

__all__ = ["check_database_connection"]
