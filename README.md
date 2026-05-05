# CherryPick Helper

An automated toolset to identify missing code changes from `develop` and batch cherry-pick them into a release branch, with built-in Jira integration and Excel-based review.

## 📋 Prerequisites

1. **Python 3.12+**
2. **Required Libraries**:
   ```bash
   pip install pandas openpyxl azure-devops GitPython python-dotenv
   ```
3. **Azure DevOps PAT**: You need a Personal Access Token with `Code (Read & Write)` and `Pull Request (Read & Write)` scopes.

---

## 🛠️ Setup

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

## 🚀 Workflow

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
2. Review any rows where the `cherry pick?` column is **blank** (Likely matches).
3. Type `yes` for any additional commits you want to move.
4. **Save and Close** the file.

### Step 4: Execute Batch Cherry-Pick
This script automates the Git operations in your local repository.
```powershell
py cherryPickHelper.py
```
**What it does:**
1. Aborts any hanging Git operations (failed merges/cherry-picks).
2. Hard resets your local base branch to match the remote exactly.
3. Deletes your local `target_branch` and recreates it fresh from the base.
4. Cherry-picks every `yes` commit from the Excel file one by one.
5. **Auto-Skips Conflicts**: If a commit conflicts, it aborts that commit and moves to the next.
6. **Success Tracking**: Updates the `success` column in your Excel file (preserves hyperlinks).

---

## 📄 File Overview

- `pullRequestHelper.py`: The data-gathering engine (Scans ADO API).
- `cherryPickHelper.py`: The Git automation engine (Uses local Git).
- `authorNameChecker.py`: Verification utility for config.
- `config.py`: Your local, private configuration.
- `cherrypick_list.xlsx`: Your interactive work manifest.
