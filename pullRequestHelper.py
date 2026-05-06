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
    subject = re.sub(r'ALP[-_]?\d+', '', subject, flags=re.IGNORECASE)
    subject = re.sub(r'^Revert\s+"?', '', subject, flags=re.IGNORECASE)
    subject = re.sub(r'[^\w\s]', '', subject)
    return subject.strip().lower()

def extract_jira_id(text):
    match = re.search(r'ALP[-_]?(\d+)', str(text), re.IGNORECASE)
    if match:
        return f"ALP-{match.group(1)}"
    return None

def get_matched_config_author(repo_name, target_list):
    """Returns the config name if matched, else None."""
    repo_name_clean = repo_name.lower()
    for target in target_list:
        target_clean = target.lower().replace(',', ' ')
        parts = [p for p in target_clean.split() if len(p) > 2]
        for p in parts:
            if p in repo_name_clean:
                return target
    return None

def main():
    # === AUTHENTICATION ===
    credentials = BasicAuthentication('', config.personal_access_token)
    connection = Connection(base_url=config.organization_url, creds=credentials)
    git_client = connection.clients.get_git_client()
    repo = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    
    start_dt = datetime.strptime(config.start_date, '%Y-%m-%d %H:%M')

    # === 1. SCAN RELEASE HISTORIES (Building Inventories) ===
    # Map (Author, CleanedSubject) -> Count
    release_global_inventory = {} 
    # Map JiraID -> { CleanedSubject -> Count }
    release_jira_inventory = {}

    branches_to_check = [config.check_target_branch, config.cherry_pick_base_branch]
    for branch_name in branches_to_check:
        print(f"Inventorying '{branch_name}' starting from {config.start_date}...")
        try:
            ver = GitVersionDescriptor(version=branch_name, version_type='branch')
            crit = GitQueryCommitsCriteria(item_version=ver, from_date=config.start_date)
            
            b_global = {}
            b_jira = {} 

            commits = git_client.get_commits(repo.id, search_criteria=crit, project=config.project_name, top=1000)
            if commits:
                for c in commits:
                    subj = get_first_line(c.comment)
                    cleaned = clean_subject(subj)
                    author = get_matched_config_author(c.author.name, config.authors)
                    jira = extract_jira_id(c.comment)

                    if cleaned and author:
                        key = (author, cleaned)
                        b_global[key] = b_global.get(key, 0) + 1
                    
                    if jira and cleaned:
                        if jira not in b_jira: b_jira[jira] = {}
                        b_jira[jira][cleaned] = b_jira[jira].get(cleaned, 0) + 1
            
            # Merge branch counts into global inventories using MAX
            for k, v in b_global.items():
                release_global_inventory[k] = max(release_global_inventory.get(k, 0), v)
            
            for jid, sub_counts in b_jira.items():
                if jid not in release_jira_inventory: release_jira_inventory[jid] = {}
                for s, v in sub_counts.items():
                    release_jira_inventory[jid][s] = max(release_jira_inventory[jid].get(s, 0), v)

        except Exception as e:
            print(f"Warning: Could not scan {branch_name}: {e}")

    print(f"Release Inventory: {len(release_global_inventory)} subjects, {len(release_jira_inventory)} Jira tickets identified.")

    # === 2. SCAN DEVELOP (Phase A: Collection & Counting) ===
    print(f"Collecting commits from history of '{config.main_target_branch}'...")
    dev_ver = GitVersionDescriptor(version=config.main_target_branch, version_type='branch')
    dev_crit = GitQueryCommitsCriteria(item_version=dev_ver)

    develop_commits = [] # List of (commit_obj, data_dict)
    dev_global_counts = {} # (Author, CleanedSubject) -> Count
    dev_jira_counts = {}   # JiraID -> { CleanedSubject -> Count }

    skip = 0
    top = 100
    while skip < 2000:
        history_commits = git_client.get_commits(repo.id, search_criteria=dev_crit, project=config.project_name, top=top, skip=skip)
        if not history_commits: break
            
        for commit in history_commits:
            commit_date = commit.committer.date.replace(tzinfo=None)
            if commit_date < start_dt:
                if skip > 500: skip = 2000; break
                continue

            repo_author_name = commit.author.name
            matched_config_name = get_matched_config_author(repo_author_name, config.authors)
            if not matched_config_name: continue

            msg = commit.comment.strip()
            subject = get_first_line(msg)
            cleaned = clean_subject(subject)
            jira = extract_jira_id(msg)

            # Redundancy check
            is_redundant_merge = False
            merge_note = ""
            
            # A structural merge has > 1 parent
            full = git_client.get_commit(commit.commit_id, repo.id, project=config.project_name)
            is_structural = full.parents and len(full.parents) > 1

            if is_structural:
                if subject.startswith("Merged PR"):
                    # System generated PR record - skip
                    is_redundant_merge = True
                elif subject.startswith("Merge branch"):
                    # Manual developer sync - keep and flag
                    merge_note = "Manual Conflict Sync"
                else:
                    # Other structural merges - keep and flag to be safe
                    merge_note = "Structural Merge"

            if is_redundant_merge:
                continue 
            
            # Record for phase B
            develop_commits.append({
                'commit': commit,
                'author': matched_config_name,
                'subject': subject,
                'cleaned': cleaned,
                'jira': jira,
                'merge_note': merge_note,
                'date': commit_date,
                'full_msg': msg
            })

            # Update counts on develop
            g_key = (matched_config_name, cleaned)
            dev_global_counts[g_key] = dev_global_counts.get(g_key, 0) + 1
            if jira:
                if jira not in dev_jira_counts: dev_jira_counts[jira] = {}
                dev_jira_counts[jira][cleaned] = dev_jira_counts[jira].get(cleaned, 0) + 1

        skip += top

    # === 3. SCAN DEVELOP (Phase B: Decision Logic) ===
    print(f"Analyzing {len(develop_commits)} commits for cherry-picking...")
    all_final_rows = []

    for item in develop_commits:
        is_in_release = "No"
        author = item['author']
        cleaned = item['cleaned']
        jira = item['jira']
        g_key = (author, cleaned)

        # RULES IN ORDER (IF-ELSE)
        
        # 1. Check Global Exact Match (Author + Subject)
        if g_key in release_global_inventory:
            if dev_global_counts[g_key] == release_global_inventory[g_key]:
                is_in_release = "Yes (Exact Match)"
            else:
                is_in_release = "Needs attention (Subject Count Mismatch)"
        
        # 2. Check Jira Match (If not already determined)
        elif jira and jira in release_jira_inventory:
            if cleaned in release_jira_inventory[jira]:
                if dev_jira_counts[jira][cleaned] == release_jira_inventory[jira][cleaned]:
                    is_in_release = "Yes (Ticket Match)"
                else:
                    is_in_release = "Needs attention (Ticket Count Mismatch)"
            else:
                # Jira ID found but this specific subject is new to the release branch
                is_in_release = f"Likely (Jira {jira} exists, but subjects differ)"
        
        # Look up PR info for better context in report
        pr_info = "Direct Push"
        pr_title = ""
        try:
            assoc = git_client.get_pull_request_query(
                queries={'queries': [{'items': [item['commit'].commit_id], 'type': 'commit'}]},
                repository_id=repo.id, project=config.project_name
            ).results.get(item['commit'].commit_id, [])
            if assoc:
                relevant_pr = next((p for p in assoc if p.target_ref_name == f'refs/heads/{config.main_target_branch}'), None)
                if relevant_pr:
                    pr_info = f"PR {relevant_pr.pull_request_id}"
                    pr_title = relevant_pr.title
                    if not jira: jira = extract_jira_id(pr_title)
        except: pass

        jira_link = f'=HYPERLINK("{config.jira_base_url}{jira}","{jira}")' if jira else ""
        decision = "no" if is_in_release.startswith("Yes") else "yes" if is_in_release == "No" else ""

        all_final_rows.append({
            'Commit ID': item['commit'].commit_id,
            'Author': author,
            'Jira Link': jira_link,
            'Date': item['date'],
            'Message': item['full_msg'],
            'Merge Status': item['merge_note'],
            'In Release?': is_in_release,
            'cherry pick?': decision
        })

    if not all_final_rows:
        print("No matches found."); return

    all_final_rows.sort(key=lambda x: x['Date'])
    df = pd.DataFrame(all_final_rows)
    try:
        df.to_excel('cherrypick_list.xlsx', index=False, engine='openpyxl')
        print(f"Successfully saved to cherrypick_list.xlsx")
    except: print(f"❌ Error: Close the file.")

if __name__ == "__main__":
    main()
