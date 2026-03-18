# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import typing

from rich.console import Console

from sunbeam.core.questions import QuestionBank, show_questions

OPENSTACK_MODEL = "openstack"
REGION_CONFIG_KEY = "Region"
DEFAULT_REGION = "RegionOne"
ENDPOINTS_CONFIG_KEY = "Endpoints"

INGRESS_ENDPOINT_TYPES = ["internal", "public", "rgw"]


INGRESS_ENDPOINT_TERRAFORM_MAP = {
    "internal": "traefik-config",
    "public": "traefik-public-config",
    "rgw": "traefik-rgw-config",
}


def get_ingress_endpoint_key(endpoint_type: str) -> str:
    """Get the config key for an ingress endpoint type."""
    return f"ingress-{endpoint_type}"


def generate_endpoint_preseed_questions(
    endpoint_questions_func: typing.Callable[[str], dict],
    console: Console,
    variables: dict,
) -> list[str]:
    """Generate preseed questions for endpoint configuration.

    Args:
        endpoint_questions_func: Function that takes endpoint type and returns questions
        console: Rich console for output
        variables: Previous answers/variables

    Returns:
        List of preseed content lines
    """
    configure_value = str(variables.get("configure", False)).lower()
    preseed_content = ["    endpoints:"]
    preseed_content.append(
        "      # Set to true to configure endpoint IP/hostname overrides"
    )
    preseed_content.append(f"      configure: {configure_value}")
    for endpoint in INGRESS_ENDPOINT_TYPES:
        questions = endpoint_questions_func(endpoint)
        questions = {
            k: v for k, v in questions.items() if not k.startswith("configure")
        }
        endpoint_bank = QuestionBank(
            questions=questions,
            console=console,
            previous_answers=variables.get(get_ingress_endpoint_key(endpoint), {}),
        )
        preseed_content.extend(
            show_questions(
                endpoint_bank,
                section=get_ingress_endpoint_key(endpoint),
                section_description=f"{endpoint.title()} Endpoint",
                initial_indent=6,
            )
        )

    return preseed_content
