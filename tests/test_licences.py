"""Code of others in the repository carries its licence."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_every_bundled_file_has_its_licence_in_the_notices() -> None:
    """Files of others (frontend/vendor) are named in THIRD_PARTY_NOTICES.md, which the App image ships."""
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text()
    bundled = sorted(str(p.relative_to(REPO)) for p in (REPO / "frontend" / "vendor").iterdir() if p.is_file())

    assert [p for p in bundled if f"`{p}`" not in notices] == []
    assert "THIRD_PARTY_NOTICES.md" in (REPO / "sunflow_app" / "Dockerfile").read_text()


def test_the_eebus_app_ships_the_licences_of_its_modules() -> None:
    """The EEBUS program carries the code of eebus-go and its modules: their licences go into the image."""
    licences = (REPO / "eebus" / "THIRD_PARTY_LICENSES.txt").read_text()
    required = [line.split()[0] for line in (REPO / "eebus" / "go.mod").read_text().splitlines() if line.startswith("\tgithub.com/enbility/")]

    assert [m for m in required if m not in licences] == []
    assert "GNU GENERAL PUBLIC" not in licences.upper()
    assert "THIRD_PARTY_LICENSES.txt" in (REPO / "sunflow_eebus" / "Dockerfile").read_text()
