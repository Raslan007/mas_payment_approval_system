# Deployment Notes

## Required environment variables
- `SECRET_KEY`: strong secret value (required in production).
- `DATABASE_URL`: connection string for your production database (Postgres or SQLite).
- `APP_ENV=production`: ensures production safeguards such as strict cookie settings are enabled.

## Build & start commands (Render)
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** run the idempotent migration scripts before launching Gunicorn, binding explicitly to `$PORT`:

  ```bash
  python scripts/migrate_add_indexes.py \
    && python scripts/migrate_add_payment_submitted_to_pm_at.py \
    && python scripts/migrate_add_payment_updated_at.py \
    && python scripts/migrate_add_user_projects.py \
    && gunicorn "app:create_app()" --bind 0.0.0.0:$PORT
  ```

If you add or upgrade dependencies, use **Settings → Clear build cache** in Render before redeploying to ensure a clean environment.

## Render start command
To ensure the schema is up to date before the app starts, configure Render's **Start Command** to run the idempotent migration scripts before launching the server (order doesn't matter; all are safe to rerun):

```bash
python scripts/migrate_add_indexes.py \
  && python scripts/migrate_add_payment_submitted_to_pm_at.py \
  && python scripts/migrate_add_payment_updated_at.py \
  && python scripts/migrate_add_user_projects.py \
  && gunicorn "app:create_app()" --bind 0.0.0.0:$PORT
```

These scripts read `DATABASE_URL`, create/patch missing tables or columns safely (Postgres or SQLite), backfill data where necessary, and then start the Flask application via Gunicorn.

To run the new dashboard/filter indexes migration locally or on the server, execute:

```bash
python scripts/migrate_add_indexes.py
```

The migration is idempotent and safe to rerun; use `--downgrade` to drop the indexes if needed.

## Optional automatic schema bootstrap
To let the application create any missing tables (e.g., newly added models) during startup, set the environment variable `AUTO_SCHEMA_BOOTSTRAP=1`. When unset or `0`, the app skips the automatic `create_all()` call and relies on your migration scripts instead. The flag is idempotent and safe to enable on platforms like Render where schema drift may occur.
