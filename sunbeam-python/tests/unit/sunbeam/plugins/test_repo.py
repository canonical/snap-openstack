# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import Mock, patch

import pytest

import sunbeam.plugins.repo.plugin as repo_plugin
from sunbeam.jobs.common import ResultType


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def externalrepo():
    with patch("sunbeam.plugins.repo.plugin.ExternalRepo") as p:
        yield p


@pytest.fixture()
def repoplugin():
    with patch("sunbeam.plugins.repo.plugin.RepoPlugin") as p:
        yield p


@pytest.fixture()
def gitrepo():
    with patch("sunbeam.plugins.repo.plugin.Repo") as p:
        yield p


@pytest.fixture()
def pluginmanager():
    with patch("sunbeam.plugins.repo.plugin.PluginManager") as p:
        yield p


class TestAddPluginRepoStep:
    def test_run(self, cclient, externalrepo, repoplugin):
        step = repo_plugin.AddPluginRepoStep(externalrepo, repoplugin)
        result = step.run()

        externalrepo.clone_repo.assert_called_once()
        externalrepo.validate_repo.assert_called_once()
        repoplugin.get_plugin_info.assert_called_once()
        repoplugin.update_plugin_info.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_plugin_yaml_validation_failed(
        self, cclient, externalrepo, repoplugin
    ):
        externalrepo.validate_repo.side_effect = repo_plugin.PluginYamlFormatException()
        step = repo_plugin.AddPluginRepoStep(externalrepo, repoplugin)
        result = step.run()

        externalrepo.clone_repo.assert_called_once()
        externalrepo.validate_repo.assert_called_once()
        repoplugin.get_plugin_info.assert_not_called()
        repoplugin.update_plugin_info.assert_not_called()
        assert result.result_type == ResultType.FAILED


class TestRemovePluginRepoStep:
    def test_run(self, cclient, pluginmanager, repoplugin, tmp_path):
        repo_name = "TEST_REPO"
        pluginmanager.enabled_plugins.return_value = []
        repoplugin.get_plugin_info.return_value = {
            "version": "0.0.1",
            "repos": [
                {
                    "name": repo_name,
                    "git_repo": "http://test.git.repo",
                    "git_branch": "main",
                }
            ],
        }
        step = repo_plugin.RemovePluginRepoStep(repo_name, tmp_path, repoplugin)
        result = step.run()

        repoplugin.get_plugin_info.assert_called_once()
        repoplugin.update_plugin_info.assert_called_once_with(
            {"version": "0.0.1", "repos": []}
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_plugins_enabled(
        self, cclient, pluginmanager, repoplugin, tmp_path
    ):
        repo_name = "TEST_REPO"
        pluginmanager.enabled_plugins.return_value = ["TEST_PLUGIN"]
        repoplugin.get_plugin_info.return_value = {
            "version": "0.0.1",
            "repos": [
                {
                    "name": repo_name,
                    "git_repo": "http://test.git.repo",
                    "git_branch": "main",
                }
            ],
        }
        step = repo_plugin.RemovePluginRepoStep(repo_name, tmp_path, repoplugin)
        result = step.run()

        repoplugin.get_plugin_info.assert_not_called()
        repoplugin.update_plugin_info.assert_not_called()
        assert result.result_type == ResultType.FAILED

    def test_run_when_repo_not_in_clusterdb(
        self, cclient, pluginmanager, repoplugin, tmp_path
    ):
        repo_name = "UNKNOWN_REPO"
        pluginmanager.enabled_plugins.return_value = []
        repoplugin.get_plugin_info.return_value = {
            "version": "0.0.1",
            "repos": [
                {
                    "name": "repo1",
                    "git_repo": "http://test.git.repo",
                    "git_branch": "main",
                }
            ],
        }
        step = repo_plugin.RemovePluginRepoStep(repo_name, tmp_path, repoplugin)
        result = step.run()

        repoplugin.get_plugin_info.assert_called_once()
        repoplugin.update_plugin_info.assert_not_called()
        assert result.result_type == ResultType.FAILED


class TestUpdatePluginRepoStep:
    def test_run(self, cclient, externalrepo, repoplugin, pluginmanager):
        repo_name = "TEST_REPO"
        pluginmanager.get_all_external_repos.return_value = [repo_name]
        externalrepo.name = repo_name
        step = repo_plugin.UpdatePluginRepoStep(cclient, externalrepo, repoplugin)
        result = step.run()

        externalrepo.repo.git.rev_parse.assert_called_once()
        externalrepo.repo.git.pull.assert_called_once()
        externalrepo.validate_repo.assert_called_once()
        pluginmanager.update_plugins.assert_called_once_with([repo_name])
        repoplugin.update_plugin_info.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_repo_not_in_clusterdb(
        self, cclient, externalrepo, repoplugin, pluginmanager
    ):
        pluginmanager.get_all_external_repos.return_value = ["TEST_REPO"]
        externalrepo.name = "UNKNOWN_REPO"
        step = repo_plugin.UpdatePluginRepoStep(cclient, externalrepo, repoplugin)
        result = step.run()

        externalrepo.repo.git.rev_parse.assert_not_called()
        externalrepo.repo.git.pull.assert_not_called()
        externalrepo.validate_repo.assert_not_called()
        pluginmanager.update_plugins.assert_not_called()
        repoplugin.update_plugin_info.assert_not_called()
        assert result.result_type == ResultType.FAILED

    def test_run_when_plugin_yaml_validation_failed(
        self, cclient, externalrepo, repoplugin, pluginmanager
    ):
        repo_name = "TEST_REPO"
        commit_id = "CURRENT_COMMIT_ID"
        externalrepo.validate_repo.side_effect = repo_plugin.PluginYamlFormatException()
        pluginmanager.get_all_external_repos.return_value = [repo_name]
        externalrepo.name = repo_name
        externalrepo.repo.git.rev_parse.return_value = commit_id
        step = repo_plugin.UpdatePluginRepoStep(cclient, externalrepo, repoplugin)
        result = step.run()

        externalrepo.repo.git.rev_parse.assert_called_once()
        externalrepo.repo.git.pull.assert_called_once()
        externalrepo.validate_repo.assert_called_once()
        externalrepo.repo.git.reset.assert_called_once_with(commit_id, hard=True)
        pluginmanager.update_plugins.assert_not_called()
        repoplugin.update_plugin_info.assert_not_called()
        assert result.result_type == ResultType.FAILED
