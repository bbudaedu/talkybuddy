import os
import sys
import argparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/drive']

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_PATH = os.path.join(SKILL_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(SKILL_DIR, 'token.json')

def get_gdrive_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                print(f"Error: Credentials file not found at {CREDENTIALS_PATH}", file=sys.stderr)
                print("Please download credentials.json from Google Cloud Console (Desktop App) and place it there.", file=sys.stderr)
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('drive', 'v3', credentials=creds)
        return service
    except HttpError as error:
        print(f"An error occurred: {error}", file=sys.stderr)
        sys.exit(1)

def list_files():
    service = get_gdrive_service()
    try:
        results = service.files().list(
            pageSize=10, fields="nextPageToken, files(id, name, mimeType)").execute()
        items = results.get('files', [])

        if not items:
            print('No files found.')
            return
        print('Files:')
        for item in items:
            print(f"{item['name']} ({item['id']}) [{item['mimeType']}]")
    except HttpError as error:
        print(f"An error occurred: {error}", file=sys.stderr)

def search_files(query):
    service = get_gdrive_service()
    try:
        # Simple name search
        q = f"name contains '{query}'"
        results = service.files().list(
            q=q, pageSize=10, fields="nextPageToken, files(id, name, mimeType)").execute()
        items = results.get('files', [])

        if not items:
            print(f"No files found matching: {query}")
            return
        print(f"Search results for '{query}':")
        for item in items:
            print(f"{item['name']} ({item['id']}) [{item['mimeType']}]")
    except HttpError as error:
        print(f"An error occurred: {error}", file=sys.stderr)

def create_folder(name, parent_id=None):
    service = get_gdrive_service()
    file_metadata = {
        'name': name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        file_metadata['parents'] = [parent_id]
    try:
        file = service.files().create(body=file_metadata, fields='id').execute()
        return file.get('id')
    except HttpError as error:
        print(f"An error occurred while creating folder: {error}", file=sys.stderr)
        return None

def upload_path(local_path, parent_id=None):
    if not os.path.exists(local_path):
        print(f"Error: Path '{local_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
        
    if os.path.isdir(local_path):
        folder_name = os.path.basename(os.path.normpath(local_path))
        if folder_name in ['.git', '.agents', '__pycache__', 'node_modules', '.venv', 'venv']:
            return
            
        print(f"Creating remote folder '{folder_name}'...")
        remote_folder_id = create_folder(folder_name, parent_id)
        if not remote_folder_id:
            return
            
        for item in os.listdir(local_path):
            item_path = os.path.join(local_path, item)
            upload_path(item_path, remote_folder_id)
    else:
        service = get_gdrive_service()
        file_name = os.path.basename(local_path)
        file_metadata = {'name': file_name}
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        media = MediaFileUpload(local_path, resumable=True)
        try:
            print(f"Uploading '{file_name}'...")
            file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            print(f"Successfully uploaded '{file_name}'! File ID: {file.get('id')}")
        except HttpError as error:
            print(f"An error occurred while uploading '{file_name}': {error}", file=sys.stderr)


def download_file(file_id, local_path):
    service = get_gdrive_service()
    try:
        request = service.files().get_media(fileId=file_id)
        with open(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                print(f"Download {int(status.progress() * 100)}%.")
        print(f"Successfully downloaded to {local_path}!")
    except HttpError as error:
        print(f"An error occurred: {error}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Google Drive Agent Helper CLI")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # List command
    subparsers.add_parser("list", help="List files from root")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search files by name")
    search_parser.add_argument("query", help="Text to search for in file names")

    # Upload command
    upload_parser = subparsers.add_parser("upload", help="Upload local file to Google Drive")
    upload_parser.add_argument("local_path", help="Path to local file")
    upload_parser.add_argument("--parent", help="Optional remote parent folder ID", default=None)

    # Download command
    download_parser = subparsers.add_parser("download", help="Download file from Google Drive")
    download_parser.add_argument("file_id", help="Google Drive file ID")
    download_parser.add_argument("local_path", help="Local destination path")

    args = parser.parse_args()

    if args.command == "list":
        list_files()
    elif args.command == "search":
        search_files(args.query)
    elif args.command == "upload":
        upload_path(args.local_path, args.parent)
    elif args.command == "download":
        download_file(args.file_id, args.local_path)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
