"""
Tests for releaseAudit.py classification logic.

Run with:  py test_releaseAudit.py

Deliberately dependency-free (no pytest) and network-free. Everything covered
here is a pure function, which is where all the misclassification risk lives:
a wrong verdict is the failure mode that matters, because a false OK reads as a
clean bill of health.

Version names below are real values from the ALP project, including the messy
ones — inconsistent casing, bare numerics, variant suffixes, and sibling product
lines that must NOT be compared against the mainline.
"""

import releaseAudit as ra

TARGET = 'IntelliVIEW 26.02.00'

_failures = []
_passes = 0


def check(label, actual, expected):
    global _passes
    if actual == expected:
        _passes += 1
    else:
        _failures.append(f"{label}\n      expected: {expected!r}\n      actual:   {actual!r}")


def verdict_of(versions, ids=('ALP-1',), err=False, inherited=None):
    return ra.classify(list(versions), list(ids), err, inherited)[0]


# --- parse_version ------------------------------------------------------------

def test_parse_version():
    mainline = {
        'IntelliVIEW 26.02.00': (26, 2, 0),
        'IntelliVIEW 26.02.00A': (26, 2, 0),   # hotfix letter dropped by design
        'IntelliVIEW 26.03.00': (26, 3, 0),
        'IntelliVIEW 27.01.00': (27, 1, 0),
        'IntelliView 26.01.00B': (26, 1, 0),   # lowercase 'v' really occurs
        'IntelliVIEW 25.01.01 (Steel Only)': (25, 1, 1),
        '18.01.01': (18, 1, 1),                # older tickets omit the product
        '  IntelliVIEW 26.02.00  ': (26, 2, 0),
    }
    for name, expected in mainline.items():
        check(f"parse_version({name!r})", ra.parse_version(name), expected)

    # Sibling product lines and non-versions must not parse, or they would be
    # numerically compared against mainline releases and produce false verdicts.
    for name in ['IntelliVIEW Cloud 25.01.00', 'IntelliVIEW Connect 2.0.0',
                 'eShop Manager 10.01', 'intelliBuild', 'iReview/eOffice',
                 '', None, 'not a version']:
        check(f"parse_version({name!r}) is None", ra.parse_version(name), None)


# --- classify: core verdicts --------------------------------------------------

def test_classify_verdicts():
    check('same release -> OK',
          verdict_of(['IntelliVIEW 26.02.00']), ra.VERDICT_OK)
    check('hotfix of same release -> OK',
          verdict_of(['IntelliVIEW 26.02.00A']), ra.VERDICT_OK)
    check('earlier release -> OK (landed late)',
          verdict_of(['IntelliVIEW 26.01.01']), ra.VERDICT_OK)
    check('much earlier bare version -> OK',
          verdict_of(['18.01.01']), ra.VERDICT_OK)

    check('next patch is a stray',
          verdict_of(['IntelliVIEW 26.02.01']), ra.VERDICT_STRAY)
    check('next release is a stray',
          verdict_of(['IntelliVIEW 26.03.00']), ra.VERDICT_STRAY)
    check('next year is a stray',
          verdict_of(['IntelliVIEW 27.01.00']), ra.VERDICT_STRAY)

    check('no ticket -> REVIEW',
          verdict_of([], ids=[]), ra.VERDICT_REVIEW)
    check('no fix version -> REVIEW',
          verdict_of([]), ra.VERDICT_REVIEW)
    check('non-mainline product -> REVIEW',
          verdict_of(['IntelliVIEW Cloud 25.01.00']), ra.VERDICT_REVIEW)


def test_classify_multiple_versions():
    """A ticket can carry several Fix Versions; any justification clears it."""
    check('future + target -> OK',
          verdict_of(['IntelliVIEW 26.03.00', 'IntelliVIEW 26.02.00']), ra.VERDICT_OK)
    check('future + prior -> OK',
          verdict_of(['IntelliVIEW 26.03.00', 'IntelliVIEW 26.01.00']), ra.VERDICT_OK)
    check('two futures -> STRAY',
          verdict_of(['IntelliVIEW 26.03.00', 'IntelliVIEW 27.01.00']), ra.VERDICT_STRAY)
    check('mainline future + unrelated product -> STRAY',
          verdict_of(['IntelliVIEW 26.03.00', 'eShop Manager 10.01']), ra.VERDICT_STRAY)

    # The label names the earliest future version, since that is when the work
    # is first due to ship.
    note = ra.classify(['IntelliVIEW 27.01.00', 'IntelliVIEW 26.03.00'], ['ALP-1'], False)[1]
    check('stray label names earliest future version', '26.03.00' in note, True)


def test_classify_partial_lookup_failure():
    """
    A failed sibling lookup must not mask a stray, and must not clear a commit.
    These two pull in opposite directions and are the subtlest part of classify.
    """
    check('failed sibling does NOT mask a confirmed stray',
          verdict_of(['IntelliVIEW 26.03.00'], ids=['ALP-1', 'ALP-2'], err=True),
          ra.VERDICT_STRAY)
    check('failed sibling BLOCKS clearing on target version',
          verdict_of(['IntelliVIEW 26.02.00'], ids=['ALP-1', 'ALP-2'], err=True),
          ra.VERDICT_REVIEW)
    check('failed sibling BLOCKS clearing on prior version',
          verdict_of(['IntelliVIEW 26.01.00'], ids=['ALP-1', 'ALP-2'], err=True),
          ra.VERDICT_REVIEW)
    check('total lookup failure -> REVIEW',
          verdict_of([], ids=['ALP-1'], err=True), ra.VERDICT_REVIEW)

    note = ra.classify([], ['ALP-1'], True)[1]
    check('total failure note mentions the lookup', 'lookup failed' in note.lower(), True)


def test_classify_provenance():
    """Inherited tickets are labelled so a reviewer can see where they came from."""
    note = ra.classify(['IntelliVIEW 26.03.00'], ['ALP-1'], False, 'PR 47009')[1]
    check('inherited note names the PR', 'PR 47009' in note, True)
    plain = ra.classify(['IntelliVIEW 26.03.00'], ['ALP-1'], False)[1]
    check('non-inherited note has no provenance suffix', 'inherited' in plain, False)


# --- ticket + PR parsing ------------------------------------------------------

def test_extract_jira_ids():
    check('plain id', ra.extract_jira_ids('ALP-86315: fix'), ['ALP-86315'])
    check('merge subject', ra.extract_jira_ids('Merged PR 47009: ALP-86315 x'), ['ALP-86315'])
    check('underscore separator', ra.extract_jira_ids('ALP_86315'), ['ALP-86315'])
    check('lowercase normalised', ra.extract_jira_ids('alp-86315'), ['ALP-86315'])
    check('deduplicated, order kept',
          ra.extract_jira_ids('ALP-2 then ALP-1 then ALP-2'), ['ALP-2', 'ALP-1'])
    check('second project key recognised',
          ra.extract_jira_ids('UKDOP-5110 pipelines'), ['UKDOP-5110'])
    check('no ticket', ra.extract_jira_ids('Simplify code for L RT'), [])
    check('empty input', ra.extract_jira_ids(''), [])

    # Engineering standards in this repo's messages look like ticket refs but are
    # not real projects; matching them would invent tickets that cannot resolve.
    for text in ['EC5 SpaceJoist design', 'TPI 1-2022 girders', '2022 EP-16 (EP21)']:
        check(f'no false ticket from {text!r}', ra.extract_jira_ids(text), [])


def test_merge_label():
    check('extracts PR number',
          ra.merge_label('Merged PR 47009: ALP-86315 x'), 'PR 47009')
    check('falls back when absent',
          ra.merge_label('Some other merge'), 'a merge commit')
    check('handles None', ra.merge_label(None), 'a merge commit')


def test_exit_codes_distinct():
    codes = {ra.EXIT_CLEAN, ra.EXIT_ERROR, ra.EXIT_STRAYS_FOUND}
    check('exit codes are distinct', len(codes), 3)
    check('clean is 0 (shell success)', ra.EXIT_CLEAN, 0)


def main():
    if ra.parse_version(getattr(ra.config, 'audit_target_version', '')) != (26, 2, 0):
        print(f"⚠️  These tests assume config.audit_target_version parses to "
              f"26.02.00; it is {ra.config.audit_target_version!r}. Skipping.")
        return 1

    for fn in [test_parse_version, test_classify_verdicts,
               test_classify_multiple_versions, test_classify_partial_lookup_failure,
               test_classify_provenance, test_extract_jira_ids, test_merge_label,
               test_exit_codes_distinct]:
        fn()

    print(f"ran {_passes + len(_failures)} checks")
    if _failures:
        print(f"\n❌ {len(_failures)} FAILED:\n")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"✅ all {_passes} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
