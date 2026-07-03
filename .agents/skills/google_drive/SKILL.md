---
name: google_drive
description: Operate Google Drive, including listing files, downloading, uploading, and searching files.
---
# Google Drive Operation Skill

Use this skill when the user asks to interact with their Google Drive, such as listing files, uploading local files, or downloading files from Google Drive.

## Prerequisite
Install Google API client libraries:
```bash
pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
```
You must place your `credentials.json` file (obtained from Google Cloud Console as a "Desktop App" client) in the workspace directory:
`C:\Users\coolexam\Documents\hackathon\.agents\skills\google_drive\credentials.json`

## Usage
Run the helper script using python:
```bash
python .agents/skills/google_drive/scripts/gdrive_helper.py <command> [args]
```

Available commands:
- `list`: Lists the first 10 files/folders in the Google Drive root.
- `upload <local_path> [remote_folder_id]`: Uploads a local file to Google Drive.
- `download <file_id> <local_path>`: Downloads a file from Google Drive by its file ID.
- `search <query>`: Searches for files matching the name query.
