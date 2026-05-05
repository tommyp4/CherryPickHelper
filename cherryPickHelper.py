import os, base64
import pandas as pd
from git import Repo, GitCommandError
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
import config
from openpyxl import load_workbook

def main():
    # === AUTHENTICATION ===
    credentials = BasicAuthentication('', config.personal_access_token)
    connection = Connection(base_url=config.organization_url, creds=credentials)
    git_client = connection.clients.get_git_client()

    # === CONFIGURATION ===
    excel_file = 'cherrypick_list.xlsx'
    
    if not os.path.exists(excel_file):
        print(f"{excel_file} not found. Use pullRequestHelper.py to generate it.")
        return

    print(f"Reading commits from {excel_file}...")
    
    # We use pandas just to easily identify which rows to process
    df = pd.read_excel(excel_file)

    if 'cherry pick?' not in df.columns:
        print(f"Error: Column 'cherry pick?' not found in {excel_file}")
        return

    # Identify commits to cherry-pick
    mask = df['cherry pick?'].astype(str).str.lower() == 'yes'
    to_pick_df = df[mask]

    if to_pick_df.empty:
        print("No commits marked 'yes' for cherry-picking.")
        return

    print(f"Found {len(to_pick_df)} commits marked for cherry-picking.")

    # === GET REPO INFO ===
    repo_info = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    remote_url = f'https://user:{config.personal_access_token}@dev.azure.com/alpineitw/VIEW/_git/VIEW'

    # === PREPARE REPOSITORY ===
    if not os.path.exists(config.local_repo_path):
        print(f"Cloning repository to {config.local_repo_path}...")
        repo = Repo.clone_from(remote_url, config.local_repo_path)
    else:
        print(f"Using existing repository at {config.local_repo_path}")
        repo = Repo(config.local_repo_path)
    
    git = repo.git

    # === ROBUST CLEANUP ===
    print("Performing pre-flight cleanup...")
    try:
        git.execute(['git', 'cherry-pick', '--abort'])
    except GitCommandError: pass
    try:
        git.execute(['git', 'merge', '--abort'])
    except GitCommandError: pass
    
    print("Clearing local changes and index errors...")
    git.reset('--hard')
    git.clean('-fd')

    print("Fetching all changes from remote...")
    repo.remotes.origin.fetch()

    base_branch = config.cherry_pick_base_branch
    target_branch = config.target_branch
    
    print(f"Updating base branch '{base_branch}' from origin...")
    git.checkout('-f', base_branch)
    git.reset('--hard', f'origin/{base_branch}')

    print(f"Ensuring local branch '{target_branch}' is clean...")
    try:
        if repo.active_branch.name == target_branch:
            git.checkout(base_branch)
        if target_branch in repo.heads:
            print(f"Deleting existing local branch '{target_branch}'...")
            git.branch('-D', target_branch)
    except Exception as e:
        print(f"Note: Could not delete local branch: {e}")

    print(f"Creating new branch '{target_branch}' from '{base_branch}'...")
    git.checkout('-b', target_branch, base_branch)

    # === CHERRY-PICK LOOP ===
    # We will store the results in a dictionary to write back via openpyxl
    results = {} # { commit_id: 'yes'/'no' }
    
    success_count = 0
    fail_count = 0

    for i, (idx, row) in enumerate(to_pick_df.iterrows()):
        commit_id = row['Commit ID']
        msg = row['Message']
        
        print(f"\n[{i + 1}/{len(to_pick_df)}] Cherry-picking {commit_id[:8]} - {str(msg)[:50]}...")
        
        try:
            git.cherry_pick(commit_id)
            print("✅ Success")
            results[commit_id] = 'yes'
            success_count += 1
        except GitCommandError:
            print(f"❌ Conflict or error. Aborting this commit.")
            results[commit_id] = 'no'
            fail_count += 1
            try:
                git.execute(['git', 'cherry-pick', '--abort'])
            except GitCommandError: pass

    # === SAVE RESULTS (PRESERVING FORMULAS) ===
    print(f"\nProcess completed. Success: {success_count}, Failed: {fail_count}")
    print(f"Updating {excel_file} with success status while preserving hyperlinks...")
    
    try:
        wb = load_workbook(excel_file)
        ws = wb.active

        # 1. Find the 'success' and 'Commit ID' column indices
        col_map = {cell.value: cell.column for cell in ws[1]}
        
        if 'success' not in col_map:
            # Add success column if missing
            new_col = ws.max_column + 1
            ws.cell(row=1, column=new_col).value = 'success'
            col_map['success'] = new_col
            
        success_col = col_map['success']
        id_col = col_map['Commit ID']

        # 2. Iterate through rows and update success column based on Commit ID
        # Row 2 is the first data row
        for row_idx in range(2, ws.max_row + 1):
            commit_id = ws.cell(row=row_idx, column=id_col).value
            if commit_id in results:
                ws.cell(row=row_idx, column=success_col).value = results[commit_id]

        wb.save(excel_file)
        print(f"Successfully updated {excel_file}.")
    except PermissionError:
        print(f"❌ Error: Could not save to {excel_file}. Please close the file.")
    except Exception as e:
        print(f"❌ Error updating Excel: {e}")

if __name__ == "__main__":
    main()
