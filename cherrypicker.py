import os, base64
from git import Repo, GitCommandError
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
import config

# === RUNTIME DATA ===
pr_ids = []

# === AUTHENTICATION ===
credentials = BasicAuthentication('', config.personal_access_token)
connection = Connection(base_url=config.organization_url, creds=credentials)
git_client = connection.clients.get_git_client()
# REST API headers
pat_bytes = f":{config.personal_access_token}".encode('utf-8')
pat_base64 = base64.b64encode(pat_bytes).decode('utf-8')
headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Basic {pat_base64}'
}

# === GET REPO INFO ===
repo = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
remote_url = f'https://user:{config.personal_access_token}@dev.azure.com/alpineitw/VIEW/_git/VIEW'

# === CLONE REPO IF NOT EXISTS ===
if not os.path.exists(config.local_repo_path):
    print(f"Cloning repository to {config.local_repo_path}...")
    Repo.clone_from(remote_url, config.local_repo_path)
else:
    print(f"Repository already exists at {local_repo_path}")

# === FETCH COMMITS FROM PRs ===
all_commits = []
for pr_id in pr_ids:
    pr = git_client.get_pull_request_by_id(pr_id)
    commits = git_client.get_pull_request_commits(repo.id, pr_id, project=project_name)
    for commit in reversed(commits):
        all_commits.append(commit.commit_id)

print(f"Found {len(all_commits)} commits to cherry-pick.")

# === CHERRY-PICK COMMITS LOCALLY ===
repo = Repo(local_repo_path)
git = repo.git

# Checkout target branch
git.checkout(target_branch)

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
        # Optionally, you can run 'git cherry-pick --continue' here
        try:
            git.execute(["git", "cherry-pick", "--continue"])
            print("✅ Cherry-pick continued.")
        except GitCommandError as continue_error:
            print(f"❌ Error continuing cherry-pick: {continue_error}")
            break


print("Cherry-pick process completed.")


# === PUSH CHANGES TO REMOTE ===
# try:
    # print(f"Pushing changes to remote branch '{target_branch}'...")
    # git.push('origin', target_branch)
    # print("Push successful.")
# except GitCommandError as e:
    # print(f"Error pushing to remote: {e}")
