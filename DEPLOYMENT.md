# Deployment Notes

## Render start command
To ensure the schema is up to date before the app starts, configure Render's **Start Command** to run the idempotent migration scripts before launching the server (order doesn't matter; both are safe to rerun):

```bash
python scripts/migrate_add_payment_updated_at.py &&
python scripts/migrate_add_user_projects.py &&
python scripts/migrate_add_payment_submitted_to_pm_at.py &&
gunicorn app:app
```

These scripts read `DATABASE_URL`, create/patch missing tables or columns safely (Postgres or SQLite), backfill data where necessary, and then start the Flask application via Gunicorn.
