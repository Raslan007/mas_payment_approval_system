# Attachment / Upload Review

## Findings

- **P1 – Attachment links point to missing route**: Templates reference `payments.download_attachment` for existing attachments, but no route/function with that endpoint exists. Rendering a payment with attachments triggers a `url_for` `BuildError` (HTTP 500) instead of a download. Evidence: download links in `templates/payments/detail.html` and `templates/payments/edit.html` point to `payments.download_attachment`, but `blueprints/payments/routes.py` defines no such handler. Suggested fix: add a safe download route with `send_from_directory`/`send_file` (using a fixed base path and attachment record lookup) or hide links until implemented.

- **P2 – No upload pipeline implemented**: There is a `PaymentAttachment` model and file input in the edit form, but no view handles `request.files`, saves to disk, or stores metadata. No upload folder or allowed-extension/size limits are configured in `config.py`. Effect: attachments can’t actually be uploaded and would rely on the ephemeral Render filesystem if naively added. Suggested fix: either remove/disable the upload UI until implemented, or add a controller that saves to a writable persistent location (e.g., `instance/uploads` with `secure_filename`, extension/size validation, and directory creation) and records metadata in `PaymentAttachment`.

- **P2 – Deletion only removes DB rows, not files**: `delete_payment` deletes `PaymentAttachment` rows but, since file storage is undefined, no filesystem cleanup occurs. If uploads are later added without delete hooks, orphaned files may accumulate. Suggested fix: when implementing uploads, delete corresponding files from the storage backend inside the transaction.
