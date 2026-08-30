"""
Release Audit — the reverse of the cherry-pick workflow.

pullRequestHelper.py asks "what work is MISSING from the release branch?".
This asks the opposite: "what work is PRESENT in the release branch that was
never intended for it?" — the failure mode when you branch late and the release
inherits whatever was sitting on develop at branch point.

Read-only. Runs `git log` and Jira GETs. Touches no git state and does not
modify cherrypick_list.xlsx.

Exit codes (so this can gate a pipeline):
    0  no strays found
    1  could not complete — bad config, git/Jira failure, empty range, unsaved report
    2  strays found
"""

import os, re, sys, base64
from git import Repo
from jira import JIRA
import config
import shared

OUTPUT_FILE = 'release_audit.xlsx'

# Record/unit separators so multi-line commit bodies parse unambiguously.
_REC = '\x1e'
_UNIT = '\x1f'

VERDICT_STRAY = 'STRAY'
VERDICT_REVIEW = 'REVIEW'
VERDICT_OK = 'OK'

# Sort order for the report: findings first, unknowns next, clean last.
_SORT_RANK = {VERDICT_STRAY: 0, VERDICT_REVIEW: 1, VERDICT_OK: 2}

# Exit codes are distinct so this can gate a release pipeline. "Found nothing"
# is an error, not a pass: an empty range almost always means a misconfigured
# branch name, and reporting that as success would be a false all-clear.
EXIT_CLEAN = 0
EXIT_ERROR = 1
EXIT_STRAYS_FOUND = 2


# Mainline releases are named "IntelliVIEW YY.RR.PP[letter]", sometimes with a
# trailing qualifier like " (Steel Only)". Older tickets use a bare "YY.RR.PP",
# so the product word is optional. Sibling product lines put a word where the
# digits go ("IntelliVIEW Cloud 25.01.00", "eShop Manager 10.01") and fail this
# pattern by design — they are not comparable to the mainline.
_version_re_cache = {}


def _version_re():
    """
    Builds the mainline-version pattern from the configured target version, so
    the product name is not hardcoded here.
    """
    target = getattr(config, 'audit_target_version', '')
    if target not in _version_re_cache:
        # Leading alphabetic words of the target, e.g. "IntelliVIEW 26.02.00" -> "IntelliVIEW".
        m = re.match(r'^\s*([A-Za-z][A-Za-z ]*?)\s+\d', target)
        product = re.escape(m.group(1).strip()) if m else r'[A-Za-z]+'
        _version_re_cache[target] = re.compile(
            r'^\s*(?:' + product + r'\s+)?(\d+)\.(\d+)\.(\d+)\s*([a-z])?\b',
            re.IGNORECASE)
    return _version_re_cache[target]


def parse_version(name):
    """
    Returns (year, release, patch) for a mainline IntelliVIEW version, else None.

    The trailing hotfix letter is deliberately dropped: 26.02.00A is a patch of
    the release under audit, so it belongs in that branch just as 26.02.00 does.
    """
    if not name:
        return None
    m = _version_re().match(name)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def require_config():
    """Verify the audit_* keys exist before doing any work."""
    required = [
        'audit_release_branch',
        'audit_previous_release_branch',
        'audit_target_version',
    ]
    missing = [k for k in required if not hasattr(config, k)]
    if missing:
        print("❌ Error: config.py is missing required keys for this script:")
        for k in missing:
            print(f"     - {k}")
        print("\n   See config.py.example for the block to copy in.")
        return False
    return True


def jira_projects():
    """
    Jira project keys to recognize in commit messages.

    Kept to an explicit allow-list rather than a generic ABC-123 pattern: this
    repo's messages are full of domain identifiers that look like ticket refs but
    are not (EC5-, TPI-, EP-), and matching those invents tickets that don't exist.
    """
    return [p.upper() for p in getattr(config, 'audit_jira_projects', ['ALP'])]


def extract_jira_ids(message):
    """Returns every distinct ticket referenced in a commit message."""
    if not message:
        return []
    pattern = r'\b(' + '|'.join(re.escape(p) for p in jira_projects()) + r')[-_]?(\d+)'
    seen, ids = set(), []
    for prefix, num in re.findall(pattern, message, re.IGNORECASE):
        jid = f"{prefix.upper()}-{num}"
        if jid not in seen:
            seen.add(jid)
            ids.append(jid)
    return ids


def merge_label(subject):
    """'Merged PR 47009: ALP-86315 ...' -> 'PR 47009', for provenance in the report."""
    m = re.search(r'Merged PR (\d+)', subject or '', re.IGNORECASE)
    return f'PR {m.group(1)}' if m else 'a merge commit'


def _parse_log(raw, fields):
    """Splits a %x1e/%x1f formatted git log into dicts."""
    out = []
    for record in raw.split(_REC):
        record = record.strip('\n')
        if not record.strip():
            continue
        parts = record.split(_UNIT)
        if len(parts) < len(fields):
            continue
        out.append(dict(zip(fields, parts)))
    return out


def fetch_commits(git, previous_branch, release_branch):
    """Commits in release_branch that are not in previous_branch."""
    fmt = _UNIT.join(['%H', '%an', '%ad', '%s', '%B']) + _REC
    raw = git.execute([
        'git', 'log', '--no-merges', '--date=short', f'--format={fmt}',
        f'origin/{previous_branch}..origin/{release_branch}',
    ])
    return _parse_log(raw, ['sha', 'author', 'date', 'subject', 'body'])


def build_attribution(git, previous_branch, release_branch):
    """
    Maps commit SHA -> (jira_ids, merge_subject) for commits that carry no Jira
    ID of their own.

    Most commits without an ID are intermediate work inside a PR; the "Merged PR
    NNNN: ALP-XXXXX" merge commit above them holds the ticket. We walk each merge
    commit's topic-branch side and attribute its ticket to everything it
    introduced, so those commits can be classified instead of landing in REVIEW.
    """
    rng = f'origin/{previous_branch}..origin/{release_branch}'

    # Commits on the branch's own first-parent trunk. Anything else arrived via
    # a topic branch and is a candidate for inheriting its merge's ticket.
    trunk = set(git.execute(
        ['git', 'rev-list', '--first-parent', rng]
    ).split())

    fmt = _UNIT.join(['%H', '%P', '%s', '%B']) + _REC
    raw = git.execute(['git', 'log', '--merges', f'--format={fmt}', rng])
    merges = _parse_log(raw, ['sha', 'parents', 'subject', 'body'])

    # Parent lookup for the BFS, across the whole range including merges.
    fmt2 = _UNIT.join(['%H', '%P']) + _REC
    raw2 = git.execute(['git', 'log', f'--format={fmt2}', rng])
    parents_of = {r['sha']: r['parents'].split()
                  for r in _parse_log(raw2, ['sha', 'parents'])}

    attribution = {}
    for m in merges:
        ids = extract_jira_ids(m['subject'] + '\n' + m['body'])
        if not ids:
            continue

        # Walk the merged-in side(s), stopping at trunk commits and at anything
        # already attributed by a more recent merge.
        stack = [p for p in m['parents'].split()[1:]]
        while stack:
            sha = stack.pop()
            if sha in trunk or sha in attribution or sha not in parents_of:
                continue
            attribution[sha] = (ids, m['subject'])
            stack.extend(parents_of.get(sha, []))

    return attribution


def fetch_fix_versions(jira, ticket_ids, batch_size=50):
    """
    Returns {ticket_id: (versions, had_error)} for every id, using batched JQL.

    pullRequestHelper.py already batches its Azure DevOps calls; this keeps the
    same convention rather than issuing one request per ticket.

    A single malformed or non-existent key makes Jira reject the whole JQL query,
    and this repo's commit messages do contain typo'd ticket numbers — so any
    failed batch falls back to individual lookups instead of losing 50 tickets.
    """
    def one(tid):
        try:
            issue = jira.issue(tid, fields='fixVersions')
            return ([v.name for v in issue.fields.fixVersions], False)
        except Exception:
            return ([], True)

    result = {}
    ids = sorted(set(ticket_ids))
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        try:
            issues = jira.search_issues(
                'key in (%s)' % ', '.join(chunk),
                fields='fixVersions', maxResults=len(chunk))
            found = {i.key.upper(): ([v.name for v in i.fields.fixVersions], False)
                     for i in issues}
            # Present with no fixVersions is a real answer ("no Fix Version").
            # Absent means the key does not exist or is not visible, which is an
            # error — those must not be mistaken for "no Fix Version".
            for tid in chunk:
                result[tid] = found.get(tid.upper(), ([], True))
        except Exception:
            for tid in chunk:
                result[tid] = one(tid)
        print(f"  ...{min(start + batch_size, len(ids))}/{len(ids)} tickets fetched")
    return result


def classify(versions, jira_ids, had_lookup_error, inherited_from=None):
    """
    Decide whether a commit belongs in this release.

    Deliberately inverted from jiraHelper.get_jira_decision: there, a missing
    Fix Version is safe to treat as 'no'. Here, missing means we cannot confirm
    the commit belongs, so it must surface for human review rather than pass.

    had_lookup_error is handled per-direction rather than up front: an incomplete
    picture must not hide a stray we did confirm, but it also must not clear a
    commit we only partially verified.
    """
    suffix = f' (ticket inherited from {inherited_from})' if inherited_from else ''

    if not jira_ids:
        return VERDICT_REVIEW, 'No Jira ID on commit or its merge — cannot classify'

    target = parse_version(config.audit_target_version)
    if target is None:
        return VERDICT_REVIEW, 'config.audit_target_version is not a parseable version'

    # Explicit escape hatch for oddly-named versions that should count as belonging.
    extra = {v.strip().lower()
             for v in getattr(config, 'audit_extra_expected_versions', [])}
    if any(v.strip().lower() in extra for v in versions):
        return VERDICT_OK, f'Listed in audit_extra_expected_versions{suffix}'

    parsed = [(v, parse_version(v)) for v in versions]
    mainline = [(v, p) for v, p in parsed if p is not None]

    if mainline:
        # A ticket can carry several versions. If any of them justifies the
        # commit being here, the commit is fine.
        is_target = any(p == target for _, p in mainline)
        is_prior = any(p < target for _, p in mainline)

        if not (is_target or is_prior):
            # Every version we resolved is later than this release. That is a
            # confirmed finding, so report it even if a sibling ticket failed to
            # resolve — a partial failure must not mask a stray.
            future = min(p for _, p in mainline)
            label = '.'.join(f'{n:02d}' for n in future)
            return VERDICT_STRAY, f'Targeted at {label}, a LATER release{suffix}'

        if had_lookup_error:
            # What resolved would clear this commit, but not everything resolved.
            # Clearing on incomplete data is the one mistake this tool must not make.
            return VERDICT_REVIEW, (f'Only partly verified — a linked ticket '
                                    f'failed to resolve{suffix}')

        if is_target:
            return VERDICT_OK, f'Targeted at this release{suffix}'
        return VERDICT_OK, f'Earlier release — landed late, fine to be present{suffix}'

    # Nothing usable came back.
    if had_lookup_error:
        return VERDICT_REVIEW, f'Jira lookup failed — could not verify{suffix}'
    if not versions:
        return VERDICT_REVIEW, f'Ticket has no Fix Version set in Jira{suffix}'
    others = ', '.join(v for v, _ in parsed)
    return VERDICT_REVIEW, f'Not a mainline version ({others}){suffix}'


def main():
    if not require_config():
        return EXIT_ERROR

    if not os.path.exists(config.local_repo_path):
        print(f"❌ Error: Repository path {config.local_repo_path} not found.")
        return EXIT_ERROR

    # Only your developers' work matters here, so the author filter is the
    # default; --all opts out of it.
    mine_only = '--all' not in sys.argv
    dry_run = '--dry-run' in sys.argv

    release_branch = config.audit_release_branch
    previous_branch = config.audit_previous_release_branch

    print("--- Release Audit: finding work that should not be here ---")
    print(f"Auditing:          {release_branch}")
    print(f"Against previous:  {previous_branch}")
    print(f"Target version:    {config.audit_target_version}")
    if mine_only:
        print(f"Author filter:     config.authors ({len(config.authors)} devs) — "
              f"pass --all to audit everyone")
    else:
        print("Author filter:     NONE (--all)")
    if dry_run:
        print("Mode:              DRY RUN (no Jira calls, coverage check only)")
    print("-" * 58)

    repo = Repo(config.local_repo_path)
    git = repo.git

    _pat_b64 = base64.b64encode(f':{config.personal_access_token}'.encode()).decode()
    _auth_header = f'http.extraheader=AUTHORIZATION: Basic {_pat_b64}'

    print("Fetching latest changes from remote...")
    try:
        git.execute(['git', 'remote', 'prune', 'origin'])
    except Exception:
        pass
    try:
        git.execute(['git', '-c', _auth_header, 'fetch', 'origin'])
    except Exception as e:
        print(f"❌ Error fetching from origin: {e}")
        return EXIT_ERROR

    print(f"Enumerating commits in {release_branch} but not {previous_branch}...")
    try:
        commits = fetch_commits(git, previous_branch, release_branch)
    except Exception as e:
        print(f"❌ Error running git log: {e}")
        print("   Check that both branch names exist on origin.")
        return EXIT_ERROR

    if not commits:
        print("\n⚠️  No commits found in that range. Either the branches are "
              "identical or a branch name is wrong — not a clean bill of health, "
              "so this exits non-zero rather than reporting a pass.")
        return EXIT_ERROR

    print(f"Found {len(commits)} commits.")

    print("Mapping commits to their merge PRs for ticket attribution...")
    try:
        # Deliberately not reporting len(attribution) here: it covers the whole
        # range including nested merge commits, so it overstates by ~10x what
        # actually gets used. The real figure is counted after resolution below.
        attribution = build_attribution(git, previous_branch, release_branch)
    except Exception as e:
        print(f"⚠️  Attribution pass failed ({e}) — commits without their own "
              f"Jira ID will fall to REVIEW.")
        attribution = {}

    skipped_foreign = 0
    if mine_only:
        kept = []
        for c in commits:
            if shared.get_matched_config_author(c['author'], config.authors):
                kept.append(c)
            else:
                skipped_foreign += 1
        commits = kept
        print(f"Filtered to {len(commits)} commits by configured authors "
              f"({skipped_foreign} skipped).")

    # Resolve each commit's ticket(s): its own, else its merge PR's. 'inherited'
    # holds the source label when borrowed, and is empty when the commit had its
    # own ID — so it doubles as the "was this inherited" flag.
    for c in commits:
        own = extract_jira_ids(c['body'])
        if own:
            c['jira_ids'], c['inherited'] = own, ''
        elif c['sha'] in attribution:
            ids, merge_subject = attribution[c['sha']]
            c['jira_ids'], c['inherited'] = ids, merge_label(merge_subject)
        else:
            c['jira_ids'], c['inherited'] = [], ''

    unresolved = sum(1 for c in commits if not c['jira_ids'])
    inherited = sum(1 for c in commits if c['inherited'])
    pct = 100 * (len(commits) - unresolved) // max(len(commits), 1)
    print(f"Ticket resolution: {pct}% of commits have a ticket "
          f"({inherited} inherited from a merge PR, {unresolved} unresolved).")

    if dry_run:
        print("\n--- DRY RUN: stopping before Jira. No report written. ---")
        if unresolved:
            print(f"\n{unresolved} commit(s) would land in REVIEW with no ticket:")
            for c in [c for c in commits if not c['jira_ids']][:15]:
                print(f"   {c['sha'][:8]}  {c['subject'][:66]}")
            if unresolved > 15:
                print(f"   ... and {unresolved - 15} more")
        # A dry run makes no claim about strays, so it cannot signal EXIT_CLEAN.
        return EXIT_ERROR if unresolved == len(commits) else EXIT_CLEAN

    # === JIRA ===
    jira_server = config.jira_base_url.split('/browse/')[0]
    print(f"Connecting to Jira at {jira_server}...")
    try:
        jira = JIRA(server=jira_server,
                    basic_auth=(config.jira_email, config.jira_api_token))
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return EXIT_ERROR

    all_ids = {jid for c in commits for jid in c['jira_ids']}
    print(f"Fetching Fix Versions for {len(all_ids)} unique tickets...")
    cache = fetch_fix_versions(jira, all_ids)

    rows = []
    for c in commits:
        jira_ids = c['jira_ids']
        versions, had_error = [], False
        for jid in jira_ids:
            v, err = cache.get(jid, ([], True))
            versions.extend(v)
            had_error = had_error or err

        # Deduplicate while preserving order.
        versions = list(dict.fromkeys(versions))

        verdict, note = classify(versions, jira_ids, had_error, c['inherited'])
        rows.append({
            'verdict': verdict,
            'note': note,
            'jira_id': jira_ids[0] if jira_ids else '',
            'jira_url': (config.jira_base_url + jira_ids[0]) if jira_ids else '',
            'all_ids': ', '.join(jira_ids),
            'versions': ', '.join(versions) if versions else '(none)',
            'subject': c['subject'],
            'author': c['author'],
            'date': c['date'],
            'sha': c['sha'],
        })

    rows.sort(key=lambda r: (_SORT_RANK[r['verdict']], r['date']))
    saved = write_report(rows)
    print_summary(rows, skipped_foreign if mine_only else 0)

    if not saved:
        return EXIT_ERROR
    if any(r['verdict'] == VERDICT_STRAY for r in rows):
        return EXIT_STRAYS_FOUND
    return EXIT_CLEAN


def write_report(rows):
    """Writes the report. Returns True on success, False if it could not be saved."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Release Audit"

    headers = [
        'Verdict', 'Why', 'Jira ID', 'Fix Version(s)', 'Description',
        'Owner', 'Date', 'All Tickets', 'Commit SHA',
    ]
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col).value = header
        ws.cell(row=1, column=col).font = Font(bold=True)

    for row_idx, r in enumerate(rows, 2):
        ws.cell(row=row_idx, column=1).value = r['verdict']
        if r['verdict'] == VERDICT_STRAY:
            ws.cell(row=row_idx, column=1).font = Font(color="C00000", bold=True)
        elif r['verdict'] == VERDICT_REVIEW:
            ws.cell(row=row_idx, column=1).font = Font(color="BF8F00", bold=True)

        ws.cell(row=row_idx, column=2).value = r['note']

        c3 = ws.cell(row=row_idx, column=3)
        c3.value = r['jira_id']
        if r['jira_url']:
            c3.hyperlink = r['jira_url']
            c3.font = Font(color="0000FF", underline="single")

        ws.cell(row=row_idx, column=4).value = r['versions']
        ws.cell(row=row_idx, column=5).value = r['subject']
        ws.cell(row=row_idx, column=6).value = r['author']
        ws.cell(row=row_idx, column=7).value = r['date']
        ws.cell(row=row_idx, column=8).value = r['all_ids']
        ws.cell(row=row_idx, column=9).value = r['sha']

    widths = {'A': 10, 'B': 42, 'C': 13, 'D': 30, 'E': 60,
              'F': 24, 'G': 12, 'H': 20, 'I': 42}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = 'A2'

    try:
        wb.save(OUTPUT_FILE)
        print(f"✅ Wrote {OUTPUT_FILE}")
        return True
    except PermissionError:
        print(f"❌ Could not save {OUTPUT_FILE}. Please close the file and re-run.")
        return False


def print_summary(rows, skipped_foreign):
    strays = [r for r in rows if r['verdict'] == VERDICT_STRAY]
    reviews = [r for r in rows if r['verdict'] == VERDICT_REVIEW]
    oks = [r for r in rows if r['verdict'] == VERDICT_OK]

    print("\n" + "=" * 58)
    print("RELEASE AUDIT SUMMARY")
    print("=" * 58)
    print(f"  🔴 STRAY  (wrong release):     {len(strays)}")
    print(f"  🟡 REVIEW (cannot classify):   {len(reviews)}")
    print(f"  ✅ OK     (belongs here):      {len(oks)}")
    print(f"     Total commits audited:      {len(rows)}")
    if skipped_foreign:
        print(f"     Skipped (other authors):    {skipped_foreign}")

    if strays:
        print("\n  Strays by Fix Version:")
        by_version = {}
        for r in strays:
            by_version.setdefault(r['versions'], []).append(r)
        for version, group in sorted(by_version.items(), key=lambda kv: -len(kv[1])):
            print(f"    - {version}: {len(group)} commit(s)")

    if reviews:
        print(f"\n  ⚠️  {len(reviews)} commit(s) could not be classified. These are "
              f"NOT confirmed clean —\n      they are unknowns and need eyes.")

    print("\n  Note: a STRAY is a review candidate, not a revert order. Future-version\n"
          "  work may legitimately be present if this release depended on it.")
    print("=" * 58)


if __name__ == "__main__":
    sys.exit(main())
