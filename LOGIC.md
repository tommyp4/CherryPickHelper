# Cherry-Pick Master Logic Specification

This document defines the complete end-to-end logic for the Cherry-Pick Helper workflow.

---

## 🛠️ Phase 1: Finding & Identifying Work (`pullRequestHelper.py`)

### 1. Data Selection
1.  **Fuzzy Author Match**: A commit is included if any word from `config.authors` (e.g., "LastName") is found anywhere in the repository author's name (e.g., "LastFirstName"), ignoring case.
2.  **Date Filter**: Only commits that **landed** (merged) in `develop` after the `start_date` are included.
3.  **Redundancy Filter**: Standard "Merged PR ####" commits are **skipped** only if they have no unique changes.
4.  **Overlap Detection**: Commits by **foreign authors** (not in config) are included if they touch any file that was also modified by the team in the same timeframe. These are flagged as "Potential Dependencies."

### 2. Detection Precedence (If-Else)
For each commit, the script checks these rules in order. **The first match determines the result.**

1.  **Commit Link Match**: If the commit hash is explicitly mentioned in a release branch PR description (e.g., "Cherry-picked from commit abc1234") ➔ **`In Release: Yes (Commit Link Match: abc1234)`**.
2.  **PR Link Match**: If the commit belongs to a PR that is explicitly mentioned in a release branch PR description (e.g., "Cherry picked from !1234") ➔ **`In Release: Yes (PR Link Match: !1234)`**.
3.  **Exact Global Match**: If the total count of (Author, Subject) pairs matches exactly between branches ➔ **`In Release: Yes (Exact Match)`**.
4.  **Global Count Mismatch**: If the counts for an (Author, Subject) pair do not match ➔ **`In Release: Needs attention (Subject Count Mismatch)`** for all instances.
5.  **Jira Ticket Content Match**: If the Jira ID exists in release and the total count of that specific subject for that ticket matches exactly ➔ **`In Release: Yes (Ticket Match)`**.
6.  **Jira Ticket Count Mismatch**: If the counts for a specific subject within a Jira ticket do not match ➔ **`In Release: Needs attention (Ticket Count Mismatch)`** for all instances.
7.  **Jira ID Match**: If the Jira ID exists in release but the subject is unknown for that ticket ➔ **`In Release: Likely`**.
8.  **Default**: If none of the above ➔ **`In Release: No`**.

### 3. Baseline Decision
- If `Yes` ➔ `cherry pick?` = **`no`**.
- If `No` ➔ `cherry pick?` = **`yes`**.
- If `Likely` or `Needs attention` ➔ `cherry pick?` = **`(blank)`**.

---

## ⚖️ Phase 2: Jira Validation (`jiraHelper.py`)

Overwrites baseline decisions using official Jira metadata. **Rules are checked in order.**

| If Jira Fix Version... | New Decision (`cherry pick?`) | Rationale |
| :--- | :--- | :--- |
| **Matches `jira_branched_from_version`** | **no** | **No cherry pick.** Already in the old release. |
| **Matches `jira_target_version`** | **yes** | **Cherry pick.** Explicitly assigned to this target. |
| **Matches any in `jira_hotfix_versions`** | **yes** | **Cherry pick.** Mandatory hotfix target. |
| **Is an "Other" version** | **no** | Assigned to a future release. |
| **Is Missing (Empty)** | **no** | **No cherry pick.** Requires explicit Fix Version in Jira. |

### Order of Precedence (If-Else)
1.  **Physical Safety**: If Git Status is `Yes`, decision is always **`no`** (cannot cherry-pick what is already there).
2.  **Ambiguity Safety**: If Git Status is `Likely` or `Needs attention`, decision is always **`(blank)`** (requires human review).
3.  **Old Release**: If Fix Version matches `jira_branched_from_version` ➔ **`no`**.
4.  **Target Release**: If Fix Version matches `jira_target_version` ➔ **`yes`**.
5.  **Mandatory Hotfix**: If Fix Version matches any in `jira_hotfix_versions` ➔ **`yes`**.
6.  **Future Release**: If Fix Version matches any other version ➔ **`no`**.
7.  **Missing Data**: If no Fix Version found ➔ **`no`** (overrides Baseline Decision).

---

## 🚀 Phase 3: Execution (`cherryPickHelper.py`)

1.  **Fresh Start**: Deletes local `target_branch` and recreates it from a freshly updated `cherry_pick_base_branch`.
2.  **Strict Selection**: Processes only rows marked **`yes`**.
3.  **Unattended Mode**: If a conflict occurs, the script **automatically aborts** that commit and moves to the next.
4.  **Reporting**: Updates the **`success`** column while preserving clickable Jira links.
5.  **Volatile Decisions**: Decisions made during the startup prompt (for blanks) are **temporary for that run** and are NOT saved back to the Excel file.
