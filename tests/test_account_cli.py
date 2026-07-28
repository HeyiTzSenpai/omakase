from click.testing import CliRunner

from omakase.cli import cli
from omakase.lite import auth, db


def test_account_bootstrap_imports_argon2_hash_from_stdin(monkeypatch, tmp_path):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    password_hash = auth.hash_password("owner-password")

    result = CliRunner().invoke(
        cli,
        [
            "account-bootstrap",
            "--email",
            "owner@example.com",
            "--display-name",
            "Owner",
            "--password-hash-stdin",
        ],
        input=f"{password_hash}\n",
    )

    assert result.exit_code == 0
    assert password_hash not in result.output
    conn = db.connect(tmp_path)
    record = db.get_login_record(conn, "owner@example.com")
    conn.close()
    assert record["role"] == "admin"
    assert auth.verify_password("owner-password", record["password_hash"])


def test_account_bootstrap_rejects_non_argon2_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))

    result = CliRunner().invoke(
        cli,
        [
            "account-bootstrap",
            "--email",
            "owner@example.com",
            "--password-hash-stdin",
        ],
        input="plaintext-is-not-accepted\n",
    )

    assert result.exit_code != 0
    assert "Argon2id" in result.output
