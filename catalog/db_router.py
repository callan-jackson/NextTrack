"""Database router for directing reads to a replica and writes to the default."""

from django.conf import settings


class ReadReplicaRouter:
    """
    Route read queries to the 'replica' database when it is configured,
    and all write / migration operations to the 'default' database.
    """

    def db_for_read(self, model, **hints):
        """Point all read operations to the replica if available."""
        if 'replica' in settings.DATABASES:
            return 'replica'
        return 'default'

    def db_for_write(self, model, **hints):
        """Point all write operations to the default (primary) database."""
        return 'default'

    def allow_relation(self, obj1, obj2, **hints):
        """Allow relations between objects in any database."""
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Only run migrations on the default (primary) database."""
        return db == 'default'
