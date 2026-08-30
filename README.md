# CherryPick Helper

An automated toolset to identify missing code changes from `develop` and batch cherry-pick them into a release branch, with built-in Jira integration and Excel-based review.

## Prerequisites

1. **Python 3.12+**
2. **Required Libraries**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Azure DevOps PAT**: You need a Personal Access Token with `Code (Read & Write)` and `Pull Request (Read & Write)` scopes.

---

## Setup

1. **Environment Variables**: Create a `.env` file in the root directory (do not commit this):
   ```text
   AZURE_DEVOPS_PAT=your_token_here
   ```
2. **Configuration**: Copy `config.py.example` to `config.py` and update the following:
   - `organization_url`: Your Azure DevOps org URL.
   - `project_name` / `repository_name`: Target repo details.
   - `main_target_branch`: The branch to scan for work (usually `develop`).
   - `check_target_branch`: The release branch to check for existing work (e.g., `release/26.01.00`).
   - `cherry_pick_base_branch`: The branch to start your new work from (e.g., `release/26.01.01`).
   - `start_date`: How far back to look (format: `YYYY-MM-DD HH:MM`).
   - `authors`: List of display names to track (e.g., `['Last, First']`).

---

## Workflow

### All-in-One Command (Recommended)
You can run the entire workflow in one go using:
```powershell
py runFullPipeline.py
```
This script will interactively guide you through all steps listed below.

### Individual Steps
If you need to run specific parts of the process manually:

### Step 1: Verify Names (Optional)
Run this if you aren't sure if your author names match the Azure DevOps display names.
```powershell
py authorNameChecker.py
```

### Step 2: Generate the Cherry-Pick List
This script scans the Git history of all relevant branches and creates an Excel report.
```powershell
py pullRequestHelper.py
```

### Step 3: Fetch Jira Fix Versions (Optional)
This script updates the Excel file with the "Fix Version" from Jira for each ticket.
```powershell
py jiraHelper.py
```
**Output:** Updates `cherrypick_list.xlsx` with a new column.

### Step 4: Review the Excel File
1. Open `cherrypick_list.xlsx`.
2. Review any rows where the `Action` column is **blank** (Likely matches).
3. Type `yes` for any additional commits you want to move.
4. **Save and Close** the file.

### Step 5: Execute Batch Cherry-Pick
This script automates the Git operations in your local repository.
```powershell
py cherryPickHelper.py
```
**Interactive Prompts:**
- **Cherry-pick blanks?**: Choose once at startup if you want to include rows where `Action` is empty. (Decision is temporary for the run and not saved).
- **Pause at conflicts?**: Choose if you want the script to pause for manual resolution in VS, or automatically skip conflicted commits (Unattended Mode).

**What it does:**
1. Aborts any hanging Git operations (failed merges/cherry-picks).
2. Hard resets your local base branch to match the remote exactly.
3. Deletes your local `target_branch` and recreates it fresh from the base.
4. Cherry-picks your selected commits one by one.
5. **Success Tracking**: Updates the `Result` column. If a commit is already present, it marks it as `no changes to commit` and updates the `Audit Trail` status to `Yes`.

### Step 6: Final Sync Verification (Optional)
This tool provides the "Final Proof" by comparing actual code patches between branches.
```powershell
py verifyBranchSync.py
```
**What it does:**
- Runs `git cherry` to compare the `develop` branch against the `release` branch at the patch level.
- Filters by your team's author list.
- Reports exactly which commits are physically missing from the release branch (even if Jira or subject matching was ambiguous).

---

## Reverse Direction: Release Audit

The workflow above answers *"what is missing from the release?"*. `releaseAudit.py`
answers the opposite question — *"what is present in the release that was never
meant for it?"* — which is the risk when you branch late and the release inherits
whatever was sitting on `develop` at branch point.

```powershell
py releaseAudit.py
```

This is a **standalone, read-only** tool. It runs `git log` plus Jira lookups, mutates
no Git state, and does not touch `cherrypick_list.xlsx`. It uses its own `audit_*`
config keys so it cannot disturb the cherry-pick configuration.

**How it works:**
1. Enumerates commits in `audit_release_branch` that are not in
   `audit_previous_release_branch`. This range is deliberately release-to-release —
   a `develop` comparison cannot see inherited work, because `develop` and the
   release branch share that history.
2. Attributes a Jira ticket to each commit, falling back to the parent
   `Merged PR NNNN: ALP-XXXXX` commit for intermediate commits that carry no ID.
3. Compares each ticket's Fix Version **numerically** against `audit_target_version`.

**Verdicts:**
- `STRAY` — targets a *later* release than this branch ships. The finding.
- `REVIEW` — no ticket, no Fix Version, or a non-mainline product version. Unknown, not clean.
- `OK` — targets this release, or an earlier one that landed late.

**Flags:**
- `--dry-run` — validate ticket-attribution coverage without making any Jira calls.
- `--all` — audit every author. By default only `config.authors` are reported.

**Output:** `release_audit.xlsx`, sorted findings-first.

**Exit codes** (so this can gate a release pipeline):

| Code | Meaning |
| :--- | :--- |
| `0` | No strays found. |
| `1` | Could not complete — bad config, Git/Jira failure, empty commit range, or the report could not be saved. |
| `2` | Strays found. |

Note that an empty commit range exits `1`, not `0`. It almost always means a
branch name is wrong, and reporting that as a pass would be a false all-clear.

**Tests:** the classification logic has unit tests. They need no network and no
extra packages:
```powershell
py test_releaseAudit.py
```

> A `STRAY` is a review candidate, not a revert order. Future-version work can
> legitimately be present if this release depended on it, and a stray can also mean
> the *Jira* Fix Version is wrong rather than the code.

---

## File Overview

- `runFullPipeline.py`: All-in-one interactive pipeline runner.
- `pullRequestHelper.py`: The data-gathering engine (Scans ADO API).
- `jiraHelper.py`: Enriches the Excel with Jira Fix Versions and overrides decisions.
- `cherryPickHelper.py`: The Git automation engine (Uses local Git).
- `verifyBranchSync.py`: Patch-level branch sync verification.
- `releaseAudit.py`: Reverse audit — finds future-release work wrongly present in a release branch.
- `test_releaseAudit.py`: Dependency-free unit tests for the audit's classification logic.
- `authorNameChecker.py`: Verification utility for config author names.
- `shared.py`: Shared utility functions.
- `compare.py`: Regression comparison tool for Excel output.
- `config.py`: Your local, private configuration.
- `cherrypick_list.xlsx`: Your interactive work manifest.
