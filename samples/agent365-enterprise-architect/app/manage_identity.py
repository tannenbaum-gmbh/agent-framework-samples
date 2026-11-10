# Copyright (c) Microsoft. All rights reserved.
"""Create and manage the Entra agent identity blueprint and agent identities for this sample."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from microsoft_agents_a365.runtime import Utility

from .agent_token import AgentIdentityTokenProvider
from .config import IdentitySettings, get_identity_settings
from .graph import AgentIdentityGraphClient, GraphTokenProvider

GRAPH_RESOURCE = "https://graph.microsoft.com"
AUTH_RECORD = Path.home() / ".azure" / "agent365-identity-sample.auth-record.json"
CACHE_NAME = "agent365-identity-sample"


def user_token_provider(settings: IdentitySettings) -> GraphTokenProvider:
    """Delegated Graph tokens for the signed-in administrator via device code sign-in.

    The token cache and authentication record are persisted so that each command only
    prompts when new scopes need consent.
    """
    from azure.identity import AuthenticationRecord, DeviceCodeCredential, TokenCachePersistenceOptions

    record = AuthenticationRecord.deserialize(AUTH_RECORD.read_text()) if AUTH_RECORD.exists() else None
    credential = DeviceCodeCredential(
        client_id=settings.graph_client_id,
        tenant_id=settings.tenant_id,
        authentication_record=record,
        cache_persistence_options=TokenCachePersistenceOptions(name=CACHE_NAME, allow_unencrypted_storage=True),
    )

    def provide(scopes: list[str]) -> str:
        qualified = [f"{GRAPH_RESOURCE}/{scope}" for scope in scopes]
        if not AUTH_RECORD.exists():
            AUTH_RECORD.parent.mkdir(parents=True, exist_ok=True)
            AUTH_RECORD.write_text(credential.authenticate(scopes=qualified).serialize())
        return credential.get_token(*qualified).token

    return provide


def blueprint_token_provider(settings: IdentitySettings) -> GraphTokenProvider:
    """App-only Graph tokens for the blueprint, which manages the agent identities it parents."""
    provider = AgentIdentityTokenProvider.from_settings(settings)
    return lambda scopes: provider.blueprint_token()


def _require(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"error: {name} is required (pass it as an argument or set the environment variable)")
    return value


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _sponsor(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> str:
    if args.sponsor_id or settings.sponsor_id:
        return args.sponsor_id or settings.sponsor_id
    if args.auth == "blueprint":
        raise SystemExit("error: --sponsor-id or AGENT_SPONSOR_ID is required with --auth blueprint")
    return graph.me()["id"]


# Blueprint commands -----------------------------------------------------------


def blueprint_create(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    me = graph.me()
    result = graph.create_blueprint(args.name, sponsor_id=args.sponsor_id or me["id"], owner_id=me["id"])
    _print(
        {
            "blueprintAppId": result["blueprint"]["appId"],
            "blueprintObjectId": result["blueprint"]["id"],
            "blueprintPrincipalId": result["principal"]["id"],
        }
    )
    print(f"\nSet AGENT_BLUEPRINT_APP_ID={result['blueprint']['appId']}", file=sys.stderr)


def blueprint_show(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    _print(graph.get_blueprint(_require(settings.blueprint_app_id, "AGENT_BLUEPRINT_APP_ID")))


def blueprint_add_secret(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    credential = graph.add_blueprint_secret(
        _require(settings.blueprint_app_id, "AGENT_BLUEPRINT_APP_ID"), args.display_name, args.days
    )
    _print({"keyId": credential["keyId"], "endDateTime": credential["endDateTime"]})
    print(
        "\nThe secret is shown only once. Store it in AGENT_BLUEPRINT_CLIENT_SECRET (local development only):\n"
        f"{credential['secretText']}",
        file=sys.stderr,
    )


def blueprint_add_managed_identity(
    args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient
) -> None:
    _print(
        graph.add_blueprint_managed_identity(
            _require(settings.blueprint_app_id, "AGENT_BLUEPRINT_APP_ID"),
            settings.tenant_id,
            args.principal_id,
            args.name,
        )
    )


def blueprint_delete(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    blueprint_app_id = _require(settings.blueprint_app_id, "AGENT_BLUEPRINT_APP_ID")
    if not args.yes:
        raise SystemExit("error: deleting a blueprint also deletes all its agent identities; pass --yes to confirm")
    graph.delete_blueprint(blueprint_app_id)
    print(f"Deleted blueprint {blueprint_app_id} and its agent identities.")


# Agent identity commands ------------------------------------------------------


def identity_create(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    blueprint_app_id = _require(settings.blueprint_app_id, "AGENT_BLUEPRINT_APP_ID")
    identity = graph.create_agent_identity(blueprint_app_id, args.name, _sponsor(args, settings, graph))
    _print({"agentIdentityId": identity["id"], "agentIdentityClientId": identity["appId"]})
    print(
        f"\nSet AGENT_IDENTITY_ID={identity['id']} and AGENT_IDENTITY_CLIENT_ID={identity['appId']}",
        file=sys.stderr,
    )


def identity_list(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    blueprint_app_id = None if args.all else _require(settings.blueprint_app_id, "AGENT_BLUEPRINT_APP_ID")
    _print(graph.list_agent_identities(blueprint_app_id))


def identity_show(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    _print(graph.get_agent_identity(_identity_id(args, settings)))


def identity_enable(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    identity_id = _identity_id(args, settings)
    graph.set_agent_identity_enabled(identity_id, True)
    print(f"Enabled agent identity {identity_id}.")


def identity_disable(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    identity_id = _identity_id(args, settings)
    graph.set_agent_identity_enabled(identity_id, False)
    print(f"Disabled agent identity {identity_id}. New token requests for it now fail.")


def identity_delete(args: argparse.Namespace, settings: IdentitySettings, graph: AgentIdentityGraphClient) -> None:
    identity_id = _identity_id(args, settings)
    if not args.yes:
        raise SystemExit("error: pass --yes to confirm deleting the agent identity")
    graph.delete_agent_identity(identity_id)
    print(f"Deleted agent identity {identity_id}.")


def identity_whoami(args: argparse.Namespace, settings: IdentitySettings) -> None:
    """Acquire a token as the agent identity and let the Agent 365 SDK resolve who it is."""
    client_id = _require(args.client_id or settings.agent_identity_client_id, "AGENT_IDENTITY_CLIENT_ID")
    provider = AgentIdentityTokenProvider.from_settings(settings)
    token, expires_on = provider.agent_identity_token(client_id, args.scope)
    _print(
        {
            "agentIdentity": Utility.get_app_id_from_token(token),
            "blueprint": Utility.get_agent_id_from_token(token),
            "scope": args.scope,
            "expiresOn": expires_on,
        }
    )


def _identity_id(args: argparse.Namespace, settings: IdentitySettings) -> str:
    return _require(args.id or settings.agent_identity_id, "AGENT_IDENTITY_ID")


Handler = Callable[[argparse.Namespace, IdentitySettings, AgentIdentityGraphClient], None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.manage_identity", description=__doc__)
    groups = parser.add_subparsers(dest="group", required=True)

    blueprint = groups.add_parser("blueprint", help="Manage the agent identity blueprint").add_subparsers(
        dest="command", required=True
    )
    cmd = blueprint.add_parser("create", help="Create a blueprint and its blueprint principal")
    cmd.add_argument("--name", required=True)
    cmd.add_argument("--sponsor-id", help="User object ID of the sponsor. Defaults to the signed-in user.")
    cmd.set_defaults(handler=blueprint_create)
    blueprint.add_parser("show", help="Show the configured blueprint").set_defaults(handler=blueprint_show)
    cmd = blueprint.add_parser("add-secret", help="Add a client secret (local development only)")
    cmd.add_argument("--display-name", default="agent365-enterprise-architect local development")
    cmd.add_argument("--days", type=int, default=30)
    cmd.set_defaults(handler=blueprint_add_secret)
    cmd = blueprint.add_parser("add-managed-identity", help="Trust a managed identity as the blueprint credential")
    cmd.add_argument("--principal-id", required=True, help="Object (principal) ID of the managed identity")
    cmd.add_argument("--name", default="agent365-enterprise-architect-mi")
    cmd.set_defaults(handler=blueprint_add_managed_identity)
    cmd = blueprint.add_parser("delete", help="Delete the blueprint and all of its agent identities")
    cmd.add_argument("--yes", action="store_true")
    cmd.set_defaults(handler=blueprint_delete)

    identity = groups.add_parser("identity", help="Manage agent identities").add_subparsers(
        dest="command", required=True
    )

    def add(name: str, handler: Handler, help_text: str, *, with_id: bool = True) -> argparse.ArgumentParser:
        sub = identity.add_parser(name, help=help_text)
        sub.add_argument(
            "--auth",
            choices=["user", "blueprint"],
            default="user",
            help="Call Graph as the signed-in user (default) or app-only as the blueprint.",
        )
        if with_id:
            sub.add_argument("--id", help="Agent identity object ID. Defaults to AGENT_IDENTITY_ID.")
        sub.set_defaults(handler=handler)
        return sub

    cmd = add("create", identity_create, "Create an agent identity from the blueprint", with_id=False)
    cmd.add_argument("--name", required=True)
    cmd.add_argument("--sponsor-id", help="User object ID of the sponsor. Defaults to the signed-in user.")
    cmd = add("list", identity_list, "List agent identities created from the blueprint", with_id=False)
    cmd.add_argument("--all", action="store_true", help="List agent identities from every blueprint")
    add("show", identity_show, "Show an agent identity")
    add("enable", identity_enable, "Enable an agent identity")
    add("disable", identity_disable, "Disable an agent identity so it can no longer get tokens")
    cmd = add("delete", identity_delete, "Delete an agent identity")
    cmd.add_argument("--yes", action="store_true")
    cmd = identity.add_parser("whoami", help="Get a token as the agent identity and inspect it with the A365 SDK")
    cmd.add_argument("--client-id", help="Agent identity client ID. Defaults to AGENT_IDENTITY_CLIENT_ID.")
    cmd.add_argument("--scope", default="https://graph.microsoft.com/.default")
    cmd.set_defaults(handler=identity_whoami, needs_graph=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_identity_settings()
    if getattr(args, "needs_graph", True) is False:
        args.handler(args, settings)
        return 0
    if getattr(args, "auth", "user") == "blueprint":
        token_provider = blueprint_token_provider(settings)
    else:
        token_provider = user_token_provider(settings)
    with AgentIdentityGraphClient(token_provider) as graph:
        args.handler(args, settings, graph)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
