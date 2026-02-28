# Safe Delete Checker (v1)

A read-only verification tool that answers one question:

“Is it safe to delete the original folder?”

Given:
- Folder A (source)
- Folder B (backup)

The tool verifies that every file in Folder A exists in Folder B
with the same relative path and file size.

PASS means:
- No files missing
- No size mismatches
- No read errors

Timestamp differences are informational only.
Copy tools may not preserve timestamps.

This tool does NOT:
- Modify files
- Copy files
- Sync files
- Delete files

It is strictly verification-only.

Windows may warn that the app is from an unknown publisher.
Click “More info” → “Run anyway”.

— Cameratrician Studios