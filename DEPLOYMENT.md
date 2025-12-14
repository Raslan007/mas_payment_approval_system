# Deployment Notes

## Render start command
To ensure the `payment_requests.updated_at` column exists before the app starts, configure Render's **Start Command** to run the migration script before launching the server:

```bash
python scripts/migrate_add_payment_updated_at.py && gunicorn app:app
```

This command reads `DATABASE_URL`, applies the idempotent migration to Postgres if needed, and then starts the Flask application via Gunicorn.
