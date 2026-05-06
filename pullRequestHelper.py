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
    global_inventory = {} 
    initial_global_inventory = {}
    
    # Map JiraID -> { CleanedSubject -> Count }
    jira_inventory = {}
    initial_jira_inventory = {}

    branches_to_check = [config.check_target_branch, config.cherry_pick_base_branch]
    for branch_name in branches_to_check:
        print(f"Inventorying '{branch_name}' starting from {config.start_date}...")
        try:
            ver = GitVersionDescriptor(version=branch_name, version_type='branch')
            crit = GitQueryCommitsCriteria(item_version=ver, from_date=config.start_date)
            
            b_global = {}
            b_jira = {} # { jira: {subject: count} }

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
                global_inventory[k] = max(global_inventory.get(k, 0), v)
            
            for jid, sub_counts in b_jira.items():
                if jid not in jira_inventory: jira_inventory[jid] = {}
                for s, v in sub_counts.items():
                    jira_inventory[jid][s] = max(jira_inventory[jid].get(s, 0), v)

        except Exception as e:
            print(f"Warning: Could not scan {branch_name}: {e}")

    initial_global_inventory = global_inventory.copy()
    # Deep copy jira inventory for mismatch detection
    initial_jira_inventory = {jid: counts.copy() for jid, counts in jira_inventory.items()}
    
    print(f"Release Inventory: {len(global_inventory)} subjects, {len(jira_inventory)} Jira tickets identified.")

    # === 2. SCAN DEVELOP (Consuming Inventories) ===
    print(f"Scanning history of '{config.main_target_branch}'...")
    dev_ver = GitVersionDescriptor(version=config.main_target_branch, version_type='branch')
    dev_crit = GitQueryCommitsCriteria(item_version=dev_ver)

    all_history_commits = []
    found_authors_in_history = set()
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

            found_authors_in_history.add(repo_author_name)
            msg = commit.comment.strip()
            subject = get_first_line(msg)
            jira = extract_jira_id(msg)

            # Redundancy check
            is_redundant_merge = False
            merge_note = ""
            if subject.startswith("Merged PR"):
                full = git_client.get_commit(commit.commit_id, repo.id, project=config.project_name)
                if full.parents and len(full.parents) > 1:
                    try:
                        changes = git_client.get_changes(commit.commit_id, repo.id, project=config.project_name)
                        if not changes.changes: is_redundant_merge = True
                        else: merge_note = f"Merge with {len(changes.changes)} unique changes"
                    except: is_redundant_merge = True
            if is_redundant_merge: continue 
                
            # --- CHERRY-PICK DETECTION (IF-ELSE ORDER) ---
            is_in_release = "No"
            cleaned_dev = clean_subject(subject)
            g_key = (matched_config_name, cleaned_dev)
            
            # 1. Exact Global Match (Author + Subject)
            if global_inventory.get(g_key, 0) > 0:
                is_in_release = "Yes (Exact Match)"
                global_inventory[g_key] -= 1
                # Also consume from Jira inventory if applicable to keep them in sync
                if jira and jira in jira_inventory and cleaned_dev in jira_inventory[jira]:
                    jira_inventory[jira][cleaned_dev] = max(0, jira_inventory[jira][cleaned_dev] - 1)
            
            # 2. Global Count Mismatch
            elif initial_global_inventory.get(g_key, 0) > 0:
                is_in_release = "Needs attention (Subject Count Mismatch)"

            # 3. Jira Ticket Content Match
            elif jira and jira in jira_inventory:
                if cleaned_dev in jira_inventory[jira] and jira_inventory[jira][cleaned_dev] > 0:
                    is_in_release = "Yes (Ticket Match)"
                    jira_inventory[jira][cleaned_dev] -= 1
                elif cleaned_dev in initial_jira_inventory.get(jira, {}):
                    is_in_release = "Needs attention (Ticket Count Mismatch)"
                else:
                    # Jira ID exists, but this subject is completely unknown for that ticket
                    is_in_release = f"Likely (Jira {jira} exists, but subjects differ)"
            
            print(f"Processing: {commit.commit_id[:8]} by {matched_config_name} - {subject[:40]}... [{is_in_release}]")

            # Look up PR info
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

            jira_link = f'=HYPERLINK("{config.jira_base_url}{jira}","{jira}")' if jira else ""
            decision = "no" if is_in_release.startswith("Yes") else "yes" if is_in_release == "No" else ""

            all_history_commits.append({
                'Commit ID': commit.commit_id,
                'Author': matched_config_name,
                'Jira Link': jira_link,
                'Date': commit_date,
                'Message': msg,
                'Merge Status': merge_note,
                'In Release?': is_in_release,
                'cherry pick?': decision
            })
        skip += top

    if not all_history_commits:
        print("No matches found."); return

    all_history_commits.sort(key=lambda x: x['Date'])
    df = pd.DataFrame(all_history_commits)
    try:
        df.to_excel('cherrypick_list.xlsx', index=False, engine='openpyxl')
        print(f"Successfully saved to cherrypick_list.xlsx")
    except: print(f"❌ Error: Close the file.")

if __name__ == "__main__":
    main()
