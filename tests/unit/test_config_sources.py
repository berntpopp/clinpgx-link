"""Website-linked CPIC stays a distinct, explicitly allowlisted source."""

import pytest
from pydantic import ValidationError

from clinpgx_link.config import Settings


def test_cpic_has_an_independent_exact_source_origin():
    settings = Settings(_env_file=None)
    assert settings.cpic_api_base_url == "https://api.cpicpgx.org/v1"
    assert settings.cpic_allowed_origins == ("https://api.cpicpgx.org",)
    assert settings.api_allowed_origins == ("https://api.clinpgx.org",)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/v1",
        "http://api.cpicpgx.org/v1",
        "https://secret@api.cpicpgx.org/v1",
        "https://api.cpicpgx.org/v1?token=secret",
    ],
)
def test_cpic_source_cannot_escape_its_origin_contract(url):
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, cpic_api_base_url=url)
    assert "secret" not in str(exc.value)
