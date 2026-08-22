"""The leak gate has to reach every file the fork publishes, not one directory.

A run on 2026-08-20 printed `15 file(s) scanned, 7 refused literal(s) in force,
0 corpus reference(s)` and exited 0 while two files in this very directory named
a private repository. Nothing was wrong with the matching: `scan_text` would have
caught both. They were never handed to it. `main` defaulted to the package
directory, and `tests/`, `README.md`, `CHANGELOG.md`, `skills/` and `hooks/` --
every one of them published by the same push -- sat outside the walk.

That is the failure this file pins, and the reason it pins SCOPE rather than
matching: a gate that reads the files least likely to leak, and reports a clean
number for them, is worse than no gate, because the number is believed.
"""
from __future__ import annotations

import os
from pathlib import Path

from watchquality import wq_corpus_scan as wcs
from watchquality import wq_policy


REPO = Path(wcs.__file__).resolve().parents[3]


def _fake_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    return tmp_path


def test_the_published_unit_is_the_repository_the_package_sits_in():
    """`published_root` walks up to the checkout, because that is what ships.

    The package is not the publication. A fork publishes its README, its
    changelog, its skills and its tests in the same push, and a reader looking
    for what a project grades will read the prose long before the source.
    """
    root, limited = wcs.published_root(Path(wcs.__file__).resolve())
    assert root == REPO, (root, REPO)
    assert limited is None, limited


def test_a_package_outside_a_checkout_says_how_far_it_could_see(tmp_path: Path):
    """Installed into site-packages there is no repository to find.

    The answer then is the package directory AND a sentence saying so. Silently
    scanning less is how `15 file(s) scanned` came to read like a clean bill of
    health for a tree of several hundred.
    """
    pkg = tmp_path / "site-packages" / "watchquality"
    pkg.mkdir(parents=True)
    mod = pkg / "wq_corpus_scan.py"
    mod.write_text("x = 1\n", encoding="utf-8")
    root, limited = wcs.published_root(mod)
    assert root == pkg, root
    assert limited and "package" in limited, limited


def test_a_default_run_collects_the_tests_directory(tmp_path: Path):
    """The exact miss: a file under `tests/` reaching `scan_text` at all."""
    repo = _fake_repo(tmp_path)
    (repo / "tests" / "test_x.py").write_text(
        "# path = acmeprivate/notes\n", encoding="utf-8")
    files = wcs.collect([repo])
    assert repo / "tests" / "test_x.py" in files, files
    hits = wcs.scan(files, repo, ("acmeprivate",))
    assert len(hits) == 1, hits
    assert "tests/test_x.py" in hits[0], hits


def test_a_default_run_collects_the_prose_a_reader_reaches_first(tmp_path: Path):
    """README and changelog prose, where the three known leaks actually were."""
    repo = _fake_repo(tmp_path)
    (repo / "CHANGELOG.md").write_text("shipped\n", encoding="utf-8")
    names = {p.name for p in wcs.collect([repo])}
    assert {"README.md", "CHANGELOG.md"} <= names, names


def test_this_repository_is_reached_in_every_published_corner():
    """A floor written as places, not as a count.

    A number would have to be edited on every added file and would be raised
    without thought; naming the directories means narrowing the walk back to one
    of them fails here, and adding a new published corner is a deliberate edit.
    """
    parts = {p.relative_to(REPO).parts[0] for p in wcs.collect([REPO])}
    assert {"tests", "skills", "watch-quality", "README.md"} <= parts, parts


def test_the_default_run_targets_the_checkout_and_not_the_package(capsys):
    """The regression this whole change exists to prevent, at the entry point.

    Every other case here calls `collect` and `published_root` directly, one
    layer BELOW the bug: the defect was `main`'s default target, and an
    independent review reverted that single line, watched the suite stay fully
    green, and got `15 file(s) scanned ... 0 corpus reference(s)` exit 0 back --
    the bad run, verbatim. So this case reads what the CLI actually scanned.
    """
    assert wcs.main([]) == 0
    err = capsys.readouterr().err
    whole = len(wcs.collect([REPO]))
    package = len(wcs.collect([Path(wcs.__file__).resolve().parent]))
    assert f"{whole} file(s) scanned under {REPO}" in err, err
    assert whole > package, (whole, package)


def test_the_walk_does_not_descend_into_what_nobody_publishes(tmp_path: Path):
    """Caches and version-control internals are on disk and are not published.

    They are also enormous, and a gate that takes a minute is a gate people
    stop running.
    """
    repo = _fake_repo(tmp_path)
    for skipped in ("__pycache__", ".pytest_cache", ".git"):
        (repo / skipped).mkdir(exist_ok=True)
        (repo / skipped / "leak.md").write_text("acmeprivate\n", encoding="utf-8")
    collected = wcs.collect([repo])
    # The reach assertion is not decoration: an absent file is absent from an
    # empty list too, and `collect` returning nothing would pass the line below
    # on its own. A review found exactly that shape in two cases here.
    assert collected, "nothing was collected, so nothing was proved"
    assert not [p for p in collected if p.name == "leak.md"], collected


def test_a_directory_above_the_checkout_cannot_silence_the_scan(tmp_path: Path):
    """The names are matched relative to the target, never absolutely.

    A checkout planted under a directory called `venv` scanned ZERO files and
    exited 0 -- a silent clean report, which is the failure this module was
    rewritten to end, re-created by a rule reading names nobody in the
    repository chose.
    """
    for ancestor in ("venv", "node_modules", ".tox"):
        repo = _fake_repo(tmp_path / ancestor / "proj")
        (repo / "leak.md").write_text("acmeprivate\n", encoding="utf-8")
        files = wcs.collect([repo])
        assert repo / "leak.md" in files, (ancestor, files)
        assert wcs.scan(files, repo, ("acmeprivate",)), ancestor


def test_a_published_directory_that_is_dropped_says_so(tmp_path: Path):
    """`docs/venv/guide.md` is a plausible page, and it IS dropped here.

    That is a real limit of a name-based skip list. The defect would be dropping
    it in silence under a line reading `0 corpus reference(s)`, so the caller
    is handed what it did not read.
    """
    repo = _fake_repo(tmp_path)
    (repo / "docs" / "venv").mkdir(parents=True)
    (repo / "docs" / "venv" / "guide.md").write_text("hi\n", encoding="utf-8")
    skipped: list[Path] = []
    assert repo / "docs" / "venv" / "guide.md" not in wcs.collect([repo], skipped)
    assert skipped == [repo / "docs" / "venv"]


def test_a_named_target_outside_this_checkout_also_says_what_it_dropped(
        tmp_path: Path, capsys):
    """The disclosure through the CLI, and for a target that is not this repo.

    Both halves were unguarded. Deleting the print loop, and deleting the
    argument that feeds it, each left the whole suite green -- the same "the
    case sits one layer below the bug" shape that let the default-target
    regression through a round earlier. And the loop itself was filtered to
    this package's own checkout, so a named target elsewhere scanned zero
    files, dropped a publishable one, and said nothing at all.
    """
    tree = tmp_path / "elsewhere"
    (tree / "build").mkdir(parents=True)
    (tree / "build" / "page.md").write_text("hi\n", encoding="utf-8")
    assert wcs.main([str(tree)]) == 0
    err = capsys.readouterr().err
    assert "not scanned" in err and "build" in err, err
    assert "0 file(s) scanned" in err, err
    # And the headline names what was WALKED. It named this package's own
    # checkout whatever the target was, so a run over a temporary tree
    # announced coverage of a repository it never opened -- in the one line a
    # reader pastes as proof.
    #
    # Asserted on that LINE, not on all of stderr. The first version read the
    # whole stream, where `# not scanned: <tree>/build/` already contains the
    # target, so the positive half never touched the header at all: a header
    # reading `/nowhere/at/all` kept the suite green.
    # The TARGET is pinned exactly; the counts are not. Pinning the whole line
    # made the case depend on the caller's own policy -- `refused_literals` is
    # resolved from the working directory, so the suite passed from the package
    # checkout and red from a corpus, for a header that was correct both times.
    # A guard too tight is a different defect, not a fix.
    header = [line for line in err.splitlines() if "file(s) scanned under" in line]
    assert len(header) == 1, header
    assert header[0].startswith(f"# 0 file(s) scanned under {tree},"), header[0]


def test_every_number_in_the_summary_line_is_measured(tmp_path: Path, capsys):
    """The counts, not just the target. Four of the five could be constants.

    A mutation sweep hardcoded the refused-literal count and the corpus-
    reference count to zero and the whole suite stayed green. That line is what
    a reader pastes as proof of coverage, so every number on it has to move
    with the run that produced it.
    """
    tree = tmp_path / "two"
    (tree / "a").mkdir(parents=True)
    (tree / "b").mkdir()
    (tree / "a" / "one.md").write_text("acmeprivate\n", encoding="utf-8")
    (tree / "b" / "two.md").write_text("acmeprivate\n", encoding="utf-8")
    policy = tmp_path / "watch-quality.toml"
    policy.write_text('refused_literals = ["acmeprivate", "othercorp"]\n',
                      encoding="utf-8")
    os.environ[wq_policy.ENV_VAR] = str(policy)
    wq_policy.load(refresh=True)
    try:
        assert wcs.main([str(tree / "a"), str(tree / "b")]) == 1
    finally:
        os.environ.pop(wq_policy.ENV_VAR, None)
        wq_policy.load(refresh=True)
    header = [l for l in capsys.readouterr().err.splitlines()
              if "file(s) scanned under" in l]
    assert header == [f"# 2 file(s) scanned under {tree / 'a'}, {tree / 'b'}, "
                      f"2 refused literal(s) and 0 anchor set(s) from the notes' own pages in force, "
                      f"2 corpus reference(s)"]


def test_a_refused_word_is_refused_in_any_case():
    """The rule that found the only live leak in five rounds, and had no case.

    It was pinned by the module's own selftest and by nothing in this suite --
    the exact gap class this repository has been bitten by before, where a
    check lives in one suite and a reader quotes the other's number. The spec
    table found it: `spec/gate.toml` names this case, and until it existed the
    conformance run was red.
    """
    for spelling in ("acmeprivate", "AcmePrivate", "ACMEPRIVATE", "AcMePrIvAtE"):
        hits = wcs.scan_text(f"a line naming {spelling} in prose\n", "f.md",
                             ("acmeprivate",))
        assert len(hits) == 1, (spelling, hits)
        assert "E-CORPUS-REFUSED-WORD" in hits[0], hits
        # And the word itself is never echoed back: a defect report that quotes
        # a refused literal publishes it into whatever reads the report.
        assert spelling.casefold() not in hits[0].casefold(), hits
    # The refused list's own casing is not the rule either.
    assert wcs.scan_text("naming acmeprivate\n", "f.md", ("ACMEPRIVATE",))


def test_a_line_naming_nothing_refused_is_left_alone():
    """The direction this rule was missing, and the reason it matters.

    Every case the rule cited fed it a line that DOES carry the word, so a
    scanner that refused every line it read would have passed all of them. A
    leak gate that reports every line is a leak gate somebody turns off, and
    then it reports nothing at all.
    """
    clean = ("a line about margins and hiring\n"
             "private acme, reversed and spaced\n")

    hits = wcs.scan_text(clean, "f.md", ("acmeprivate",))

    assert [h for h in hits if "E-CORPUS-REFUSED-WORD" in h] == [], hits


def test_an_empty_string_in_the_refused_list_refuses_nothing():
    """A list with a hole in it must not refuse the whole corpus.

    `"" in anything` is True, so one empty entry -- a trailing comma in the
    policy, a row somebody blanked instead of deleting -- would refuse every
    line of every file. The guard is the first half of a conjunction, and no
    case reached it: dropping that half left every cited case green.
    """
    hits = wcs.scan_text("a line about margins and hiring\n", "f.md",
                         ("", "acmeprivate"))

    assert [h for h in hits if "E-CORPUS-REFUSED-WORD" in h] == [], hits
    # And the real word beside it still fires, so the guard did not turn the
    # rest of the list off with it.
    assert wcs.scan_text("naming acmeprivate\n", "f.md", ("", "acmeprivate"))


def _corpus(root: Path) -> Path:
    """A corpus beside a policy, so a run with the flag can find something.

    `--require-literals` refuses a run that could not have made a finding, and
    an absent corpus is one of the three ways to be in that state. A test about
    some OTHER refusal has to put a corpus here, or it is measuring this one.
    """
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "a.md").write_text("- `[06:47]` to `[41:53]` COV -- a claim\n",
                                encoding="utf-8")
    return notes


def _with_policy(policy: Path | None):
    """Install a policy for one run, and put the process back afterwards."""
    if policy is None:
        os.environ.pop(wq_policy.ENV_VAR, None)
    else:
        os.environ[wq_policy.ENV_VAR] = str(policy)
    wq_policy.load(refresh=True)


def test_a_wordless_run_is_refused_when_the_caller_asked_for_words(tmp_path,
                                                                   capsys):
    """The whole point of the flag: a hook cannot read the summary line.

    The word list is private and does not travel with this package, so a run
    from the wrong directory finds no policy, compares every file against an
    empty list, prints `0 corpus reference(s)` and exits 0 -- byte-identical in
    its exit code to the run that actually checked. A push hook reads the exit
    code and nothing else.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    (tree / "a.md").write_text("acmeprivate\n", encoding="utf-8")
    empty = tmp_path / "watch-quality.toml"
    empty.write_text("refused_literals = []\n", encoding="utf-8")
    _with_policy(empty)
    try:
        assert wcs.main([str(tree)]) == 0            # the silent clean bill
        assert wcs.main([wcs.REQUIRE_FLAG, str(tree)]) == 2
    finally:
        _with_policy(None)
    err = capsys.readouterr().err
    assert wq_policy.ENV_VAR in err, err
    # And it does not report a count it did not earn: the refused run says why
    # rather than printing `0 corpus reference(s)` for somebody to quote.
    refused_run = err.split(wcs.REQUIRE_FLAG, 1)[1]
    assert "corpus reference(s)" not in refused_run, refused_run


def test_the_flag_is_not_a_second_way_to_pass(tmp_path):
    """With words in force it changes nothing -- including the verdict.

    A flag that only ever adds a refusal is one edit from being a flag that
    also grants one. Both answers are pinned here: the clean tree stays 0 with
    the flag, and the dirty tree stays 1.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    (tree / "clean.md").write_text("nothing to see\n", encoding="utf-8")
    policy = tmp_path / "watch-quality.toml"
    policy.write_text('refused_literals = ["acmeprivate"]\n', encoding="utf-8")
    _corpus(tmp_path)
    _with_policy(policy)
    try:
        assert wcs.main([wcs.REQUIRE_FLAG, str(tree)]) == 0
        (tree / "dirty.md").write_text("acmeprivate\n", encoding="utf-8")
        assert wcs.main([wcs.REQUIRE_FLAG, str(tree)]) == 1
    finally:
        _with_policy(None)


def test_a_run_that_walked_no_file_is_refused_too(tmp_path, capsys):
    """The other empty set, and the likelier accident.

    Words in force and no FILES is just as incapable of a finding: a named
    target that was moved or misspelled walks nothing and prints a clean
    summary, exit 0 (round-5 refutation F7). A caller reading only the exit
    code cannot tell that from a scan that checked a whole tree.
    """
    empty = tmp_path / "nothing"
    empty.mkdir()
    policy = tmp_path / "watch-quality.toml"
    policy.write_text('refused_literals = ["acmeprivate"]\n', encoding="utf-8")
    _corpus(tmp_path)
    _with_policy(policy)
    try:
        assert wcs.main([str(empty)]) == 0                    # the silent one
        assert wcs.main([wcs.REQUIRE_FLAG, str(empty)]) == 2
        # A target that does not exist was already loud: it is collected as a
        # named file and reported `E-READ`, exit 1. Pinned here so the new
        # refusal cannot quietly take that case over and turn a 1 into a 2.
        assert wcs.main([wcs.REQUIRE_FLAG, str(tmp_path / "no-such-tree")]) == 1
    finally:
        _with_policy(None)
    assert "walked no file at all" in capsys.readouterr().err


def test_the_flag_is_not_mistaken_for_a_path(tmp_path, capsys):
    """It is stripped from the targets, not walked as one.

    Left in `argv`, it resolves against the working directory and becomes a
    named target that does not exist -- which is how a flag turns into a scan
    of nothing that still exits 0.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    (tree / "a.md").write_text("hello\n", encoding="utf-8")
    policy = tmp_path / "watch-quality.toml"
    policy.write_text('refused_literals = ["acmeprivate"]\n', encoding="utf-8")
    _corpus(tmp_path)
    _with_policy(policy)
    try:
        assert wcs.main([wcs.REQUIRE_FLAG, str(tree)]) == 0
    finally:
        _with_policy(None)
    header = [l for l in capsys.readouterr().err.splitlines()
              if "file(s) scanned under" in l]
    assert header == [f"# 1 file(s) scanned under {tree}, 1 refused literal(s) "
                      f"and 1 anchor set(s) from the notes' own pages in force, 0 corpus reference(s)"]


def test_a_file_with_no_suffix_is_published_text_too(tmp_path: Path):
    """`LICENSE` and `.gitignore` are tracked, human-written and were unread.

    `.gitignore` is the pointed one: naming directories is its whole job, and a
    directory layout is one of the three things this gate refuses.
    """
    repo = _fake_repo(tmp_path)
    for name in ("LICENSE", ".gitignore", ".gitattributes"):
        (repo / name).write_text("acmeprivate/notes\n", encoding="utf-8")
    hits = wcs.scan(wcs.collect([repo]), repo, ("acmeprivate",))
    assert len(hits) == 3, hits


def test_a_namesake_of_the_scanner_is_read_like_any_other_file(tmp_path: Path):
    """A stale build copy of this module is tracked, published and was unread.

    Excusing by FILENAME excused every file that happens to share the name, and
    a vendored copy of this module is exactly that. The excuse is a marked LINE
    now, so an unmarked line in a namesake is read like any other line.
    """
    repo = _fake_repo(tmp_path)
    twin = repo / "vendored" / Path(wcs.__file__).name
    twin.parent.mkdir()
    twin.write_text("# path = acmeprivate/notes\n", encoding="utf-8")
    assert twin in wcs.collect([repo])
    assert wcs.scan([twin], repo, ("acmeprivate",))


def test_a_file_that_is_not_utf8_does_not_abort_the_run(tmp_path: Path):
    """A latin-1 subtitle killed the walk part-way and exited 1.

    Exit 1 is the code for "a leak is in the source", so the crash was
    indistinguishable from a finding, and every file sorted after it went
    unread. Subtitles are the file type most likely to arrive in some other
    encoding, and `.srt` and `.vtt` are both on the list.
    """
    repo = _fake_repo(tmp_path)
    (repo / "cue.srt").write_bytes("caf\xe9 acmeprivate\n".encode("latin-1"))
    (repo / "zz.md").write_text("clean\n", encoding="utf-8")
    hits = wcs.scan(wcs.collect([repo]), repo, ("acmeprivate",))
    assert len(hits) == 1 and "cue.srt" in hits[0], hits


def test_one_file_named_twice_is_scanned_once(tmp_path: Path):
    """Two overlapping targets inflated the count and printed each hit twice."""
    repo = _fake_repo(tmp_path)
    (repo / "leak.md").write_text("acmeprivate\n", encoding="utf-8")
    assert wcs.collect([repo, repo]) == wcs.collect([repo])
    assert len(wcs.scan(wcs.collect([repo, repo]), repo, ("acmeprivate",))) == 1


def test_a_package_standing_at_a_checkout_root_is_in_one(tmp_path: Path):
    """`pkg.parents` skips `pkg`, so a module at the root reported no checkout.

    The path it returned was right and the sentence beside it was false, which
    is the harder half to notice.
    """
    repo = tmp_path.resolve()
    (repo / ".git").mkdir()
    mod = repo / "wq_corpus_scan.py"
    mod.write_text("x = 1\n", encoding="utf-8")
    assert wcs.published_root(mod) == (repo, None)


def test_an_untracked_file_is_still_scanned(tmp_path: Path):
    """The walk is the filesystem, deliberately, and not `git ls-files`.

    A leak that has not been committed yet is precisely the one worth catching,
    and the tracked set does not contain it until the commit this gate is meant
    to stop.
    """
    repo = _fake_repo(tmp_path)
    (repo / "draft.md").write_text("# acmeprivate\n", encoding="utf-8")
    assert repo / "draft.md" in wcs.collect([repo])


def test_the_scanner_is_collected_and_comes_back_clean():
    """It is in the walk now, and it still has to be clean over the real file.

    The whole file was excused, so a refused word written into it could never be
    found. It is scanned like anything else; only lines it marks as fixtures are
    excused, and this asserts BOTH -- that the file is reached, and that the
    real file with the real policy has nothing left over.
    """
    collected = wcs.collect([REPO])
    assert collected, "nothing was collected, so nothing was proved"
    assert Path(wcs.__file__).resolve() in collected

    hits = wcs.scan([Path(wcs.__file__).resolve()], REPO, ("acmeprivate",))

    assert not hits, hits


def test_a_named_path_is_still_scanned_as_named(tmp_path: Path):
    """Passing a path explicitly must not be widened to its repository."""
    repo = _fake_repo(tmp_path)
    (repo / "pkg" / "only.md").write_text("hello\n", encoding="utf-8")
    assert wcs.collect([repo / "pkg"]) == [repo / "pkg" / "mod.py",
                                           repo / "pkg" / "only.md"]


def test_the_whole_repository_is_clean_of_the_two_rules_it_can_run_here():
    """The gate over this checkout, and an honest account of what that covers.

    Two of the three rules run here: video-id shape and dated exemption. The
    third cannot, and saying why matters more than the green tick. The refused
    words are the CALLER's -- a private repository's name, an employer, a client
    -- and writing one into this repository to test it would publish the very
    string the rule exists to keep out. So the refused rule is exercised on a
    temporary tree above, and in earnest only from the private checkout whose
    policy holds the words.

    The reach assertions are here so this cannot pass by scanning nothing: an
    empty file list produces an empty hit list, which is the same green.
    """
    files = wcs.collect([REPO])
    assert REPO / "README.md" in files
    assert any(p.parts[-2] == "tests" for p in files)
    hits = wcs.scan(files, REPO, ())
    assert not hits, hits[:20]


def test_the_scanner_is_scanned_and_only_its_marked_fixtures_are_excused():
    """It excused itself by resolved path, so a leak in it was invisible.

    The scanner quotes every shape it refuses, so something has to be excused --
    but excusing the FILE meant a refused word written into that one file could
    never be found, by construction. And excusing it by RESOLVED path meant a
    copy of the package at any other path was scanned in full and refused its own
    invented fixtures: point the gate at a release tarball, a CI copy or a second
    worktree and it exits 1 on files that leak nothing.

    Both come from the same choice. The unit of excuse is the marked LINE now,
    and the marker is honoured only in a file with this scanner's name -- so the
    original and every copy of it behave the same way, and every other line of
    the scanner is scanned like any other line.

    Restoring the whole-file excuse leaves the first assertion green and the
    second red; dropping the filename condition reverses that.
    """
    leak = 'path = "acmeprivate/notes"\n'
    fixture = f'check("a refused word is caught", {leak.strip()})  {wcs.FIXTURE}\n'

    assert wcs.scan_text(leak, "wq_corpus_scan.py", ("acmeprivate",))
    assert not wcs.scan_text(fixture, "wq_corpus_scan.py", ("acmeprivate",))
    # The marker is this file's, not a token any published page can spend.
    assert wcs.scan_text(fixture, "README.md", ("acmeprivate",))
    # And a copy of the scanner at another path is excused exactly as the
    # original is -- same bytes, same verdict, wherever it sits (§8).
    assert not wcs.scan_text(fixture, "/tmp/release/wq_corpus_scan.py",
                             ("acmeprivate",))


def test_the_walk_descends_a_symlinked_directory_once(tmp_path):
    """A tree reachable only through a link was never read.

    `rglob` does not follow a symlinked directory, so private content linked into
    the repository would have been published unscanned. Following them needs a
    seen-set in the same breath: a link back up the tree is a cycle, and a link
    to a sibling directory would otherwise report the same file twice.

    Walking without following leaves the first assertion red; following without
    the seen-set leaves the third red or never returns.
    """
    repo = _fake_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    # Reachable ONLY through the link: a directory of published pages that lives
    # somewhere else, which is the shape that would publish unscanned.
    (outside / "page.md").write_text("acmeprivate\n", encoding="utf-8")
    (repo / "linked").symlink_to(outside, target_is_directory=True)
    (repo / "loop").symlink_to(repo, target_is_directory=True)

    files = wcs.collect([repo])

    assert any(p.name == "page.md" for p in files), files
    assert [p.name for p in files].count("page.md") == 1, files
    assert len(files) < 50, "the cycle was walked more than once"


def test_a_refused_word_is_caught_whatever_the_file_is_encoded_in(tmp_path):
    """Three encodings, one literal, one verdict.

    `scan` fell back to a lossy read only on `UnicodeDecodeError`, and UTF-16
    does not raise one: it decodes as UTF-8 into every letter separated by a
    replacement character, so the file counted toward the scanned total, matched
    nothing, and produced no `# not scanned:` line. That is the silent clean
    report this module exists to end.

    Reverting the encoding walk to a plain UTF-8 read makes the UTF-16 row miss.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    body = "a line naming acmeprivate in prose\n"
    (tree / "utf8.md").write_bytes(body.encode("utf-8"))
    (tree / "latin1.md").write_bytes(body.encode("latin-1"))
    (tree / "utf16.md").write_bytes(body.encode("utf-16"))

    hits = wcs.scan(wcs.collect([tree]), tree, ("acmeprivate",))

    caught = {h.split(":")[0] for h in hits if "E-CORPUS-REFUSED-WORD" in h}
    assert caught == {"utf8.md", "latin1.md", "utf16.md"}, sorted(caught)


def test_a_file_that_decodes_as_nothing_is_reported_rather_than_scanned(tmp_path):
    """The miss is not the defect; the silence is.

    A file the reader cannot make text of must land in the not-scanned list,
    where the summary already names what it did not read. Counted as scanned and
    matched against nothing, it reads exactly like a clean file.

    Dropping the unread report leaves this red while every other case stays
    green.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    (tree / "binary.md").write_bytes(b"\x00\x01\x02\xff\xfe\x00\x00\x80\x81")

    unread: list[Path] = []
    files = wcs.collect([tree], None, unread)
    hits = wcs.scan(files, tree, ("acmeprivate",), (), unread)

    assert not hits, hits
    assert [p.name for p in unread] == ["binary.md"], unread


def test_a_refused_word_is_caught_however_its_separators_are_written():
    """The one multi-word literal walked out through its own space.

    Case folding closed casing, plurals and possessives. It does not close the
    separator: the literal evades when its space is written as a hyphen, an
    underscore, nothing at all, or two spaces, and EVERY literal evades when it
    is broken across a line or has a zero-width space dropped into it. None of
    those is a different word; they are the same word with different bytes
    between the letters.

    Reverting to a plain `word in line` test leaves the first row green and
    every other row red.
    """
    for spelling in ("acme private", "acme-private", "acme_private",
                     "acmeprivate", "acme  private", "acme​private",
                     "acme­private", "acme\nprivate"):
        hits = wcs.scan_text(f"a line about {spelling} in prose\n", "f.md",
                             ("acme private",))
        found = [h for h in hits if "E-CORPUS-REFUSED-WORD" in h]
        assert len(found) == 1, (repr(spelling), hits)


def test_the_separator_walk_does_not_refuse_a_line_that_names_nothing():
    """The other half, and the half that decides whether the gate survives.

    Squashing separators out of both sides is what catches a two-word name
    written as one. It is also what would let any two adjacent words match a
    literal that happens to be their concatenation, and a gate that fires on
    innocent prose is a gate somebody removes. Both directions are asked here,
    of the same normalisation.

    A version that reports every line satisfies the case above and fails here.
    """
    clean = ("a line about margins and hiring\n"
             "private acme, reversed and spaced\n"
             "an acme sold a private company to another\n")

    hits = wcs.scan_text(clean, "f.md", ("acmeprivate", "acme private"))

    assert [h for h in hits if "E-CORPUS-REFUSED-WORD" in h] == [], hits


def test_a_word_broken_across_a_line_is_reported_on_the_line_it_starts():
    """A finding a reader cannot go and look at is not a finding.

    The squashed text has no lines in it, so the offset has to be carried back.
    Reporting line 1 for everything passes the catch cases above and sends every
    reader to the top of the file.
    """
    body = "clean first line\nsecond line has acme\nprivate spilling over\n"

    hits = wcs.scan_text(body, "f.md", ("acmeprivate",))

    found = [h for h in hits if "E-CORPUS-REFUSED-WORD" in h]
    assert len(found) == 1, hits
    assert found[0].startswith("f.md:2 "), found


def test_a_line_repeating_one_notes_timestamps_is_refused():
    """The leak class no word list can catch, and the one that got through.

    An independent read of this fork found thirteen tracked lines that
    reproduced a private recording by its ordered timestamps alone -- five of
    them carrying the note's prose beside them -- and four more naming a brand
    off the screen. Every one of them exited 0 here, correctly: none contains a
    refusable word, and the refused list can only refuse what somebody thought
    to write down. What identifies a recording is its ANCHORS, and nobody can
    enumerate those in advance.

    So this rule is not a list. It compares the line in front of it against the
    corpus itself, and the stamps are never echoed back: a defect report that
    quotes the pair publishes it into whatever reads the report, which is the
    same leak one hop further out.
    """
    anchors = wcs.anchor_sets_from_lines(["- `[01:20]` `[03:05]` `[07:41]` COV"])

    hits = wcs.scan_text("- `[01:20]` to `[03:05]` COV -- a claim\n", "f.md",
                         (), anchors)

    assert len(hits) == 1, hits
    assert "E-CORPUS-ANCHOR-SET" in hits[0], hits
    assert "01:20" not in hits[0] and "03:05" not in hits[0], hits


def test_a_line_carrying_one_timestamp_is_left_alone():
    """One stamp is not a recording, and the fixtures are full of them.

    `[00:00]` appears in this suite, in the README and in every synthetic note
    the tests build. A rule that fired on a single stamp would refuse the fork's
    own fixtures, and a gate that refuses everything is a gate somebody turns
    off. The pair is the fingerprint; the stamp is not.
    """
    anchors = wcs.anchor_sets_from_lines(["- `[01:20]` to `[03:05]` COV"])

    hits = wcs.scan_text("- `[01:20]` COV -- one anchor, invented\n", "f.md",
                         (), anchors)

    assert [h for h in hits if "E-CORPUS-ANCHOR-SET" in h] == [], hits


def test_two_stamps_from_two_different_note_lines_are_left_alone():
    """Contained in ONE line, not scattered across the corpus.

    Flattening every note into one bag of stamps would make the rule fire on any
    two round numbers, because a corpus of twenty-five notes carries most of
    them somewhere. What reproduces a recording is stamps that stood together on
    one line.
    """
    anchors = wcs.anchor_sets_from_lines(["- `[01:20]` to `[09:00]` COV",
                                          "- `[03:05]` to `[11:12]` COV"])

    hits = wcs.scan_text("- `[01:20]` to `[03:05]` COV -- a claim\n", "f.md",
                         (), anchors)

    assert [h for h in hits if "E-CORPUS-ANCHOR-SET" in h] == [], hits


def test_the_anchor_sets_are_read_from_the_corpus_and_not_from_a_list(tmp_path):
    """Where the comparison set comes from, pinned apart from the comparison.

    If this returned nothing the rule above would pass every line and the run
    would print the same clean summary it printed for thirteen leaked ones. A
    line with fewer than two stamps carries no pair and is not an anchor set.
    """
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("- `[01:20]` to `[03:05]` COV -- a claim\n"
                                "- `[09:00]` COV -- one anchor\n"
                                "prose with no anchor at all\n",
                                encoding="utf-8")

    sets = wcs.anchor_sets(notes)

    assert sets == (frozenset({80, 185}),), sets       # 1:20 and 3:05, in seconds


def test_the_same_moment_written_differently_is_still_refused():
    """The rule compared strings, so one keystroke walked through it.

    Independent verification took the two real stamps off one note line and
    published them six other ways. Every one exited 0: the pad dropped, an hour
    field added, the brackets removed, parentheses instead, a space inside the
    bracket, the pair written as one range. Same moments, same recording, a
    different string. What identifies a moment is the second it names.
    """
    anchors = wcs.anchor_sets_from_lines(["- `[06:47]` to `[41:53]` COV -- a claim"])

    for line in ("- `[6:47]` to `[41:53]` COV",           # the pad dropped
                 "- `[0:06:47]` to `[0:41:53]` COV",     # an hour field added
                 "- 06:47 to 41:53 COV",                 # no brackets at all
                 "- (06:47) to (41:53) COV",             # parentheses
                 "- `[ 06:47 ]` to `[ 41:53 ]` COV",     # a space inside
                 "- `[06:47-41:53]` COV"):               # one bracketed range
        hits = [h for h in wcs.scan_text(line + "\n", "f.md", (), anchors)
                if "E-CORPUS-ANCHOR-SET" in h]
        assert len(hits) == 1, (line, hits)


def test_a_pair_of_whole_minute_marks_is_not_refused():
    """The false positive that turns a gate off.

    `[00:00]` and `[02:00]` is the likeliest pair anybody writes in a README, a
    chapter list or a fixture, and some note line somewhere carries both. The
    report may not quote the stamps, so the author of that page sees an exit 1
    it cannot explain -- and a gate nobody can explain is a gate somebody
    removes.
    """
    anchors = wcs.anchor_sets_from_lines(
        ["- `[00:00]` to `[02:00]` COV -- a real note line"])

    hits = wcs.scan_text("chapters at [00:00] and [02:00] make an intro\n",
                         "f.md", (), anchors)

    assert [h for h in hits if "E-CORPUS-ANCHOR-SET" in h] == [], hits


def test_three_whole_minute_marks_are_still_refused():
    """The control on the excuse above, and the reason it is narrow.

    Excusing round numbers is a hole if it excuses a run of them. Two round
    marks is a coincidence anyone can write; three that all stand on one note
    line is that note's shape.
    """
    anchors = wcs.anchor_sets_from_lines(
        ["- `[00:00]` `[02:00]` `[05:00]` COV -- a real note line"])

    hits = wcs.scan_text("see [00:00], [02:00] and [05:00]\n", "f.md",
                         (), anchors)

    assert len([h for h in hits if "E-CORPUS-ANCHOR-SET" in h]) == 1, hits


def test_a_run_with_no_anchor_sets_is_refused_when_the_caller_asked(tmp_path,
                                                                    capsys):
    """The third empty set, and the same hole one field over.

    The flag already refuses a run with no words and a run with no files,
    because both print `0 corpus reference(s)` and exit 0 while being incapable
    of a finding. A run with no ANCHORS is incapable in exactly the same way,
    and it is as easy to reach: a corpus root pointed elsewhere, a notes
    directory not yet cloned, a sparse checkout.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    (tree / "a.md").write_text("- `[06:47]` to `[41:53]` COV\n", encoding="utf-8")
    policy = tmp_path / "watch-quality.toml"
    policy.write_text('refused_literals = ["acmeprivate"]\n', encoding="utf-8")
    _with_policy(policy)
    try:
        assert wcs.main([str(tree)]) == 0            # the silent clean bill
        assert wcs.main([wcs.REQUIRE_FLAG, str(tree)]) == 2
    finally:
        _with_policy(None)
    err = capsys.readouterr().err
    refused_run = err.split(wcs.REQUIRE_FLAG, 1)[1]
    assert "corpus reference(s)" not in refused_run, refused_run


def test_the_summary_says_how_many_anchor_sets_were_in_force(tmp_path, capsys):
    """A count a reader can check, beside the one for refused words.

    `0 corpus reference(s)` is the same line whether the corpus was compared or
    was never opened, and that line is what gets pasted as proof. The summary
    already names the word count for exactly this reason; the anchor count is
    the second thing this run's verdict rests on.
    """
    tree = tmp_path / "pub"
    tree.mkdir()
    (tree / "clean.md").write_text("nothing to see\n", encoding="utf-8")

    assert wcs.main([str(tree)]) == 0

    err = capsys.readouterr().err
    assert "anchor set(s) from the notes' own pages in force" in err, err
