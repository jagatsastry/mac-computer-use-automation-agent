"""Command-line interface for the automation agent."""

import argparse
import sys
from pathlib import Path
from typing import Optional, List

from .config import LogLevel, ModelProvider, StatusUIMode
from .version import __description__, __version__


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="automation-agent",
        description=__description__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  automation-agent "Click on the Safari icon"
  automation-agent --vision-server-url http://192.168.1.100:8080 "Open Finder"
  automation-agent --dry-run --verbose "Test prompt"
        """,
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    parser.add_argument("prompt", type=str, help="The automation task to perform")

    # Configuration
    parser.add_argument("--config", type=Path, metavar="FILE", help="Path to JSON config file")

    # Provider selection
    parser.add_argument(
        "--provider",
        type=str,
        choices=[p.value for p in ModelProvider],
        help="LLM provider to use (local or anthropic)",
    )

    # Vision server settings
    parser.add_argument(
        "--vision-server-url", type=str, metavar="URL",
        help="Vision server URL (any OpenAI-compatible endpoint)",
    )
    parser.add_argument("--vision-model", type=str, metavar="MODEL", help="Vision model to use")
    parser.add_argument(
        "--vision-timeout", type=int, metavar="SECONDS",
        help="Timeout for vision server API calls",
    )

    # Anthropic settings
    parser.add_argument(
        "--anthropic-api-key",
        type=str,
        metavar="KEY",
        help="Anthropic API key (or set AGENT_ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--anthropic-model",
        type=str,
        metavar="MODEL",
        help="Anthropic model to use (default: claude-sonnet-4-20250514)",
    )

    # OpenRouter settings (used by Molmo mode)
    parser.add_argument(
        "--openrouter-api-key",
        type=str,
        metavar="KEY",
        help="OpenRouter API key for Molmo mode (or set AGENT_OPENROUTER_API_KEY/OPENROUTER_API_KEY)",
    )

    # Logging
    parser.add_argument(
        "--log-level",
        type=str,
        choices=[level.value for level in LogLevel],
        help="Logging level",
    )
    parser.add_argument("--log-dir", type=Path, metavar="DIR", help="Directory for log files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--status-ui",
        type=str,
        choices=[mode.value for mode in StatusUIMode],
        help="Show a live on-screen status UI during execution",
    )
    parser.add_argument(
        "--verbose-overlay",
        action="store_true",
        help="Show detailed LLM responses and reasoning in the status overlay",
    )

    # Execution
    parser.add_argument(
        "--dry-run", action="store_true", help="Analyze and plan without executing"
    )
    parser.add_argument(
        "--molmo",
        action="store_true",
        help="Use Molmo for vision-based coordinate identification",
    )
    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        args.log_level = LogLevel.DEBUG.value

    if not args.prompt.strip():
        parser.error("prompt cannot be empty")

    return args


def validate_args(args: argparse.Namespace) -> None:
    """Validate parsed arguments."""
    if args.config and not args.config.exists():
        print(f"Error: Configuration file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    if args.vision_timeout is not None and args.vision_timeout <= 0:
        print("Error: --vision-timeout must be greater than 0", file=sys.stderr)
        sys.exit(1)
