"""Connection health checker for sow-admin.

Re-exports the shared check_database_connection function from the db module.
"""

from stream_of_worship.db.connection import check_database_connection

__all__ = ["check_database_connection"]
