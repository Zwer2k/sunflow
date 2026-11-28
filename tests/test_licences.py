"""Code of others in the repository carries its licence."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_every_bundled_file_has_its_licence_in_the_notices() -> None:
    """Files of others (frontend/vendor) are named in THIRD_PARTY_NOTICES.md, which the App image ships."""
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text()
    bundled = sorted(str(p.relative_to(REPO)) for p in (REPO / "frontend" / "vendor").iterdir() if p.is_file())

    assert [p for p in bundled if f"`{p}`" not in notices] == []
    assert "THIRD_PARTY_NOTICES.md" in (REPO / "sunflow_app" / "Dockerfile").read_text()
