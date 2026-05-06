# Cherry-Pick Master Logic Specification

This document defines the complete end-to-end logic for the Cherry-Pick Helper workflow.

---

## 🛠️ Phase 1: Finding & Identifying Work (`pullRequestHelper.py`)

### 1. Data Selection
1.  **Fuzzy Author Match**: A commit is included if any word from `config.authors` (e.g., "LastName") is found anywhere in the repository author's name (e.g., "LastFirstName"), ignoring case.
2.  **Date Filter**: Only commits that **landed** (merged) in `develop` after the `start_date` are included.
3.  **Redundancy Filter**: Standard "Merged PR ####" commits are **skipped** only if they have no unique changes.

### 2. Detection Precedence (If-Else)
For each commit, the script checks these rules in order. **The first match determines the result.**

1.  **Exact Inventory Match**: If the exact (Author, Subject) pair exists in the release branches ➔ **`In Release: Yes (Exact Match)`** (and 1 count is consumed from inventory).
2.  **Inventory Depleted**: If the (Author, Subject) was in the inventory but all counts are consumed ➔ **`In Release: Needs attention (Count Mismatch)`**.
3.  **Jira Ticket Content Match**: If the Jira ID exists in release and the cleaned subject matches ➔ **`In Release: Yes (Ticket Match)`**.
4.  **Jira ID Match**: If the Jira ID exists in release but the subject is unknown ➔ **`In Release: Likely`**.
5.  **Default**: If none of the above ➔ **`In Release: No`**.

### 3. Baseline Decision
- If `Yes` ➔ `cherry pick?` = **`no`**.
- If `No` ➔ `cherry pick?` = **`yes`**.
- If `Likely` or `Needs attention` ➔ `cherry pick?` = **`(blank)`**.

---

## ⚖️ Phase 2: Jira Validation (`jiraHelper.py`)

Overwrites baseline decisions using official Jira metadata. **Rules are checked in order.**

### Order of Precedence (If-Else)
1.  **Physical Safety**: If Git Status is `Yes`, decision is always **`no`** (cannot cherry-pick what is already there).
2.  **Ambiguity Safety**: If Git Status is `Likely` or `Needs attention`, decision is always **`(blank)`** (requires human review).
3.  **Old Release**: If Fix Version matches `jira_branched_from_version` ➔ **`no`**.
4.  **Target Release**: If Fix Version matches `jira_target_version` ➔ **`yes`**.
5.  **Mandatory Hotfix**: If Fix Version matches any in `jira_hotfix_versions` ➔ **`yes`**.
6.  **Future Release**: If Fix Version matches any other version ➔ **`no`**.
7.  **Missing Data**: If no Fix Version found ➔ **No Change** (keep Baseline Decision).

---

## 🚀 Phase 3: Execution (`cherryPickHelper.py`)

1.  **Fresh Start**: Deletes local `target_branch` and recreates it from a freshly updated `cherry_pick_base_branch`.
2.  **Strict Selection**: Processes only rows marked **`yes`**.
3.  **Unattended Mode**: If a conflict occurs, the script **automatically aborts** that commit and moves to the next.
4.  **Reporting**: Updates the **`success`** column while preserving clickable Jira links.
