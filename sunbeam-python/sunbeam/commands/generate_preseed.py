# Copyright (c) 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

import click
from rich.console import Console

import sunbeam.jobs.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.configure import (
    CLOUD_CONFIG_SECTION,
    ext_net_questions,
    ext_net_questions_local_only,
    user_questions,
)
from sunbeam.commands.juju import BOOTSTRAP_CONFIG_KEY, bootstrap_questions
from sunbeam.commands.microceph import microceph_questions
from sunbeam.commands.microk8s import (
    MICROK8S_ADDONS_CONFIG_KEY,
    microk8s_addons_questions,
)

LOG = logging.getLogger(__name__)
console = Console()


def show_questions(
    question_bank,
    section=None,
    subsection=None,
    section_description=None,
    comment_out=False,
):
    ident = ""
    if comment_out:
        comment = "# "
    else:
        comment = ""
    if section:
        if section_description:
            console.print(f"{comment}{ident}# {section_description}")
        console.print(f"{comment}{ident}{section}:")
        ident = "  "
    if subsection:
        console.print(f"{comment}{ident}{subsection}:")
        ident = "    "
    for key, question in question_bank.questions.items():
        default = question.calculate_default() or ""
        console.print(f"{comment}{ident}# {question.question}")
        console.print(f"{comment}{ident}{key}: {default}")


@click.command()
@click.pass_context
def generate_preseed(ctx: click.Context) -> None:
    """Generate preseed file."""
    name = utils.get_fqdn()
    client: Client = ctx.obj
    try:
        variables = sunbeam.jobs.questions.load_answers(client, BOOTSTRAP_CONFIG_KEY)
    except ClusterServiceUnavailableException:
        variables = {}
    bootstrap_bank = sunbeam.jobs.questions.QuestionBank(
        questions=bootstrap_questions(),
        console=console,
        previous_answers=variables.get("bootstrap", {}),
    )
    show_questions(bootstrap_bank, section="bootstrap")
    try:
        variables = sunbeam.jobs.questions.load_answers(
            client, MICROK8S_ADDONS_CONFIG_KEY
        )
    except ClusterServiceUnavailableException:
        variables = {}
    microk8s_addons_bank = sunbeam.jobs.questions.QuestionBank(
        questions=microk8s_addons_questions(),
        console=console,
        previous_answers=variables.get("addons", {}),
    )
    show_questions(microk8s_addons_bank, section="addons")
    user_bank = sunbeam.jobs.questions.QuestionBank(
        questions=user_questions(),
        console=console,
        previous_answers=variables.get("user"),
    )
    try:
        variables = sunbeam.jobs.questions.load_answers(client, CLOUD_CONFIG_SECTION)
    except ClusterServiceUnavailableException:
        variables = {}
    show_questions(user_bank, section="user")
    ext_net_bank_local = sunbeam.jobs.questions.QuestionBank(
        questions=ext_net_questions_local_only(),
        console=console,
        previous_answers=variables.get("external_network"),
    )
    show_questions(
        ext_net_bank_local,
        section="external_network",
        section_description="Local Access",
    )
    ext_net_bank_remote = sunbeam.jobs.questions.QuestionBank(
        questions=ext_net_questions(),
        console=console,
        previous_answers=variables.get("external_network"),
    )
    show_questions(
        ext_net_bank_remote,
        section="external_network",
        section_description="Remote Access",
        comment_out=True,
    )
    microceph_config_bank = sunbeam.jobs.questions.QuestionBank(
        questions=microceph_questions(),
        console=console,
        previous_answers=variables.get("microceph_config", {}).get(name),
    )
    show_questions(
        microceph_config_bank,
        section="microceph_config",
        subsection=name,
        section_description="MicroCeph config",
    )
