# Google Drive Setup

To set up Google Drive for use with the Google Drive API, follow these steps. This guide provides a step-by-step process for both personal and business accounts.

1. **Create a Google Account**: Ensure you have a Google Account. This process works for both personal and business accounts.

2. **Create a Project**:
   - Visit [Google Cloud Console](https://console.cloud.google.com/).
   - In the top left corner, click on `Select a project` (there might already be a project name selected if you have previously created a project) and then `New Project`.
   - Assign a name to the project, select a location (which specifies where the project is created), and click `Create`.
   - In the top left corner, you should see the name of your project, or click on `Select a project` and select the project you just created.

3. **Enable Google Drive API**:
   - In the left menu, click on `APIs & Services`.
   - Click on `Enable APIs and Services`.
   - Search for `Google Drive API`, click on it, and then click `Enable`.

4. **Create a Service Account**:
   - On the left side of the screen, click on `Credentials`.
   - Click on `Create credentials` and then on `Service account`.
   - Specify the name, account ID, and description, and click `Done`.
   - Click on the created credentials, select the `Keys` page, and then create a new key using `Add Key` and then `Create new key`.
   - In the following dialog, select `JSON` and click `Create`. The key will be downloaded to your computer (store it safely, as it cannot be redownloaded). In case of a lost key, you can delete it and create a new one.

The downloaded JSON file contains all the necessary information to authenticate with the Google Drive API. Provide this file to the media sync tool as the path to the file:

```yaml
google_drive:
  service_account_file: path/to/your/service_account_file.json
```

Keep this file secret and do not share it with anyone.

## Sharing the Folder with Data from Media Sync

The data stored under the project's `Service Account` is counted towards the Google Drive storage quota of the user who created the project.

To store data in Google Drive, use the following settings:

```yaml
google_drive:
  service_account_file: path/to/your/service_account_file.json
  folder: your_folder_name
  share_with: [email1@example.com, email2@example.com]
```

This creates a `folder` in Google Drive under the `Service account`, accessible only by this specific user. To make it available to other users, use the `share_with` setting. The folder will be shared with all the email addresses specified in the list (the emails need to be Google Emails - business or free). Every user will have the same access rights as the user who created the folder and can create and delete files in the folder. For users with whom the folder is shared, it will be listed in their Google Drive under the `Shared with me` section.