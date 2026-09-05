"""Groundline CLI (§7).

    main.py           Typer app + top-level `pull`
    client.py         authenticated HTTP client (PAT from config)
    config.py         reads the API URL and token
    lock.py           groundline.lock read/write and hash verification
    commands/         `datasets list`, `datasets diff`
"""
