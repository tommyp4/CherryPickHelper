import os
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria, GitVersionDescriptor, GitQueryCommitsCriteria
import config

def get_matched_config_author(repo_name, target_list):
    """
    Checks if any significant part of a target name exists as a substring 
    within the repo_name. Returns the matched name from target_list.
    Handles names of any length (First Middle Last etc).
    """
    repo_name_clean = repo_name.lower()

    for target in target_list:
        # Split target into all its parts (space, comma, etc)
        target_clean = target.lower().replace(',', ' ')
        parts = [p for p in target_clean.split() if len(p) > 2] # Ignore short initials/parts

        for p in parts:
            if p in repo_name_clean:
                return target
    return None

def main():
    # === AUTHENTICATION ===
    credentials = BasicAuthentication('', config.personal_access_token)
    connection = Connection(base_url=config.organization_url, creds=credentials)
    git_client = connection.clients.get_git_client()

    # === GET REPO ===
    try:
        repo = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    except Exception as e:
        print(f"❌ Error accessing repository: {e}")
        return

    print(f"Checking authors for repository: {repo.name}")
    print(f"Target authors to verify: {len(config.authors)} names listed.")
    print("-" * 40)

    # === FETCH RECENT HISTORY ===
    # We check both PRs and Commits to get a wide sample of names used in this repo
    print("Fetching recent repository history to verify names...")
    ver = GitVersionDescriptor(version=config.main_target_branch, version_type='branch')
    crit = GitQueryCommitsCriteria(item_version=ver)
    
    found_repo_names = set()
    
    # Sample last 500 commits
    try:
        commits = git_client.get_commits(repo.id, search_criteria=crit, project=config.project_name, top=500)
        for c in commits:
            found_repo_names.add(c.author.name)
            found_repo_names.add(c.committer.name)
    except: pass

    # === VERIFY CONFIG NAMES ===
    matched_targets = set()
    for repo_name in found_repo_names:
        match = get_matched_config_author(repo_name, config.authors)
        if match:
            matched_targets.add(match)

    missing_authors = [a for a in config.authors if a not in matched_targets]

    # === OUTPUT RESULTS ===
    if not missing_authors:
        print("✅ Success: All authors in your config have been matched to names in recent history.")
    else:
        print(f"⚠️  Warning: {len(missing_authors)} author(s) in your config were NOT found in the last 500 commits:")
        for author in sorted(missing_authors):
            print(f"  - {author}")
        
        print("\nSuggestions:")
        print("1. Check for typos (e.g., 'First Last' vs 'Last, First').")
        print("2. Ensure the author has made a commit since the branch started.")
        print("3. Here are some names that WERE found in the repo (sample):")
        sample_size = min(15, len(found_repo_names))
        for name in sorted(list(found_repo_names))[:sample_size]:
            print(f"  - {name}")

if __name__ == "__main__":
    main()
