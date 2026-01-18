# MAS Payment Approval System

## Database migrations on Render
- **Local:** run `flask --app app:app db upgrade` to apply the latest migrations.
- **Render:** configure the web service **Pre-Deploy Command** to run `flask --app app:app db upgrade` so migrations are applied before the server starts. The explicit `--app app:app` flag makes the command work in non-interactive environments without requiring `FLASK_APP`.
