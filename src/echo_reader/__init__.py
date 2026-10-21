"""Public integration API for the Echo Reader workspace."""

from .app_config import AppConfig, AppPaths
from .echo_api import EchoApiClient, EchoApiError
from .integration import ImportCoordinator, ImportFailed
from .migration import (
    ActiveDatabaseError,
    MigrationConflict,
    MigrationError,
    MigrationResult,
    migrate_from,
    recover_interrupted_migration,
)
from .server import create_app

__all__ = [
    "AppConfig",
    "AppPaths",
    "EchoApiClient",
    "EchoApiError",
    "ImportCoordinator",
    "ImportFailed",
    "ActiveDatabaseError",
    "MigrationConflict",
    "MigrationError",
    "MigrationResult",
    "migrate_from",
    "recover_interrupted_migration",
    "create_app",
]
