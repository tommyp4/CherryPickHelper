import os, base64
import pandas as pd
from git import Repo, GitCommandError
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
import config

def main():
    # === AUTHENTICATION ===
    credentials = BasicAuthentication('', config.personal_access_token)
    connection = Connection(base_url=config.organization_url, creds=credentials)
    git_client = connection.clients.get_git_client()

    # === CONFIGURATION ===
    # This script now primarily reads from cherrypick_list.xlsx
    excel_file = 'cherrypick_list.xlsx'
    all_commits = []

    if os.path.exists(excel_file):
        print(f"Reading commits from {excel_file}...")
        df = pd.read_excel(excel_file)
        # Ensure the column exists
        if 'cherry pick?' in df.columns:
            # Filter for 'yes' (case-insensitive)
            mask = df['cherry pick?'].astype(str).str.lower() == 'yes'
            selected_df = df[mask]
            all_commits = selected_df['Commit ID'].tolist()
            print(f"Found {len(all_commits)} commits marked for cherry-picking.")
        else:
            print(f"Error: Column 'cherry pick?' not found in {excel_file}")
            return
    else:
        print(f"{excel_file} not found. Use pullrequesthelper.py to generate it.")
        # Fallback to hardcoded list if desired (currently empty in previous turn)
        # pr_ids = [] 
        # ... (old logic could go here if needed)
        return

    if not all_commits:
        print("No commits to cherry-pick.")
        return

    # === GET REPO INFO ===
    repo_info = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    remote_url = f'https://user:{config.personal_access_token}@dev.azure.com/alpineitw/VIEW/_git/VIEW'

    # === CLONE REPO IF NOT EXISTS ===
    if not os.path.exists(config.local_repo_path):
        print(f"Cloning repository to {config.local_repo_path}...")
        Repo.clone_from(remote_url, config.local_repo_path)
    else:
        print(f"Repository already exists at {config.local_repo_path}")

    # === CHERRY-PICK COMMITS LOCALLY ===
    repo = Repo(config.local_repo_path)
    git = repo.git

    # Checkout target branch
    print(f"Checking out {config.target_branch}...")
    git.checkout(config.target_branch)

    # Cherry-pick each commit
    for commit_id in all_commits:
        try:
            print(f"Cherry-picking {commit_id}...")
            git.cherry_pick(commit_id)
        except GitCommandError as e:
            print(f"\n❌ Conflict or error cherry-picking {commit_id}:")
            print(e)
            print("\n🛠️ Please resolve the conflict manually in your Git client.")
            input("✅ Press Enter to continue after resolving the conflict...")
            # Attempt to continue if the user resolved it
            try:
                # We check if a cherry-pick is still in progress
                if os.path.exists(os.path.join(config.local_repo_path, '.git', 'CHERRY_PICK_HEAD')):
                    git.execute(["git", "cherry-pick", "--continue"])
                    print("✅ Cherry-pick continued.")
            except GitCommandError as continue_error:
                print(f"❌ Error continuing cherry-pick: {continue_error}")
                break

    print("Cherry-pick process completed.")

if __name__ == "__main__":
    main()
