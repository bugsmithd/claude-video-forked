"""setup.py --json surfaces the resolved watch detail."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SETUP = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts" / "setup.py"


def _run(args, *, home=None, extra_env=None):
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    # Don't let a real key in the developer's shell env leak into the test.
    # EVERY backend's variable, not the two that existed when this was written:
    # an exported OPENROUTER_API_KEY silently turned three keyless cases into
    # ready ones, and the tests failed on the machine that had the key rather
    # than on the change that broke them.
    for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
                 "WHISPER_CPP_BIN", "WHISPER_CPP_MODEL", "SETUP_COMPLETE"):
        env.pop(name, None)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # Windows
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SETUP), *args],
        capture_output=True, text=True, env=env,
    )


def _write_env(home: Path, body: str) -> None:
    cfg = home / ".config" / "watch"
    cfg.mkdir(parents=True, exist_ok=True)
    f = cfg / ".env"
    f.write_text(body, encoding="utf-8")
    f.chmod(0o600)


def test_json_reports_watch_detail():
    proc = _run(["--json"])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["watch_detail"] == "balanced"


def test_keyless_completed_setup_proceeds_silently(tmp_path):
    """A user who finished setup without a key must NOT be nagged forever."""
    _write_env(tmp_path, "GROQ_API_KEY=\nOPENAI_API_KEY=\nSETUP_COMPLETE=true\n")
    chk = _run(["--check"], home=tmp_path)
    assert chk.returncode == 0, f"keyless-complete should pass --check; got {chk.returncode}: {chk.stderr}"
    assert chk.stdout == "" and chk.stderr == ""

    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["can_proceed"] is True
    assert js["first_run"] is False
    assert js["setup_complete"] is True
    # status still encourages a key even though we can proceed
    assert js["status"] == "needs_key"


def test_keyless_first_run_is_encouraged(tmp_path):
    """Genuine first run with no key: --check reports exit 3 (encourage a key)."""
    _write_env(tmp_path, "GROQ_API_KEY=\nOPENAI_API_KEY=\n")
    chk = _run(["--check"], home=tmp_path)
    assert chk.returncode == 3, chk.stderr

    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["can_proceed"] is False
    assert js["first_run"] is True


def test_key_present_is_ready(tmp_path):
    _write_env(tmp_path, "GROQ_API_KEY=sk-test-abc\n")
    chk = _run(["--check"], home=tmp_path)
    assert chk.returncode == 0, chk.stderr

    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["status"] == "ready"
    assert js["can_proceed"] is True
    assert js["whisper_backend"] == "groq"


def test_the_preflight_never_names_a_backend_no_unflagged_run_would_take(tmp_path):
    """verification G4 — the setup half of the OpenRouter demotion.

    `whisper.load_api_key` stopped choosing `openrouter` automatically and
    three cases in `test_whisper.py` hold it there. The preflight was demoted
    in the same pass and nothing held it: restoring its `OPENROUTER_API_KEY`
    branch left all 293 cases green, and setup then reported `ready` on
    `openrouter` while every unflagged run fell through to groq, openai or
    local. That is the exact divergence `_have_api_key`'s own comment forbids
    -- a preflight naming a backend the run will not use.
    """
    _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-test\n")
    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["whisper_backend"] != "openrouter", js
    assert js["status"] == "needs_key", js

    # And it is not that the key is unreadable: add the one an unflagged run
    # WOULD take, and the preflight names that one.
    _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-test\nGROQ_API_KEY=sk-test-abc\n")
    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["whisper_backend"] == "groq", js
    assert js["status"] == "ready", js


def test_the_scaffold_never_pins_a_provider_the_module_argues_against():
    """The generated .env is configuration a user is invited to uncomment.

    Every other line in it is inert prose; the provider pin is the one line
    that changes a request. It was written when the default was `groq` and
    stayed there after `whisper.OPENROUTER_PROVIDER` moved to the provider the
    bill was actually measured at, so uncommenting the scaffold restored the
    configuration the module above it argues against. Asserting against the
    module rather than against a literal keeps the two from drifting again.
    """
    import setup
    import whisper

    pins = [ln for ln in setup.ENV_TEMPLATE.splitlines()
            if "WATCH_OPENROUTER_PROVIDER" in ln]
    assert pins, "the scaffold no longer mentions the provider pin at all"
    for line in pins:
        assert line.split("=", 1)[1].strip() == whisper.OPENROUTER_PROVIDER, line


def test_the_shipped_doc_names_the_provider_the_module_pins():
    """The THIRD copy of the same value, and the one nothing was watching.

    Pinning the scaffold to the module closed one pair and read as closing the
    fact (round-16 F1). It did not: `SKILL.md` states the default in prose,
    twice, and no case pinned it, so changing the module left the suite green
    and the shipped documentation wrong. A value written in three places needs
    three assertions or one place.
    """
    import re

    import whisper

    doc = (Path(__file__).resolve().parent.parent
           / "skills" / "watch" / "SKILL.md").read_text(encoding="utf-8")
    claims = re.findall(r"default(?:s to)?[ \t]+`([a-z0-9_-]+)`", doc)
    claims += re.findall(r"The default is `([a-z0-9_-]+)`", doc)
    named = [c for c in claims if c in ("groq", "deepinfra", "together",
                                        "openai")]
    assert named, "SKILL.md no longer names a default provider at all"
    for value in named:
        assert value == whisper.OPENROUTER_PROVIDER, (value, named)
