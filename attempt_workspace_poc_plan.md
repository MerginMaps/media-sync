# Attempt Workspace POC - Bare Bones Plan

## Core Flow (COPY Mode)

**Every run in COPY mode uses an attempt workspace:**

1. Get current server version
2. Create attempt workspace from baseline (hardlink clone)
3. Break hardlinks for non-media files (.gpkg, .mergin, etc.)
4. Run sync in attempt workspace:
   - Pull latest (if needed)
   - Upload media to S3
   - Update references in .gpkg
5. Push from attempt workspace
6. If push fails (version conflict):
   - Delete attempt workspace
   - Baseline remains clean (unchanged)
   - Get latest server version (will be newer)
   - Create new attempt workspace with new version
   - Retry from step 4
7. If push succeeds:
   - Promote attempt to baseline (rename)
   - Baseline is now updated

## Key Simplifications

- No logging beyond basic print statements
- No tests
- No lock mechanism (assume single instance)
- No backup of previous baseline
- No cleanup of old attempts (manual cleanup)
- No retry limits (can loop, but user can interrupt)
- No error classification (assume all push failures are version conflicts)
- No backoff (immediate retry)

## Implementation Steps

### Step 1: Modify `media_sync_push()` to handle push failures

**Location:** `media_sync.py`, function `media_sync_push()`

**Changes:**
- When push fails, check if it's a version conflict
- If yes, raise a special exception: `VersionConflictError`
- Otherwise, raise normal `MediaSyncError`

### Step 2: Create attempt workspace functions

**New function:** `create_attempt_workspace(baseline_path, server_version)`
- Create path: `<baseline_path>_attempt_v{server_version}`
- Hardlink clone entire directory
- Break hardlinks for non-media files
- Return attempt_path

**New function:** `break_hardlinks_for_non_media(attempt_path)`
- Walk directory
- For each file:
  - If extension NOT in `config.allowed_extensions` → break hardlink (delete + copy)
  - If file is `.gpkg` (from config.references) → break hardlink
  - If in `.mergin/` directory → break hardlink (copy entire directory)
- Media files remain hardlinked

### Step 3: Modify main flow to always use attempt workspace (COPY mode)

**Location:** `media_sync.py`, function `main()`

**New flow for COPY mode:**
1. Check if `config.operation_mode == "copy"`
2. If COPY mode:
   - Get current server version
   - Create attempt workspace from baseline
   - Run sync in attempt workspace (with retry loop)
   - If push succeeds → promote attempt to baseline
3. If MOVE mode:
   - Run normal sync in baseline (unchanged behavior)

**New function:** `sync_with_attempt_workspace(mc, driver, baseline_path)`
- Retry loop:
  - Get current server version
  - Create attempt workspace with that version
  - Run sync in attempt workspace:
    - `mc_pull(mc, workspace_path=attempt_path)`
    - Get files to sync
    - `media_sync_push(mc, driver, files, workspace_path=attempt_path)`
  - If push fails (VersionConflictError):
    - Delete attempt workspace
    - Get new server version (will be newer)
    - Continue loop (retry)
  - If push succeeds:
    - Promote attempt to baseline
    - Break loop

**New function:** `promote_attempt_to_baseline(baseline_path, attempt_path)`
- Rename attempt to baseline: `os.rename(attempt_path, baseline_path)`

### Step 4: Modify functions to accept workspace_path parameter

**Functions to modify:**
- `mc_pull(mc, workspace_path=None)` - use workspace_path if provided, else config
- `media_sync_push(mc, driver, files, workspace_path=None)` - use workspace_path if provided
- `_update_references(files, workspace_path=None)` - use workspace_path for gpkg path
- `_check_pending_changes(workspace_path=None)` - use workspace_path if provided

### Step 5: Error handling

**New exception:** `VersionConflictError(MediaSyncError)`
- Raised when push fails due to version conflict

**In `media_sync_push()`:**
- Catch `ClientError` on push
- Check error message for version conflict indicators
- If version conflict → raise `VersionConflictError`
- Otherwise → raise `MediaSyncError`

## Minimal Code Structure

### New Functions (minimal signatures)

```python
# In media_sync.py

class VersionConflictError(MediaSyncError):
    pass

def create_attempt_workspace(baseline_path, server_version):
    """Create attempt workspace with hardlinks, break non-media hardlinks"""
    pass

def break_hardlinks_for_non_media(attempt_path):
    """Break hardlinks for .gpkg, .mergin, and other non-media files"""
    pass

def sync_with_attempt_workspace(mc, driver, baseline_path, server_version):
    """Run sync in attempt workspace, retry on version conflict"""
    pass

def promote_attempt_to_baseline(baseline_path, attempt_path):
    """Rename attempt workspace to become baseline"""
    pass
```

### Modified Functions

```python
def mc_pull(mc, workspace_path=None):
    """Pull with optional workspace_path parameter"""
    # Use workspace_path if provided, else config.project_working_dir
    pass

def media_sync_push(mc, driver, files, workspace_path=None):
    """Push with optional workspace_path, raise VersionConflictError on conflict"""
    # Use workspace_path if provided
    # On push failure, check if version conflict, raise VersionConflictError
    pass

def _update_references(files, workspace_path=None):
    """Update references with optional workspace_path"""
    # Use workspace_path for gpkg path construction
    pass

def main():
    """Main entry point with attempt workspace for COPY mode"""
    if config.operation_mode == "copy":
        # Always use attempt workspace in COPY mode
        sync_with_attempt_workspace(mc, driver, config.project_working_dir)
    else:
        # MOVE mode: normal sync in baseline (unchanged)
        files_to_sync = mc_pull(mc)
        if files_to_sync:
            media_sync_push(mc, driver, files_to_sync)
```

## Retry Loop Logic

```
def sync_with_attempt_workspace(mc, driver, baseline_path):
    while True:
        # Get current server version (may be newer on retry)
        server_version = get_server_version(mc, baseline_path)
        
        # Create attempt workspace from baseline
        attempt_path = create_attempt_workspace(baseline_path, server_version)
        
        try:
            # Sync in attempt workspace
            mc_pull(mc, workspace_path=attempt_path)
            files = get_files_to_sync(attempt_path)
            media_sync_push(mc, driver, files, workspace_path=attempt_path)
            
            # Success! Promote attempt to baseline
            promote_attempt_to_baseline(baseline_path, attempt_path)
            break
            
        except VersionConflictError:
            # Push failed - cleanup attempt
            shutil.rmtree(attempt_path)
            # Baseline is still clean (unchanged)
            # Loop continues: get new server version, create new attempt, retry
            continue
```

## File Changes Summary

**`media_sync.py`:**
- Add `VersionConflictError` exception class
- Add `create_attempt_workspace()` function
- Add `break_hardlinks_for_non_media()` function
- Add `sync_with_attempt_workspace()` function (contains retry loop)
- Add `promote_attempt_to_baseline()` function
- Add `get_server_version()` helper function
- Modify `mc_pull()` to accept `workspace_path` parameter
- Modify `media_sync_push()` to accept `workspace_path` and raise `VersionConflictError`
- Modify `_update_references()` to accept `workspace_path` parameter
- Modify `main()` to check COPY mode and call `sync_with_attempt_workspace()` if COPY mode

**No other files need changes for POC.**

## Hardlink Operations

**Create hardlink clone:**
- Use `os.link()` for files
- Use `shutil.copytree()` with `copy_function=os.link` for directories
- Handle `OSError` if hardlinks not supported (raise informative error)

**Break hardlinks:**
- For each non-media file:
  - `os.remove(file_path)` (removes hardlink)
  - `shutil.copy2(original_path, file_path)` (creates independent copy)
- For `.mergin/` directory:
  - `shutil.rmtree(attempt_mergin)`
  - `shutil.copytree(baseline_mergin, attempt_mergin)`

## Key Points

1. **COPY mode always uses attempt workspace**: Every run creates attempt from baseline
2. **Baseline stays clean**: Original workspace never gets modified during attempt
3. **Retry with latest version**: Each retry gets the newest server version
4. **Simple cleanup**: Delete attempt on failure, baseline remains unchanged
5. **Atomic promotion**: Single rename operation when push succeeds
6. **No state persistence**: Everything derived from current server state
7. **MOVE mode unchanged**: MOVE mode continues to work in baseline (no attempt workspace)
