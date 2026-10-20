import pytest

from creditdash import config


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch, tmp_path):
    """Tests never read the developer's .env."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no.env")
