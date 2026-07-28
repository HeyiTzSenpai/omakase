"""CLI entry point for Omakase."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from omakase import __version__
from omakase.adapters.base import list_sources
from omakase.engine import run
from omakase.output.terminal import show_recommendations
from omakase.types import OmakaseConfig, resolve_model_preset

LLM_TYPES = [
    "ollama",
    "lmstudio",
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "openrouter",
    "groq",
    "together",
]


@click.group()
@click.version_option(version=__version__)
def cli():
    """Omakase — an LLM-powered sommelier for anime.

    \b
    Connect AniList or MyAnimeList, point it at any LLM (local or cloud),
    and get a hand-picked tasting menu of anime based on what you actually
    love — not what's popular.
    """


@cli.command()
@click.option("--source", "-s", default="anilist", help="Data source (anilist, myanimelist)")
@click.option("--username", "-u", required=True, help="Your username on the source platform")
@click.option(
    "--profile",
    "-p",
    default="taste-profile.md",
    help="Path to your taste profile markdown file",
    show_default=True,
)
@click.option(
    "--llm-url",
    default="http://localhost:11434",
    help="LLM API base URL",
    show_default=True,
    envvar="OMAKASE_LLM_URL",
)
@click.option(
    "--model",
    "-m",
    default="qwen2.5:7b",
    help="Model name",
    show_default=True,
    envvar="OMAKASE_MODEL",
)
@click.option(
    "--llm-type",
    default="ollama",
    type=click.Choice(LLM_TYPES),
    help="LLM backend type",
    show_default=True,
)
@click.option(
    "--pool-size", default=100, help="Number of candidates to consider", show_default=True
)
@click.option(
    "--temperature", default=0.4, help="LLM temperature (0.0-1.0)", show_default=True, type=float
)
@click.option(
    "--json", "json_output", is_flag=True, help="Output raw JSON instead of formatted table"
)
@click.option(
    "--mode",
    default="fast",
    type=click.Choice(["fast", "pro"]),
    help="Model tier: fast (cheap) or pro (thinking)",
    show_default=True,
)
@click.option("--use-planning", is_flag=True, help="Use your Plan to Watch list as candidates")
def recommend(
    source,
    username,
    profile,
    llm_url,
    model,
    llm_type,
    pool_size,
    temperature,
    json_output,
    mode,
    use_planning,
):
    """Serve your anime tasting menu — 5–10 personalized picks."""
    llm_url, llm_type, model, supports_json = resolve_model_preset(llm_url, llm_type, model, mode)

    cfg = OmakaseConfig(
        source=source,
        username=username,
        llm_url=llm_url,
        model=model,
        profile_path=profile,
        candidate_pool_size=pool_size,
        temperature=temperature,
        llm_type=llm_type,
        output_format="json" if json_output else "terminal",
        mode=mode,
        supports_json_mode=supports_json,
        use_planning=use_planning,
    )

    recs = run(cfg)

    if json_output:
        print(
            json.dumps(
                {
                    "source": source,
                    "username": username,
                    "recommendations": [
                        {
                            "title": r.title,
                            "predicted_score": r.predicted_score,
                            "reasoning": r.reasoning,
                            "best_match_from_history": r.best_match_from_history,
                        }
                        for r in recs
                    ],
                },
                indent=2,
            )
        )
    else:
        show_recommendations(recs, source, username)


@cli.command()
def sources():
    """List available data source adapters."""
    click.echo("Available sources:")
    for s in list_sources():
        click.echo(f"  - {s}")


@cli.command()
def backends():
    """List available LLM backends."""
    from omakase.llm import list_backends

    click.echo("Available LLM backends:")
    for b in list_backends():
        click.echo(f"  - {b}")


@cli.command("account-bootstrap")
@click.option("--email", required=True, help="Owner email address")
@click.option("--display-name", default="Owner", show_default=True)
@click.option(
    "--password-hash-stdin",
    is_flag=True,
    required=True,
    help="Read an existing Argon2id password hash from standard input",
)
def account_bootstrap(email, display_name, password_hash_stdin):
    """Create or update the Omakase Lite owner without handling plaintext."""
    from omakase.lite import db

    password_hash = click.get_text_stream("stdin").readline().strip()
    if not password_hash.startswith("$argon2id$"):
        raise click.ClickException("Standard input must contain one Argon2id password hash.")
    conn = db.connect()
    try:
        try:
            user_id = db.bootstrap_admin(
                conn,
                email=email,
                password_hash=password_hash,
                display_name=display_name,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        conn.close()
    click.echo(f"Omakase Lite owner is ready (account {user_id}).")


@cli.command()
@click.argument("path", type=click.Path(), default="taste-profile.md")
def init(path):
    """Create a starter taste profile."""
    template = """# My Taste Profile

## Things I love
- Morally complex protagonists, not pure-hearted heroes
- Dense world-building over slice-of-life
- Stories that earn their ending — strong third act
- [Add more...]

## Things I bounce off
- [Add what you dislike...]

## Characters that resonate
- [Character] — [why they resonate]

## Recent loves (don't recommend these — just calibration)
- [Title you scored highly]
"""
    dest = Path(path)
    if dest.exists():
        click.echo(f"File already exists: {path}", err=True)
        sys.exit(1)
    dest.write_text(template.strip() + "\n")
    click.echo(f"Created taste profile at {path}")
    click.echo(
        "Edit it to describe what you love, what you bounce off, and the characters that resonate with you."
    )


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option(
    "--port", "-p", default=8765, help="Port to listen on", show_default=True, envvar="OMAKASE_PORT"
)
def web(host, port):
    """Launch the Omakase web setup UI in your browser."""
    from omakase.web.server import run_server

    run_server(host=host, port=port)
