# Cherry-Pick Master Logic Specification

This document defines the complete end-to-end logic for the Cherry-Pick Helper workflow.

---

## 🛠️ Phase 1: Finding & Identifying Work (`pullRequestHelper.py`)

1.  **Fuzzy Author Match**: A commit is included if any word from `config.authors` (e.g., "LastName") is found anywhere in the repository author's name (e.g., "LastFirstName"), ignoring case.
2.  **Date Filter**: Only commits that **landed** (merged) in `develop` after the `start_date` are included.
3.  **Redundancy Filter**: Standard "Merged PR ####" commits are **skipped** if they have more than one parent.
4.  **Baseline Detection**:
    *   If the **Subject** is physically found in the release branches ➔ **`In Release: Yes (Exact Match)`**.
    *   If the **Jira ID** is in release and the **Cleaned Subject** matches ➔ **`In Release: Yes (Ticket Match)`**.
    *   If the **Jira ID** is in release but the work looks new ➔ **`In Release: Likely`**.
    *   Otherwise ➔ **`In Release: No`**.
5.  **Baseline Decision**:
    *   If `Yes` ➔ `cherry pick?` = **`no`**.
    *   If `No` ➔ `cherry pick?` = **`yes`**.
    *   If `Likely` ➔ `cherry pick?` = **`(blank)`**.

---

## ⚖️ Phase 2: Jira Validation (`jiraHelper.py`)

Overwrites baseline decisions using official Jira metadata.

| If Jira Fix Version... | New Decision (`cherry pick?`) | Rationale |
| :--- | :--- | :--- |
| **Matches `jira_branched_from_version`** | **no** | **No cherry pick.** Already in the old release. |
| **Matches `jira_target_version`** | **yes** | **Cherry pick.** Explicitly assigned to this target. |
| **Matches any in `jira_hotfix_versions`** | **yes** | **Cherry pick.** Mandatory hotfix target. |
| **Is an "Other" version** | **no** | Assigned to a future release. |
| **Is Missing (Empty)** | *No Change* | Keep the baseline from Phase 1. |

**Safety Overrides:**
- If Git Status is `Yes`, the decision is always **`no`** (physical presence over Jira data).
- If Git Status is `Likely`, the decision stays **`blank`** (forces human review).

---

## 🚀 Phase 3: Execution (`cherryPickHelper.py`)

1.  **Fresh Start**: Deletes local `target_branch` and recreates it from a freshly updated `cherry_pick_base_branch`.
2.  **Strict Selection**: Processes only rows marked **`yes`**.
3.  **Unattended Mode**: If a conflict occurs, the script **automatically aborts** that commit and moves to the next.
4.  **Reporting**: Updates the **`success`** column while preserving clickable Jira links.
