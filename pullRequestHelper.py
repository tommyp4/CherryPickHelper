import os, base64, re
import pandas as pd
from datetime import datetime, timedelta
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
from azure.devops.v7_1.git.models import GitQueryCommitsCriteria, GitVersionDescriptor, GitPullRequestSearchCriteria, GitPullRequestQuery, GitPullRequestQueryInput
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

def extract_original_pr_id(text):
    """Looks for 'cherry picked from !1234' in commit message."""
    if not text: return None
    match = re.search(r'cherry[- ]picked from !(\d+)', text, re.IGNORECASE)
    if match:
        res = match.group(1)
        # print(f"DEBUG: Found PR link !{res} in text: {text[:50]}...")
        return res
    return None

def extract_original_commit_id(text):
    """Looks for 'cherry-picked from commit a8feecf1' in commit message."""
    if not text: return None
    match = re.search(r'cherry[- ]picked from commit `?([a-f0-9]{7,40})`?', text, re.IGNORECASE)
    if match:
        res = match.group(1)
        # print(f"DEBUG: Found Commit link {res} in text: {text[:50]}...")
        return res
    return None

def get_pr_ids_for_commits(git_client, repo_id, project, commit_ids):
    """Batch queries Azure DevOps for PRs associated with commit IDs."""
    results = {}
    commit_ids = list(set(commit_ids))
    total_found = 0
    # Process in chunks of 10 (Azure DevOps API limit)
    for i in range(0, len(commit_ids), 10):
        chunk = commit_ids[i:i+10]
        try:
            query = GitPullRequestQuery(
                queries=[GitPullRequestQueryInput(items=[cid], type='commit') for cid in chunk]
            )
            resp = git_client.get_pull_request_query(query, repo_id, project=project)
            if resp and resp.results:
                for mapping in resp.results:
                    for cid, prs in mapping.items():
                        if prs:
                            results[cid] = {str(pr.pull_request_id) for pr in prs}
                            total_found += 1
        except Exception as e:
            pass # Silent fail for individual chunks
    print(f"  PR Context: Found PRs for {total_found} out of {len(commit_ids)} commits.")
    return results

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

def get_commit_files(git_client, repo_id, commit_id, project):
    """Fetches the list of file paths changed in a commit."""
    try:
        changes = git_client.get_changes(commit_id, repo_id, project=project)
        return {c.item.path for c in changes.changes if c.item and c.item.path}
    except:
        return set()

def main():
    # === AUTHENTICATION ===
    credentials = BasicAuthentication('', config.personal_access_token)
    connection = Connection(base_url=config.organization_url, creds=credentials)
    git_client = connection.clients.get_git_client()
    repo = git_client.get_repository(project=config.project_name, repository_id=config.repository_name)
    
    start_dt = datetime.strptime(config.start_date, '%Y-%m-%d %H:%M')

    # === 1. SCAN RELEASE HISTORIES (Building Inventories) ===
    release_global_inventory = {} 
    release_jira_inventory = {}
    release_linked_prs = set()
    release_linked_commits = set()
    all_release_cleaned_subjects = set()

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
                print(f"  Processing {len(commits)} commits in {branch_name} (fetching full details)...")
                for c in commits:
                    try:
                        # Always fetch full details to ensure we get the complete message body/comment
                        full_c = git_client.get_commit(c.commit_id, repo.id, project=config.project_name)
                        msg = full_c.comment
                    except:
                        msg = c.comment # Fallback if full fetch fails

                    subj = get_first_line(msg)
                    cleaned = clean_subject(subj)
                    author = get_matched_config_author(c.author.name, config.authors)
                    jira = extract_jira_id(msg)

                    # High-fidelity link extraction
                    linked_pr = extract_original_pr_id(msg)
                    if linked_pr: release_linked_prs.add(linked_pr)
                    
                    linked_commit = extract_original_commit_id(msg)
                    if linked_commit: release_linked_commits.add(linked_commit)

                    if cleaned:
                        all_release_cleaned_subjects.add(cleaned)
                        if author:
                            key = (author, cleaned)
                            b_global[key] = b_global.get(key, 0) + 1
                        if jira:
                            if jira not in b_jira: b_jira[jira] = {}
                            b_jira[jira][cleaned] = b_jira[jira].get(cleaned, 0) + 1
            
            for k, v in b_global.items():
                release_global_inventory[k] = max(release_global_inventory.get(k, 0), v)
            for jid, sub_counts in b_jira.items():
                if jid not in release_jira_inventory: release_jira_inventory[jid] = {}
                for s, v in sub_counts.items():
                    release_jira_inventory[jid][s] = max(release_jira_inventory[jid].get(s, 0), v)
        except Exception as e:
            print(f"Warning: Could not scan {branch_name}: {e}")

    print(f"Found {len(release_linked_prs)} unique PR links and {len(release_linked_commits)} commit links in release history.")
    initial_global_inventory = release_global_inventory.copy()
    initial_jira_inventory = {jid: counts.copy() for jid, counts in release_jira_inventory.items()}

    # === 2. SCAN DEVELOP (Phase A: Collection & Dependency Analysis) ===
    print(f"Scanning history of '{config.main_target_branch}'...")
    dev_ver = GitVersionDescriptor(version=config.main_target_branch, version_type='branch')
    dev_crit = GitQueryCommitsCriteria(item_version=dev_ver)

    team_commits = []
    foreign_commits = []
    team_files = set() # Set of all files touched by the team

    skip = 0
    top = 100
    while skip < 1500: # Slightly smaller window for performance
        history_commits = git_client.get_commits(repo.id, search_criteria=dev_crit, project=config.project_name, top=top, skip=skip)
        if not history_commits: break
            
        for commit in history_commits:
            commit_date = commit.committer.date.replace(tzinfo=None)
            if commit_date < start_dt:
                if skip > 300: skip = 1500; break
                continue

            msg = commit.comment.strip()
            subject = get_first_line(msg)
            
            # Skip structural PR merge records
            if subject.startswith("Merged PR"):
                full = git_client.get_commit(commit.commit_id, repo.id, project=config.project_name)
                if full.parents and len(full.parents) > 1: continue

            matched_author = get_matched_config_author(commit.author.name, config.authors)
            
            if matched_author:
                # print(f"  Team commit: {commit.commit_id[:8]} by {matched_author}")
                # Fetch files to build dependency map
                files = get_commit_files(git_client, repo.id, commit.commit_id, config.project_name)
                team_files.update(files)
                
                team_commits.append({
                    'commit': commit,
                    'author': matched_author,
                    'subject': subject,
                    'cleaned': clean_subject(subject),
                    'jira': extract_jira_id(msg),
                    'date': commit_date,
                    'full_msg': msg,
                    'files': files,
                    'type': 'Team'
                })
            else:
                foreign_commits.append(commit)

        skip += top

    # === 3. FILTER FOREIGN COMMITS FOR OVERLAPS ===
    print(f"Checking {len(foreign_commits)} foreign commits for file overlaps...")
    overlapping_foreign = []
    for c in foreign_commits:
        f_files = get_commit_files(git_client, repo.id, c.commit_id, config.project_name)
        overlaps = f_files.intersection(team_files)
        if overlaps:
            print(f"  🔗 Overlap found: {c.commit_id[:8]} by {c.author.name} touches team files!")
            msg = c.comment.strip()
            overlapping_foreign.append({
                'commit': c,
                'author': f"FOREIGN: {c.author.name}",
                'subject': get_first_line(msg),
                'cleaned': clean_subject(get_first_line(msg)),
                'jira': extract_jira_id(msg),
                'date': c.committer.date.replace(tzinfo=None),
                'full_msg': msg,
                'type': 'Dependency',
                'overlap_files': list(overlaps)[:3] # Show first few overlaps
            })

    # Combine and sort
    all_develop_work = team_commits + overlapping_foreign
    all_develop_work.sort(key=lambda x: x['date'])

    # Recalculate develop counts for combined list
    dev_global_counts = {}
    dev_jira_counts = {}
    all_dev_commit_ids = [item['commit'].commit_id for item in all_develop_work]
    
    print(f"Fetching PR context for {len(all_dev_commit_ids)} develop commits...")
    commit_to_pr_map = get_pr_ids_for_commits(git_client, repo.id, config.project_name, all_dev_commit_ids)

    for item in all_develop_work:
        if item['type'] == 'Team':
            g_key = (item['author'], item['cleaned'])
            dev_global_counts[g_key] = dev_global_counts.get(g_key, 0) + 1
            if item['jira']:
                jid = item['jira']
                if jid not in dev_jira_counts: dev_jira_counts[jid] = {}
                dev_jira_counts[jid][item['cleaned']] = dev_jira_counts[jid].get(item['cleaned'], 0) + 1

    # === 4. DECISION LOGIC ===
    print(f"Analyzing {len(all_develop_work)} total relevant commits...")
    all_final_rows = []
    
    match_counts = {"PR Link": 0, "Commit Link": 0, "Exact": 0, "Ticket": 0, "No": 0}

    for item in all_develop_work:
        is_in_release = "No"
        author = item['author']
        cleaned = item['cleaned']
        jira = item['jira']
        commit_id = item['commit'].commit_id
        
        if item['type'] == 'Dependency':
            is_in_release = f"Foreign commit touching {item['overlap_files'][0]}"
            decision = "" # Leave blank for review
        else:
            # 1. High-fidelity Link Matching (Highest Confidence)
            # Check for direct commit ID match (short or long)
            matched_linked_commit = next((c for c in release_linked_commits if commit_id.startswith(c) or c.startswith(commit_id)), None)
            
            # Check for PR ID match
            my_prs = commit_to_pr_map.get(commit_id, set())
            matched_linked_pr = next((p for p in my_prs if p in release_linked_prs), None)

            if matched_linked_commit:
                is_in_release = f"Yes (Commit Link Match: {matched_linked_commit[:8]})"
                match_counts["Commit Link"] += 1
            elif matched_linked_pr:
                is_in_release = "Yes (PR Link Match)"
                match_counts["PR Link"] += 1
            else:
                # 2. Standard Heuristic Rules
                g_key = (author, cleaned)
                if g_key in release_global_inventory:
                    if dev_global_counts[g_key] == release_global_inventory[g_key]:
                        is_in_release = "Yes (Exact Match)"
                        match_counts["Exact"] += 1
                    else: is_in_release = "Needs attention (Subject Count Mismatch)"
                elif jira and jira in release_jira_inventory:
                    if cleaned in release_jira_inventory[jira]:
                        if dev_jira_counts[jira][cleaned] == release_jira_inventory[jira][cleaned]:
                            is_in_release = "Yes (Ticket Match)"
                            match_counts["Ticket"] += 1
                        else: is_in_release = "Needs attention (Ticket Count Mismatch)"
                    else: is_in_release = f"Likely (Jira {jira} exists, but subjects differ)"
                
                if is_in_release == "No":
                    match_counts["No"] += 1
            
            decision = "no" if is_in_release.startswith("Yes") else "yes" if is_in_release == "No" else ""

        jira_link = f'=HYPERLINK("{config.jira_base_url}{jira}","{jira}")' if jira else ""
        
        # Determine PR ID to link (prioritize the one that matched, then the first available)
        my_prs = commit_to_pr_map.get(commit_id, set())
        # We need to re-find matched_linked_pr here or just use the logic from above
        display_pr = next((p for p in my_prs if p in release_linked_prs), (sorted(list(my_prs))[0] if my_prs else None))
        pr_link = f'=HYPERLINK("{config.organization_url}/{config.project_name}/_git/{config.repository_name}/pullrequest/{display_pr}","!{display_pr}")' if display_pr else ""

        all_final_rows.append({
            'Commit ID': item['commit'].commit_id,
            'Author': author,
            'Jira Link': jira_link,
            'PR Link': pr_link,
            'Date': item['date'],
            'Message': item['full_msg'],
            'In Release?': is_in_release,
            'cherry pick?': decision
        })

    print(f"\nDecision Summary:")
    for k, v in match_counts.items():
        print(f"  - {k}: {v}")

    df = pd.DataFrame(all_final_rows)
    try:
        df.to_excel('cherrypick_list.xlsx', index=False, engine='openpyxl')
        print(f"Successfully saved {len(all_final_rows)} commits to cherrypick_list.xlsx")
    except: print(f"❌ Error: Close the file.")

if __name__ == "__main__":
    main()
