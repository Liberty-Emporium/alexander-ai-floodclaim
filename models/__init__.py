"""
FloodClaims Pro — Models package
Database layer, migrations, password hashing, settings.
"""
from models.database import (
    _ensure_db_initialized,
    get_db,
    close_db,
    init_db,
    hash_pw,
    check_pw,
    migrate_claims_columns,
    migrate_new_features,
    migrate_photos_columns,
    migrate_new_features_v2,
    _migrate_recruitment_tables,
    _migrate_feedback_tables,
    migrate_batch_photo_columns,
    _migrate_aquila_tables,
    get_setting,
    set_setting,
    _set_app,
    _set_paths,
)
