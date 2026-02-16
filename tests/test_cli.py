"""Tests for CLI argument parsing."""

import pytest
from pathlib import Path
from automation_agent.cli import parse_args, validate_args, create_parser


class TestCreateParser:
    """Test argument parser creation."""

    def test_parser_creation(self):
        """Test that parser is created successfully."""
        parser = create_parser()
        assert parser is not None
        assert parser.prog == "automation-agent"
        print("✓ Parser created successfully")


class TestParseArgs:
    """Test argument parsing."""

    def test_parse_basic_prompt(self):
        """Test parsing basic prompt."""
        args = parse_args(["Click on Safari"])

        assert args.prompt == "Click on Safari"
        assert args.dry_run is False
        assert args.verbose is False
        assert args.config is None
        print("✓ Basic prompt parsing works")

    def test_parse_with_dry_run(self):
        """Test parsing with --dry-run flag."""
        args = parse_args(["--dry-run", "Test prompt"])

        assert args.prompt == "Test prompt"
        assert args.dry_run is True
        print("✓ Dry-run flag works")

    def test_parse_with_molmo_flag(self):
        """Test parsing with --molmo flag."""
        args = parse_args(["--molmo", "Find the reserve button"])

        assert args.molmo is True
        assert args.prompt == "Find the reserve button"
        print("✓ Molmo flag parsing works")

    def test_parse_with_verbose(self):
        """Test parsing with --verbose flag."""
        args = parse_args(["--verbose", "Test"])

        assert args.verbose is True
        assert args.log_level == "DEBUG"
        print("✓ Verbose flag sets DEBUG log level")

    def test_parse_with_ollama_options(self):
        """Test parsing with Ollama options."""
        args = parse_args([
            "--ollama-host", "http://192.168.1.100:11434",
            "--ollama-model", "custom-model",
            "--ollama-timeout", "60",
            "Test prompt"
        ])

        assert args.ollama_host == "http://192.168.1.100:11434"
        assert args.ollama_model == "custom-model"
        assert args.ollama_timeout == 60
        print("✓ Ollama options parsing works")

    def test_parse_with_log_level(self):
        """Test parsing with --log-level."""
        args = parse_args(["--log-level", "DEBUG", "Test"])

        assert args.log_level == "DEBUG"
        print("✓ Log level parsing works")

    def test_parse_with_config_file(self):
        """Test parsing with --config."""
        args = parse_args(["--config", "config.json", "Test"])

        assert args.config == Path("config.json")
        print("✓ Config file parsing works")

    def test_parse_empty_prompt_fails(self):
        """Test that empty prompt raises error."""
        with pytest.raises(SystemExit):
            parse_args([""])
        print("✓ Empty prompt validation works")

    def test_parse_no_args_fails(self):
        """Test that no arguments raises error."""
        with pytest.raises(SystemExit):
            parse_args([])
        print("✓ No arguments validation works")


class TestValidateArgs:
    """Test argument validation."""

    def test_validate_valid_args(self):
        """Test validation with valid arguments."""
        args = parse_args(["Test prompt"])
        # Should not raise
        validate_args(args)
        print("✓ Valid args pass validation")

    def test_validate_nonexistent_config_file(self, tmp_path, capsys):
        """Test validation with nonexistent config file."""
        nonexistent = tmp_path / "nonexistent.json"
        args = parse_args(["--config", str(nonexistent), "Test"])

        with pytest.raises(SystemExit):
            validate_args(args)

        captured = capsys.readouterr()
        assert "not found" in captured.err
        print("✓ Nonexistent config file detected")

    def test_validate_invalid_timeout(self, capsys):
        """Test validation with invalid timeout."""
        args = parse_args(["--ollama-timeout", "0", "Test"])

        with pytest.raises(SystemExit):
            validate_args(args)

        captured = capsys.readouterr()
        assert "must be greater than 0" in captured.err
        print("✓ Invalid timeout detected")

    def test_validate_negative_timeout(self, capsys):
        """Test validation with negative timeout."""
        args = parse_args(["--ollama-timeout", "-1", "Test"])

        with pytest.raises(SystemExit):
            validate_args(args)

        captured = capsys.readouterr()
        assert "must be greater than 0" in captured.err
        print("✓ Negative timeout detected")
