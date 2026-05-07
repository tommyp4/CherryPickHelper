import os
from git import Repo
import config

def get_matched_config_author(repo_name, target_list):
    """Checks if any significant part of a target name exists as a substring."""
    repo_name_clean = repo_name.lower()
    for target in target_list:
        target_clean = target.lower().replace(',', ' ')
        parts = [p for p in target_clean.split() if len(p) > 2]
        for p in parts:
            if p in repo_name_clean:
                return target
    return None

def main():
    if not os.path.exists(config.local_repo_path):
        print(f"❌ Error: Repository path {config.local_repo_path} not found.")
        return

    repo = Repo(config.local_repo_path)
    git = repo.git

    print(f"--- Definitive Branch Sync Verification ---")
    print(f"Base (Release):   {config.cherry_pick_base_branch}")
    print(f"Source (Develop): {config.main_target_branch}")
    print("-" * 40)

    print("Fetching latest changes from remote...")
    repo.remotes.origin.fetch()

    # Use origin versions to be 100% sure we are comparing against the server state
    upstream = f"origin/{config.cherry_pick_base_branch}"
    head = f"origin/{config.main_target_branch}"

    print(f"Analyzing patches between {upstream} and {head}...")
    try:
        # git cherry -v <upstream> <head>
        # + means the patch is missing from upstream
        # - means the patch is already in upstream
        cherry_output = git.cherry("-v", upstream, head)
    except Exception as e:
        print(f"❌ Error running git cherry: {e}")
        return

    lines = cherry_output.splitlines()
    missing_count = 0
    total_scanned = len(lines)

    print(f"Scanned {total_scanned} commits in {config.main_target_branch} history.")
    print("-" * 40)
    print(f"COMMITS MISSING FROM {config.cherry_pick_base_branch}:")

    for line in lines:
        if not line.startswith("+"):
            continue
        
        parts = line.split(maxsplit=2)
        if len(parts) < 2: continue
        
        commit_id = parts[1]
        
        # Fetch commit object to check author
        commit = repo.commit(commit_id)
        author_name = commit.author.name
        
        matched_author = get_matched_config_author(author_name, config.authors)
        
        if matched_author:
            missing_count += 1
            subject = commit.message.splitlines()[0]
            print(f"[{missing_count}] {commit_id[:8]} | {matched_author}")
            print(f"    Date: {commit.committer_datetime}")
            print(f"    Msg:  {subject[:80]}...")
            print("-" * 20)

    if missing_count == 0:
        print("\n✅ Success! All team commits from develop are physically present in the release branch.")
    else:
        print(f"\n⚠️  Found {missing_count} commits by your team that are physically missing from the release branch.")
        print("Note: If a commit was cherry-picked but modified manually, it might show up here even if the logic is present.")

if __name__ == "__main__":
    main()
