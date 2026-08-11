from typer.testing import CliRunner

from simklcli.cli import app


def test_package_exposes_simkl_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Track movies, shows, and anime on Simkl." in result.stdout
    assert "auth" in result.stdout


def test_installed_app_uses_environment_credential_override() -> None:
    result = CliRunner().invoke(
        app,
        ["auth", "status", "--json"],
        env={"SIMKL_ACCESS_TOKEN": "environment-secret"},
    )

    assert result.exit_code == 0
    assert '"credential_source":"environment"' in result.stdout
    assert "environment-secret" not in result.output


def test_login_storage_choices_only_offer_persistent_backends() -> None:
    result = CliRunner().invoke(app, ["auth", "login", "--help"])

    assert result.exit_code == 0
    assert "keyring" in result.stdout
    assert "file" in result.stdout
    assert "environment" not in result.stdout
