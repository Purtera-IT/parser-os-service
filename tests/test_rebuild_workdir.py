"""The rebuild endpoint's working directory must not be shared between requests.

It used to be /tmp/parser-os/orbitbrief-{compile_id}, wiped with rmtree on entry.
Two rebuilds of the same deal arriving together deleted each other's downloaded
artifacts mid-flight; the loser died with FileNotFoundError naming a file it had
just written itself. Retrying always "fixed" it, which is why it survived: the
bug is invisible unless two requests overlap.
"""
import re
from pathlib import Path

SRC = Path("src/parser_os_service/server/routes/orbitbrief_latest.py")


def test_workdir_is_unique_per_request():
    body = SRC.read_text()
    m = re.search(r'f"/tmp/parser-os/orbitbrief-\{compile_id\}([^"]*)"', body)
    assert m, "the rebuild working directory is no longer built the expected way"
    suffix = m.group(1)
    assert "uuid" in suffix, (
        "working directory is keyed only by compile_id; two concurrent rebuilds of "
        "the same deal will delete each other's artifacts"
    )


def test_entry_does_not_wipe_a_directory_it_may_not_own():
    body = SRC.read_text()
    head = body[: body.index("art_dir.mkdir")]
    assert "rmtree" not in head.split("local_root")[-1], (
        "rmtree before mkdir destroys a concurrent request's downloads"
    )


def test_the_directory_is_still_cleaned_up():
    # Unique per request is only safe if each one is removed afterwards.
    body = SRC.read_text()
    assert "shutil.rmtree(work, ignore_errors=True)" in body
    assert "finally" in body
