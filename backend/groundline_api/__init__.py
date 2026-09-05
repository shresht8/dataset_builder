"""Groundline dataset builder API (FastAPI).

See docs/groundline-dataset-builder.md. Package layout:

    main.py       app factory + router wiring
    config.py     env-driven settings
    db.py         SQLAlchemy engine / session
    deps.py       shared FastAPI dependencies (db session, current user)
    models/       SQLAlchemy ORM models (§3 data model)
    schemas/      Pydantic request/response models
    api/v1/       HTTP routes (§8)
    auth/         Entra OIDC, roles, JIT provisioning, PATs (§5)
    services/     versioning, export, import, storage, diff (§6, §7)
"""
