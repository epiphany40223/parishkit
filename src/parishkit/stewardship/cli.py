"""Safe scaffold diagnostics using ParishKit's shared argument/YAML helpers."""

import json
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from parishkit.cli_arguments import add_common_arguments
from parishkit.config import ConfigError, load_yaml_config

from .arguments import StewardshipArgumentParser
from .deployment import DeploymentProfile, ServiceRole, load_deployment
from .services import healthcheck, prepare_development, run_service

_COMMAND_OPTIONS = {
    "config-check": {"config"},
    "validate-deployment": {
        "config",
        "profile",
        "service_role",
        "public_origin",
        "runtime_root",
    },
    "service": {"profile", "service_role", "bind_all_interfaces"},
    "healthcheck": set(),
    "prepare-development": {"runtime_root"},
}


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch explicit scaffold commands without disclosing configuration.

    Syntax validation is explicitly not deployment readiness. Do not configure
    Django, contact providers, or perform writes from syntax diagnostics.
    Service execution and new development-tree provisioning are explicit commands.
    """
    parser = StewardshipArgumentParser(
        "pk-stewardship",
        description="Stewardship application commands",
        error_hints={
            "command": "invalid command; choose " + ", ".join(_COMMAND_OPTIONS),
            "--profile": "--profile requires a valid value; choose "
            + ", ".join(DeploymentProfile),
            "--service-role": "--service-role requires a valid value; choose "
            + ", ".join(ServiceRole),
            "--config": "--config requires a configuration file path (value redacted)",
            "--public-origin": "--public-origin requires a URL (value redacted)",
            "--runtime-root": "--runtime-root requires a path (value redacted)",
            "--version": "--version does not accept a value",
            "--bind-all-interfaces": "--bind-all-interfaces does not accept a value",
        },
    )
    add_common_arguments(parser, options="config")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "command",
        nargs="?",
        choices=list(_COMMAND_OPTIONS),
    )
    parser.add_argument("--profile", choices=list(DeploymentProfile))
    parser.add_argument("--service-role", choices=list(ServiceRole))
    parser.add_argument("--public-origin")
    parser.add_argument("--runtime-root")
    parser.add_argument(
        "--bind-all-interfaces",
        action="store_true",
        default=None,
        help="development web only: listen on every interface (container opt-in)",
    )
    args = parser.parse_args(argv)
    supplied = {
        name
        for name, value in vars(args).items()
        if name not in {"command", "version"} and value is not None
    }
    if args.version:
        if args.command is not None or supplied:
            parser.usage_error("--version must be used alone")
        print(f"pk-stewardship {version('parishkit')}")
        return 0
    if args.command is None:
        if supplied:
            parser.usage_error("options require a command")
        parser.print_help()
        return 2
    unsupported = supplied - _COMMAND_OPTIONS[args.command]
    if unsupported:
        # Check before any filesystem, network, or process operation. Report
        # option names only: the values may contain credential paths or URLs.
        names = ", ".join("--" + name.replace("_", "-") for name in sorted(unsupported))
        parser.usage_error(f"options not supported by {args.command}: {names}")
    if args.command == "service":
        if args.bind_all_interfaces and (
            args.profile != DeploymentProfile.DEVELOPMENT
            or args.service_role != ServiceRole.WEB
        ):
            parser.usage_error(
                "--bind-all-interfaces requires --profile development "
                "and --service-role web"
            )
        return run_service(
            args.profile,
            args.service_role,
            bind_all_interfaces=args.bind_all_interfaces is True,
        )
    if args.command == "healthcheck":
        return healthcheck()
    if args.command == "prepare-development":
        if args.runtime_root is None:
            parser.usage_error(
                "prepare-development requires an explicit --runtime-root"
            )
        try:
            root = Path(args.runtime_root).expanduser().absolute()
        except (OSError, ValueError, RuntimeError):
            print(
                "ERROR: runtime path cannot be resolved; no existing data was replaced",
                file=sys.stderr,
            )
            return 2
        try:
            prepare_development(root)
        except OSError:
            print(
                "ERROR: use a new writable runtime directory with an existing parent; "
                "no existing data was replaced",
                file=sys.stderr,
            )
            return 2
        print("Created local scaffold storage; application bootstrap is still required")
        return 0
    if args.command == "validate-deployment":
        overrides = {
            key: value
            for key, value in {
                "PARISHKIT_STEWARDSHIP_PROFILE": args.profile,
                "PARISHKIT_STEWARDSHIP_SERVICE_ROLE": args.service_role,
                "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": args.public_origin,
                "PARISHKIT_ROOT": args.runtime_root,
            }.items()
            if value is not None
        }
        try:
            load_deployment(args.config, overrides=overrides)
        except ConfigError:
            print("ERROR: deployment configuration is invalid", file=sys.stderr)
            return 2
        print(json.dumps({"deployment_syntax_valid": True, "startup_validated": False}))
        return 0
    if args.config is None:
        parser.usage_error("config-check requires --config")
    try:
        load_yaml_config(args.config, required=True, reject_duplicate_keys=True)
    except (ConfigError, OSError, UnicodeError):
        # YAML parser errors can embed source lines containing secret values.
        print("ERROR: configuration must be a readable YAML mapping", file=sys.stderr)
        return 2
    print(json.dumps({"yaml_mapping_valid": True, "deployment_validated": False}))
    return 0
