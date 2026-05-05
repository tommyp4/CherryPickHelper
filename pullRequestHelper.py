import os, base64, re
import pandas as pd
from datetime import datetime, timedelta
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
from azure.devops.v7_1.git.models import GitQueryCommitsCriteria, GitVersionDescriptor, GitPullRequestSearchCriteria
import config

def get_first_line(msg):
    if not msg: return ""
    return msg.split('\n')[0].strip()

def clean_subject(subject):
    subject = re.sub(r'^Merged PR \d+: ', '', subject, flags=re.IGNORECASE)
    # Remove Jira IDs with hyphens, underscores, or no separator
    subject = re.sub(r'ALP[-_]?\d+', '', subject, flags=re.IGNORECASE)
    subject = re.sub(r'^Revert\s+"?', '', subject, flags=re.IGNORECASE)
    subject = re.sub(r'[^\w\s]', '', subject)
    return subject.strip().lower()

def extract_jira_id(text):
    # Match ALP-#####, ALP_#####, or ALP#####
    match = re.search(r'ALP[-_]?(\d+)', str(text), re.IGNORECASE)
    if match:
        # Standardize to ALP-##### format
        return f"ALP-{match.group(1)}"
    return None

def get_matched_config_author(repo_name, target_list):
    """
    Checks if any significant part of a target name exists as a substring 
    within the repo_name. Returns the matched name from target_list.
    """
    repo_name_clean = repo_name.lower()

    for target in target_list:
        # Split target into parts (e.g., 'Last', 'First')
        target_clean = target.lower().replace(',', ' ')
        parts = [p for p in target_clean.split() if len(p) > 2] # Ignore initials

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
    repo = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    
    # === PARSE START DATE ===
    start_dt = datetime.strptime(config.start_date, '%Y-%m-%d %H:%M')

    # === 1. SCAN COMMIT HISTORIES FOR CHERRY-PICKS ===
    branches_to_check = [config.check_target_branch, config.cherry_pick_base_branch]
    release_jira_subjects = {} 
    all_release_cleaned_subjects = set()

    for branch_name in branches_to_check:
        print(f"Scanning history of '{branch_name}' starting from {config.start_date} for existing work...")
        try:
            ver = GitVersionDescriptor(version=branch_name, version_type='branch')
            crit = GitQueryCommitsCriteria(item_version=ver, from_date=config.start_date)
            
            skip = 0
            top = 100
            while True:
                commits = git_client.get_commits(repo.id, search_criteria=crit, project=config.project_name, top=top, skip=skip)
                if not commits: break
                for c in commits:
                    msg = c.comment
                    subject = get_first_line(msg)
                    jira = extract_jira_id(msg)
                    cleaned = clean_subject(subject)
                    if cleaned:
                        all_release_cleaned_subjects.add(cleaned)
                        if jira:
                            if jira not in release_jira_subjects:
                                release_jira_subjects[jira] = set()
                            release_jira_subjects[jira].add(cleaned)
                skip += top
                if len(commits) < top: break
        except Exception as e:
            print(f"Warning: Could not scan branch {branch_name}: {e}")

    print(f"Release Scan Summary: {len(all_release_cleaned_subjects)} unique work items identified across {len(release_jira_subjects)} tickets.")

    # === 2. SCAN MAIN BRANCH (DEVELOP) ===
    print(f"Scanning history of '{config.main_target_branch}'...")
    
    dev_ver = GitVersionDescriptor(version=config.main_target_branch, version_type='branch')
    dev_crit = GitQueryCommitsCriteria(item_version=dev_ver)

    all_history_commits = []
    found_authors_in_history = set()
    skip = 0
    top = 100
    max_scan = 2000

    while skip < max_scan:
        history_commits = git_client.get_commits(repo.id, search_criteria=dev_crit, project=config.project_name, top=top, skip=skip)
        if not history_commits: break
            
        for commit in history_commits:
            # Use committer date (when it landed in branch) rather than author date
            commit_date = commit.committer.date.replace(tzinfo=None)
            
            # Apply our own date filter in Python
            if commit_date < start_dt:
                if skip > 500:
                    skip = max_scan
                    break
                continue

            repo_author_name = commit.author.name
            found_authors_in_history.add(repo_author_name)
            
            # --- FUZZY AUTHOR MATCH (And get canonical name) ---
            matched_config_name = get_matched_config_author(repo_author_name, config.authors)
            if not matched_config_name:
                continue

            msg = commit.comment.strip()
            subject = get_first_line(msg)
            jira = extract_jira_id(msg)

            # Check for structural merge
            is_redundant_merge = False
            if subject.startswith("Merged PR"):
                full_commit = git_client.get_commit(commit.commit_id, repo.id, project=config.project_name)
                if full_commit.parents and len(full_commit.parents) > 1:
                    is_redundant_merge = True

            if is_redundant_merge:
                print(f"Skipping {commit.commit_id[:8]} - Redundant Merged PR record")
                continue 
                
            print(f"Processing: {commit.commit_id[:8]} by {matched_config_name} (Repo: {repo_author_name}) - {subject[:50]}...")
            
            # Look up PR info for better context
            pr_info = "Direct Push"
            pr_title = ""
            try:
                assoc = git_client.get_pull_request_query(
                    queries={'queries': [{'items': [commit.commit_id], 'type': 'commit'}]},
                    repository_id=repo.id, project=config.project_name
                ).results.get(commit.commit_id, [])
                if assoc:
                    relevant_pr = next((p for p in assoc if p.target_ref_name == f'refs/heads/{config.main_target_branch}'), None)
                    if relevant_pr:
                        pr_info = f"PR {relevant_pr.pull_request_id}"
                        pr_title = relevant_pr.title
                        if not jira: jira = extract_jira_id(pr_title)
            except: pass

            jira_link = ""
            if jira:
                url = f"{config.jira_base_url}{jira}"
                jira_link = f'=HYPERLINK("{url}","{jira}")'

            # CHERRY-PICK CHECK
            is_in_release = "No"
            cleaned_dev = clean_subject(subject)
            if cleaned_dev in all_release_cleaned_subjects:
                is_in_release = "Yes (Exact Match)"
            elif jira and jira in release_jira_subjects:
                if cleaned_dev in release_jira_subjects[jira]:
                    is_in_release = "Yes (Ticket Match)"
                else:
                    is_in_release = f"Likely (Jira {jira} exists, but subjects differ)"
            
            # PREFILL
            cherry_pick_decision = "no" if is_in_release.startswith("Yes") else "yes" if is_in_release == "No" else ""

            all_history_commits.append({
                'Commit ID': commit.commit_id,
                'Author': matched_config_name, # Use the name from Config
                'Jira Link': jira_link,
                'Date': commit_date,
                'Message': msg,
                'In Release?': is_in_release,
                'cherry pick?': cherry_pick_decision
            })
        
        skip += top

    print("-" * 40)
    print(f"Scan complete. Found {len(all_history_commits)} work commits by target authors.")
    print(f"Unique authors found in repo history: {sorted(list(found_authors_in_history))}")

    if not all_history_commits:
        return

    all_history_commits.sort(key=lambda x: x['Date'])
    df = pd.DataFrame(all_history_commits)
    try:
        df.to_excel('cherrypick_list.xlsx', index=False, engine='openpyxl')
        print(f"Successfully saved to cherrypick_list.xlsx")
    except PermissionError:
        print(f"❌ Error: Close the Excel file and try again.")

if __name__ == "__main__":
    main()
