import os
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria
import config

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

    print(f"Checking author names for repository: {repo.name}")
    print(f"Target authors to verify: {config.authors}")
    print("-" * 40)

    # === FETCH RECENT PRs ===
    # We fetch a larger batch of PRs to get a good sample of author names
    print("Fetching recent PRs to verify author names...")
    search_criteria = GitPullRequestSearchCriteria(status='all')
    recent_prs = git_client.get_pull_requests(
        repository_id=repo.id,
        search_criteria=search_criteria,
        project=config.project_name,
        top=100
    )

    # === COLLECT UNIQUE NAMES ===
    found_authors = set()
    for pr in recent_prs:
        # Check PR creator
        if pr.created_by:
            found_authors.add(pr.created_by.display_name)
        
        # We could also check commit authors, but PR creators are usually the target
        # For more thoroughness, we check a few commits if needed, 
        # but usually display_name in ADO is consistent.

    # === VERIFY CONFIG NAMES ===
    missing_authors = []
    for author in config.authors:
        if author not in found_authors:
            missing_authors.append(author)

    # === OUTPUT RESULTS ===
    if not missing_authors:
        print("✅ Success: All authors in config were found in recent PR history.")
    else:
        print("⚠️  Warning: The following authors in your config were NOT found in the last 100 PRs:")
        for author in missing_authors:
            print(f"  - {author}")
        
        print("\nSuggestions:")
        print("1. Check for typos (e.g., 'Thomas Ptak' vs 'Ptak, Thomas').")
        print("2. Here are some names that WERE found (sample):")
        sample_size = min(10, len(found_authors))
        for name in list(found_authors)[:sample_size]:
            print(f"  - {name}")

if __name__ == "__main__":
    main()
