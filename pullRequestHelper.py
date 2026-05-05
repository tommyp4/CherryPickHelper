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
    # Remove "Merged PR ####: "
    subject = re.sub(r'^Merged PR \d+: ', '', subject, flags=re.IGNORECASE)
    # Remove Jira ID "ALP-#### " 
    subject = re.sub(r'ALP-\d+', '', subject, flags=re.IGNORECASE)
    # Remove "Revert ..."
    subject = re.sub(r'^Revert\s+"?', '', subject, flags=re.IGNORECASE)
    # Clean up whitespace and punctuation
    subject = re.sub(r'[^\w\s]', '', subject)
    return subject.strip().lower()

def extract_jira_id(text):
    match = re.search(r'(ALP-\d+)', str(text), re.IGNORECASE)
    return match.group(1).upper() if match else None

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
    # Map Jira ID -> set of cleaned subjects
    release_jira_subjects = {} 
    # Global set of all cleaned subjects in release
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
    print(f"Scanning history of '{config.main_target_branch}' starting from {start_dt}...")
    
    dev_ver = GitVersionDescriptor(version=config.main_target_branch, version_type='branch')
    dev_crit = GitQueryCommitsCriteria(item_version=dev_ver, from_date=config.start_date)

    all_history_commits = []
    skip = 0
    top = 100
    
    while True:
        history_commits = git_client.get_commits(repo.id, search_criteria=dev_crit, project=config.project_name, top=top, skip=skip)
        if not history_commits: break
            
        for commit in history_commits:
            author_name = commit.author.name
            commit_date = commit.author.date.replace(tzinfo=None)
            
            if author_name in config.authors:
                msg = commit.comment.strip()
                subject = get_first_line(msg)
                jira = extract_jira_id(msg)
                
                # Filter out redundant PR merge records
                is_structural_merge = False
                if subject.startswith("Merged PR"):
                    full_commit = git_client.get_commit(commit.commit_id, repo.id, project=config.project_name)
                    is_structural_merge = len(full_commit.parents) > 1 if full_commit.parents else False
                
                if is_structural_merge:
                    continue 
                
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

                # Jira Link
                jira_link = ""
                if jira:
                    url = f"{config.jira_base_url}{jira}"
                    jira_link = f'=HYPERLINK("{url}","{jira}")'

                # CHERRY-PICK CHECK (REFINED)
                is_in_release = "No"
                cleaned_dev = clean_subject(subject)
                
                if cleaned_dev in all_release_cleaned_subjects:
                    is_in_release = "Yes (Exact Match)"
                elif jira and jira in release_jira_subjects:
                    # Jira ticket is in release. Let's see if the content matches.
                    if cleaned_dev in release_jira_subjects[jira]:
                        is_in_release = "Yes (Ticket Match)"
                    else:
                        is_in_release = f"Likely (Jira {jira} exists, but subjects differ)"
                
                # PREFILL CHERRY PICK COLUMN
                cherry_pick_decision = ""
                if is_in_release.startswith("Yes"):
                    cherry_pick_decision = "no"
                elif is_in_release == "No":
                    cherry_pick_decision = "yes"
                # Else: leave blank for Likely cases

                print(f"Commit: {commit.commit_id[:8]} - {subject[:40]}... [In Release: {is_in_release}]")

                all_history_commits.append({
                    'Commit ID': commit.commit_id,
                    'Author': author_name,
                    'Source': pr_info,
                    'Jira Link': jira_link,
                    'Date': commit_date,
                    'Message': msg,
                    'In Release?': is_in_release,
                    'cherry pick?': cherry_pick_decision
                })
        
        skip += top
        if len(history_commits) < top: break

    if not all_history_commits:
        print("No matching commits found.")
        return

    all_history_commits.sort(key=lambda x: x['Date'])
    df = pd.DataFrame(all_history_commits)
    
    try:
        df.to_excel('cherrypick_list.xlsx', index=False, engine='openpyxl')
        print(f"Successfully saved {len(all_history_commits)} commits to cherrypick_list.xlsx")
    except PermissionError:
        print(f"❌ Error: Could not save to Excel. Please close the file.")

if __name__ == "__main__":
    main()
