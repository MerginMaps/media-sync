import typing
from drivers import GoogleDriveDriver


def google_drive_delete_folder(driver: GoogleDriveDriver, folder_name: str) -> None:
    """Delete folder from Google Drive."""

    folder_id = driver._folder_exists(folder_name)

    if folder_id:
        driver._service.files().delete(fileId=folder_id).execute()


def extract_files_from_google_list_of_files(
    files: typing.List[typing.Dict],
) -> typing.List[str]:
    """Extract files from Google Drive metadata."""

    return [file["name"] for file in files]


def google_drive_list_files_in_folder(
    driver: GoogleDriveDriver, folder_name: str
) -> typing.List[str]:
    """List files in folder from Google Drive."""

    folder_id = driver._folder_exists(folder_name)

    if folder_id:
        query = f"'{folder_id}' in parents and trashed = false"

        # Get files with their metadata
        results = (
            driver._service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageSize=100,
            )
            .execute()
        )

        files = results.get("files", [])

        # Handle pagination for large folders
        while "nextPageToken" in results:
            results = (
                driver._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    pageToken=results["nextPageToken"],
                    pageSize=100,
                )
                .execute()
            )
            files.extend(results.get("files", []))

    return extract_files_from_google_list_of_files(files)
