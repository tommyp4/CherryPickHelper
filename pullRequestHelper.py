import os, base64
import pandas as pd
from datetime import datetime
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
    repo = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    
    # === PARSE START DATE ===
    start_dt = datetime.strptime(config.start_date, '%Y-%m-%d %H:%M')

    print(f"Fetching PRs for {repo.name} starting from {start_dt}...")

    # === FETCH PRs ===
    # We'll fetch completed PRs as they are most likely what we want to cherry-pick
    search_criteria = GitPullRequestSearchCriteria(status='completed')
    prs = git_client.get_pull_requests(
        repository_id=repo.id,
        search_criteria=search_criteria,
        project=config.project_name
    )

    relevant_commits = []

    for pr in prs:
        # Check if PR was closed/merged after start_dt
        # Note: closed_date is when it was merged/closed
        if pr.closed_date and pr.closed_date.replace(tzinfo=None) < start_dt:
            continue
            
        print(f"Checking PR {pr.pull_request_id}: {pr.title}")
        
        # Fetch commits for this PR
        commits = git_client.get_pull_request_commits(repo.id, pr.pull_request_id, project=config.project_name)
        
        for commit in commits:
            author_name = commit.author.name
            commit_date = commit.author.date.replace(tzinfo=None)
            
            if author_name in config.authors and commit_date >= start_dt:
                relevant_commits.append({
                    'Commit ID': commit.commit_id,
                    'Author': author_name,
                    'PR ID': pr.pull_request_id,
                    'PR Title': pr.title,
                    'Date': commit_date,
                    'Message': commit.comment,
                    'cherry pick?': '' # Empty column for user input
                })

    if not relevant_commits:
        print("No matching commits found.")
        return

    # Sort by Date (oldest first)
    relevant_commits.sort(key=lambda x: x['Date'])

    # Convert to DataFrame
    df = pd.DataFrame(relevant_commits)
    
    # Save to Excel
    output_file = 'cherrypick_list.xlsx'
    df.to_excel(output_file, index=False)
    print(f"Saved {len(relevant_commits)} commits to {output_file}")

if __name__ == "__main__":
    main()
