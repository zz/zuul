#!/usr/bin/env python

# Copyright 2012 Hewlett-Packard Development Company, L.P.
# Copyright 2014 Wikimedia Foundation Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import os
import shutil
import time
from unittest import skip

import git

import zuul.lib.cloner

from tests.base import ZuulTestCase, simple_layout


class TestCloner(ZuulTestCase):
    tenant_config_file = 'config/single-tenant/main.yaml'

    log = logging.getLogger("zuul.test.cloner")

    def assertRepoState(self, repo, state, project, build, number):
        if 'branch' in state:
            self.assertFalse(repo.head.is_detached,
                             'Project %s commit for build %s #%s should '
                             'not have a detached HEAD' % (
                                 project, build, number))
            self.assertEquals(repo.active_branch.name,
                              state['branch'],
                              'Project %s commit for build %s #%s should '
                              'be on the correct branch' % (
                                  project, build, number))
        if 'commit' in state:
            self.assertEquals(state['commit'],
                              str(repo.commit('HEAD')),
                              'Project %s commit for build %s #%s should '
                              'be correct' % (
                                  project, build, number))
        ref = repo.commit('HEAD')
        repo_messages = set(
            [c.message.strip() for c in repo.iter_commits(ref)])
        if 'present' in state:
            for change in state['present']:
                msg = '%s-1' % change.subject
                self.assertTrue(msg in repo_messages,
                                'Project %s for build %s #%s should '
                                'have change %s' % (
                                    project, build, number, change.subject))
        if 'absent' in state:
            for change in state['absent']:
                msg = '%s-1' % change.subject
                self.assertTrue(msg not in repo_messages,
                                'Project %s for build %s #%s should '
                                'not have change %s' % (
                                    project, build, number, change.subject))

    @skip("Disabled for early v3 development")
    def test_cache_dir(self):
        projects = ['org/project1', 'org/project2']
        cache_root = os.path.join(self.test_root, "cache")
        for project in projects:
            upstream_repo_path = os.path.join(self.upstream_root, project)
            cache_repo_path = os.path.join(cache_root, project)
            git.Repo.clone_from(upstream_repo_path, cache_repo_path)

        self.worker.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        A.addApproval('CRVW', 2)
        self.fake_gerrit.addEvent(A.addApproval('APRV', 1))

        self.waitUntilSettled()

        self.assertEquals(1, len(self.builds), "One build is running")

        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'B')
        B.setMerged()

        upstream = self.getUpstreamRepos(projects)
        states = [{
            'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
            'org/project2': str(upstream['org/project2'].commit('master')),
        }]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            cloner = zuul.lib.cloner.Cloner(
                git_base_url=self.upstream_root,
                projects=projects,
                workspace=self.workspace_root,
                zuul_project=build.parameters.get('ZUUL_PROJECT', None),
                zuul_branch=build.parameters['ZUUL_BRANCH'],
                zuul_ref=build.parameters['ZUUL_REF'],
                zuul_url=self.src_root,
                cache_dir=cache_root,
            )
            cloner.execute()
            work = self.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertEquals(state[project],
                                  str(work[project].commit('HEAD')),
                                  'Project %s commit for build %s should '
                                  'be correct' % (project, number))

        work = self.getWorkspaceRepos(projects)
        # project1 is the zuul_project so the origin should be set to the
        # zuul_url since that is the most up to date.
        cache_repo_path = os.path.join(cache_root, 'org/project1')
        self.assertNotEqual(
            work['org/project1'].remotes.origin.url,
            cache_repo_path,
            'workspace repo origin should not be the cache'
        )
        zuul_url_repo_path = os.path.join(self.git_root, 'org/project1')
        self.assertEqual(
            work['org/project1'].remotes.origin.url,
            zuul_url_repo_path,
            'workspace repo origin should be the zuul url'
        )

        # project2 is not the zuul_project so the origin should be set
        # to upstream since that is the best we can do
        cache_repo_path = os.path.join(cache_root, 'org/project2')
        self.assertNotEqual(
            work['org/project2'].remotes.origin.url,
            cache_repo_path,
            'workspace repo origin should not be the cache'
        )
        upstream_repo_path = os.path.join(self.upstream_root, 'org/project2')
        self.assertEqual(
            work['org/project2'].remotes.origin.url,
            upstream_repo_path,
            'workspace repo origin should be the upstream url'
        )

        self.worker.hold_jobs_in_build = False
        self.worker.release()
        self.waitUntilSettled()

    @simple_layout('layouts/repo-checkout-two-project.yaml')
    def test_one_branch(self):
        self.executor_server.hold_jobs_in_build = True

        p1 = 'review.example.com/org/project1'
        p2 = 'review.example.com/org/project2'
        projects = [p1, p2]
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'B')
        A.addApproval('code-review', 2)
        B.addApproval('code-review', 2)
        self.fake_gerrit.addEvent(A.addApproval('approved', 1))
        self.fake_gerrit.addEvent(B.addApproval('approved', 1))

        self.waitUntilSettled()

        self.assertEquals(2, len(self.builds), "Two builds are running")

        upstream = self.getUpstreamRepos(projects)
        states = [
            {p1: dict(present=[A], absent=[B], branch='master'),
             p2: dict(commit=str(upstream[p2].commit('master')),
                      branch='master'),
             },
            {p1: dict(present=[A], absent=[B], branch='master'),
             p2: dict(present=[B], absent=[A], branch='master'),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            work = build.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertRepoState(work[project], state[project],
                                     project, build, number)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    @simple_layout('layouts/repo-checkout-four-project.yaml')
    def test_multi_branch(self):
        self.executor_server.hold_jobs_in_build = True

        p1 = 'review.example.com/org/project1'
        p2 = 'review.example.com/org/project2'
        p3 = 'review.example.com/org/project3'
        p4 = 'review.example.com/org/project4'
        projects = [p1, p2, p3, p4]

        self.create_branch('org/project2', 'stable/havana')
        self.create_branch('org/project4', 'stable/havana')
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project2', 'stable/havana',
                                           'B')
        C = self.fake_gerrit.addFakeChange('org/project3', 'master', 'C')
        A.addApproval('code-review', 2)
        B.addApproval('code-review', 2)
        C.addApproval('code-review', 2)
        self.fake_gerrit.addEvent(A.addApproval('approved', 1))
        self.fake_gerrit.addEvent(B.addApproval('approved', 1))
        self.fake_gerrit.addEvent(C.addApproval('approved', 1))

        self.waitUntilSettled()

        self.assertEquals(3, len(self.builds), "Three builds are running")

        upstream = self.getUpstreamRepos(projects)
        states = [
            {p1: dict(present=[A], absent=[B, C], branch='master'),
             p2: dict(commit=str(upstream[p2].commit('master')),
                      branch='master'),
             p3: dict(commit=str(upstream[p3].commit('master')),
                      branch='master'),
             p4: dict(commit=str(upstream[p4].commit('master')),
                      branch='master'),
             },
            {p1: dict(present=[A], absent=[B, C], branch='master'),
             p2: dict(present=[B], absent=[A, C], branch='stable/havana'),
             p3: dict(commit=str(upstream[p3].commit('master')),
                      branch='master'),
             p4: dict(commit=str(upstream[p4].commit('stable/havana')),
                      branch='stable/havana'),
             },
            {p1: dict(present=[A], absent=[B, C], branch='master'),
             p2: dict(commit=str(upstream[p2].commit('master')),
                      branch='master'),
             p3: dict(present=[C], absent=[A, B], branch='master'),
             p4: dict(commit=str(upstream[p4].commit('master')),
                      branch='master'),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            work = build.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertRepoState(work[project], state[project],
                                     project, build, number)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    @skip("Disabled for early v3 development")
    def test_upgrade(self):
        # Simulates an upgrade test
        self.worker.hold_jobs_in_build = True
        projects = ['org/project1', 'org/project2', 'org/project3',
                    'org/project4', 'org/project5', 'org/project6']

        self.create_branch('org/project2', 'stable/havana')
        self.create_branch('org/project3', 'stable/havana')
        self.create_branch('org/project4', 'stable/havana')
        self.create_branch('org/project5', 'stable/havana')
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'B')
        C = self.fake_gerrit.addFakeChange('org/project3', 'stable/havana',
                                           'C')
        D = self.fake_gerrit.addFakeChange('org/project3', 'master', 'D')
        E = self.fake_gerrit.addFakeChange('org/project4', 'stable/havana',
                                           'E')
        A.addApproval('CRVW', 2)
        B.addApproval('CRVW', 2)
        C.addApproval('CRVW', 2)
        D.addApproval('CRVW', 2)
        E.addApproval('CRVW', 2)
        self.fake_gerrit.addEvent(A.addApproval('APRV', 1))
        self.fake_gerrit.addEvent(B.addApproval('APRV', 1))
        self.fake_gerrit.addEvent(C.addApproval('APRV', 1))
        self.fake_gerrit.addEvent(D.addApproval('APRV', 1))
        self.fake_gerrit.addEvent(E.addApproval('APRV', 1))

        self.waitUntilSettled()

        self.assertEquals(5, len(self.builds), "Five builds are running")

        # Check the old side of the upgrade first
        upstream = self.getUpstreamRepos(projects)
        states = [
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': str(upstream['org/project2'].commit(
                                 'stable/havana')),
             'org/project3': str(upstream['org/project3'].commit(
                                 'stable/havana')),
             'org/project4': str(upstream['org/project4'].commit(
                                 'stable/havana')),
             'org/project5': str(upstream['org/project5'].commit(
                                 'stable/havana')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': str(upstream['org/project2'].commit(
                                 'stable/havana')),
             'org/project3': str(upstream['org/project3'].commit(
                                 'stable/havana')),
             'org/project4': str(upstream['org/project4'].commit(
                                 'stable/havana')),
             'org/project5': str(upstream['org/project5'].commit(
                                 'stable/havana')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': str(upstream['org/project2'].commit(
                                 'stable/havana')),
             'org/project3': self.builds[2].parameters['ZUUL_COMMIT'],
             'org/project4': str(upstream['org/project4'].commit(
                                 'stable/havana')),

             'org/project5': str(upstream['org/project5'].commit(
                                 'stable/havana')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': str(upstream['org/project2'].commit(
                                 'stable/havana')),
             'org/project3': self.builds[2].parameters['ZUUL_COMMIT'],
             'org/project4': str(upstream['org/project4'].commit(
                                 'stable/havana')),
             'org/project5': str(upstream['org/project5'].commit(
                                 'stable/havana')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': str(upstream['org/project2'].commit(
                                 'stable/havana')),
             'org/project3': self.builds[2].parameters['ZUUL_COMMIT'],
             'org/project4': self.builds[4].parameters['ZUUL_COMMIT'],
             'org/project5': str(upstream['org/project5'].commit(
                                 'stable/havana')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            cloner = zuul.lib.cloner.Cloner(
                git_base_url=self.upstream_root,
                projects=projects,
                workspace=self.workspace_root,
                zuul_project=build.parameters.get('ZUUL_PROJECT', None),
                zuul_branch=build.parameters['ZUUL_BRANCH'],
                zuul_ref=build.parameters['ZUUL_REF'],
                zuul_url=self.src_root,
                branch='stable/havana',  # Old branch for upgrade
            )
            cloner.execute()
            work = self.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertEquals(state[project],
                                  str(work[project].commit('HEAD')),
                                  'Project %s commit for build %s should '
                                  'be correct on old side of upgrade' %
                                  (project, number))
            shutil.rmtree(self.workspace_root)

        # Check the new side of the upgrade
        states = [
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': str(upstream['org/project2'].commit('master')),
             'org/project3': str(upstream['org/project3'].commit('master')),
             'org/project4': str(upstream['org/project4'].commit('master')),
             'org/project5': str(upstream['org/project5'].commit('master')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': self.builds[1].parameters['ZUUL_COMMIT'],
             'org/project3': str(upstream['org/project3'].commit('master')),
             'org/project4': str(upstream['org/project4'].commit('master')),
             'org/project5': str(upstream['org/project5'].commit('master')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': self.builds[1].parameters['ZUUL_COMMIT'],
             'org/project3': str(upstream['org/project3'].commit('master')),
             'org/project4': str(upstream['org/project4'].commit('master')),
             'org/project5': str(upstream['org/project5'].commit('master')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': self.builds[1].parameters['ZUUL_COMMIT'],
             'org/project3': self.builds[3].parameters['ZUUL_COMMIT'],
             'org/project4': str(upstream['org/project4'].commit('master')),
             'org/project5': str(upstream['org/project5'].commit('master')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
            {'org/project1': self.builds[0].parameters['ZUUL_COMMIT'],
             'org/project2': self.builds[1].parameters['ZUUL_COMMIT'],
             'org/project3': self.builds[3].parameters['ZUUL_COMMIT'],
             'org/project4': str(upstream['org/project4'].commit('master')),
             'org/project5': str(upstream['org/project5'].commit('master')),
             'org/project6': str(upstream['org/project6'].commit('master')),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            cloner = zuul.lib.cloner.Cloner(
                git_base_url=self.upstream_root,
                projects=projects,
                workspace=self.workspace_root,
                zuul_project=build.parameters.get('ZUUL_PROJECT', None),
                zuul_branch=build.parameters['ZUUL_BRANCH'],
                zuul_ref=build.parameters['ZUUL_REF'],
                zuul_url=self.src_root,
                branch='master',  # New branch for upgrade
            )
            cloner.execute()
            work = self.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertEquals(state[project],
                                  str(work[project].commit('HEAD')),
                                  'Project %s commit for build %s should '
                                  'be correct on old side of upgrade' %
                                  (project, number))
            shutil.rmtree(self.workspace_root)

        self.worker.hold_jobs_in_build = False
        self.worker.release()
        self.waitUntilSettled()

    @simple_layout('layouts/repo-checkout-six-project.yaml')
    def test_project_override(self):
        self.executor_server.hold_jobs_in_build = True

        p1 = 'review.example.com/org/project1'
        p2 = 'review.example.com/org/project2'
        p3 = 'review.example.com/org/project3'
        p4 = 'review.example.com/org/project4'
        p5 = 'review.example.com/org/project5'
        p6 = 'review.example.com/org/project6'
        projects = [p1, p2, p3, p4, p5, p6]

        self.create_branch('org/project3', 'stable/havana')
        self.create_branch('org/project4', 'stable/havana')
        self.create_branch('org/project6', 'stable/havana')
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B')
        C = self.fake_gerrit.addFakeChange('org/project2', 'master', 'C')
        D = self.fake_gerrit.addFakeChange('org/project3', 'stable/havana',
                                           'D')
        A.addApproval('code-review', 2)
        B.addApproval('code-review', 2)
        C.addApproval('code-review', 2)
        D.addApproval('code-review', 2)
        self.fake_gerrit.addEvent(A.addApproval('approved', 1))
        self.fake_gerrit.addEvent(B.addApproval('approved', 1))
        self.fake_gerrit.addEvent(C.addApproval('approved', 1))
        self.fake_gerrit.addEvent(D.addApproval('approved', 1))

        self.waitUntilSettled()

        self.assertEquals(4, len(self.builds), "Four builds are running")

        upstream = self.getUpstreamRepos(projects)
        states = [
            {p1: dict(present=[A], absent=[B, C, D], branch='master'),
             p2: dict(commit=str(upstream[p2].commit('master')),
                      branch='master'),
             p3: dict(commit=str(upstream[p3].commit('master')),
                      branch='master'),
             p4: dict(commit=str(upstream[p4].commit('master')),
                      branch='master'),
             p5: dict(commit=str(upstream[p5].commit('master')),
                      branch='master'),
             p6: dict(commit=str(upstream[p6].commit('master')),
                      branch='master'),
             },
            {p1: dict(present=[A, B], absent=[C, D], branch='master'),
             p2: dict(commit=str(upstream[p2].commit('master')),
                      branch='master'),
             p3: dict(commit=str(upstream[p3].commit('master')),
                      branch='master'),
             p4: dict(commit=str(upstream[p4].commit('master')),
                      branch='master'),
             p5: dict(commit=str(upstream[p5].commit('master')),
                      branch='master'),
             p6: dict(commit=str(upstream[p6].commit('master')),
                      branch='master'),
             },
            {p1: dict(present=[A, B], absent=[C, D], branch='master'),
             p2: dict(present=[C], absent=[A, B, D], branch='master'),
             p3: dict(commit=str(upstream[p3].commit('master')),
                      branch='master'),
             p4: dict(commit=str(upstream[p4].commit('master')),
                      branch='master'),
             p5: dict(commit=str(upstream[p5].commit('master')),
                      branch='master'),
             p6: dict(commit=str(upstream[p6].commit('master')),
                      branch='master'),
             },
            {p1: dict(present=[A, B], absent=[C, D], branch='master'),
             p2: dict(present=[C], absent=[A, B, D], branch='master'),
             p3: dict(present=[D], absent=[A, B, C],
                      branch='stable/havana'),
             p4: dict(commit=str(upstream[p4].commit('master')),
                      branch='master'),
             p5: dict(commit=str(upstream[p5].commit('master')),
                      branch='master'),
             p6: dict(commit=str(upstream[p6].commit('stable/havana')),
                      branch='stable/havana'),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            work = build.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertRepoState(work[project], state[project],
                                     project, build, number)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    def test_periodic(self):
        # This test can not use simple_layout because it must start
        # with a configuration which does not include a
        # timer-triggered job so that we have an opportunity to set
        # the hold flag before the first job.
        self.executor_server.hold_jobs_in_build = True
        # Start timer trigger - also org/project
        self.commitConfigUpdate('common-config',
                                'layouts/repo-checkout-timer.yaml')
        self.sched.reconfigure(self.config)

        p1 = 'review.example.com/org/project1'
        projects = [p1]
        self.create_branch('org/project1', 'stable/havana')

        # The pipeline triggers every second, so we should have seen
        # several by now.
        time.sleep(5)
        self.waitUntilSettled()

        # Stop queuing timer triggered jobs so that the assertions
        # below don't race against more jobs being queued.
        self.commitConfigUpdate('common-config',
                                'layouts/repo-checkout-no-timer.yaml')
        self.sched.reconfigure(self.config)

        self.assertEquals(1, len(self.builds), "One build is running")

        upstream = self.getUpstreamRepos(projects)
        states = [
            {p1: dict(commit=str(upstream[p1].commit('stable/havana')),
                      branch='stable/havana'),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            work = build.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertRepoState(work[project], state[project],
                                     project, build, number)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    @skip("Disabled for early v3 development")
    def test_periodic_update(self):
        # Test that the merger correctly updates its local repository
        # before running a periodic job.

        # Prime the merger with the current state
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # Merge a different change
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')
        B.setMerged()

        # Start a periodic job
        self.worker.hold_jobs_in_build = True
        self.executor.negative_function_cache_ttl = 0
        self.config.set('zuul', 'layout_config',
                        'tests/fixtures/layout-timer.yaml')
        self.sched.reconfigure(self.config)
        self.registerJobs()

        # The pipeline triggers every second, so we should have seen
        # several by now.
        time.sleep(5)
        self.waitUntilSettled()

        builds = self.builds[:]

        # Stop queuing timer triggered jobs so that the assertions
        # below don't race against more jobs being queued.
        self.config.set('zuul', 'layout_config',
                        'tests/fixtures/layout-no-timer.yaml')
        self.sched.reconfigure(self.config)
        self.registerJobs()
        self.worker.release()
        self.waitUntilSettled()

        projects = ['org/project']

        self.assertEquals(2, len(builds), "Two builds are running")

        upstream = self.getUpstreamRepos(projects)
        self.assertEqual(upstream['org/project'].commit('master').hexsha,
                         B.patchsets[0]['revision'])
        states = [
            {'org/project':
                str(upstream['org/project'].commit('master')),
             },
            {'org/project':
                str(upstream['org/project'].commit('master')),
             },
        ]

        for number, build in enumerate(builds):
            self.log.debug("Build parameters: %s", build.parameters)
            cloner = zuul.lib.cloner.Cloner(
                git_base_url=self.upstream_root,
                projects=projects,
                workspace=self.workspace_root,
                zuul_project=build.parameters.get('ZUUL_PROJECT', None),
                zuul_branch=build.parameters.get('ZUUL_BRANCH', None),
                zuul_ref=build.parameters.get('ZUUL_REF', None),
                zuul_url=self.git_root,
            )
            cloner.execute()
            work = self.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertEquals(state[project],
                                  str(work[project].commit('HEAD')),
                                  'Project %s commit for build %s should '
                                  'be correct' % (project, number))

            shutil.rmtree(self.workspace_root)

        self.worker.hold_jobs_in_build = False
        self.worker.release()
        self.waitUntilSettled()

    @simple_layout('layouts/repo-checkout-post.yaml')
    def test_post_checkout(self):
        self.executor_server.hold_jobs_in_build = True
        p1 = "review.example.com/org/project1"
        projects = [p1]

        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        event = A.getRefUpdatedEvent()
        A.setMerged()
        self.fake_gerrit.addEvent(event)
        self.waitUntilSettled()

        upstream = self.getUpstreamRepos(projects)
        states = [
            {p1: dict(commit=str(upstream[p1].commit('master')),
                      present=[A], branch='master'),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            work = build.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertRepoState(work[project], state[project],
                                     project, build, number)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    @simple_layout('layouts/repo-checkout-post.yaml')
    def test_post_and_master_checkout(self):
        self.executor_server.hold_jobs_in_build = True
        p1 = "review.example.com/org/project1"
        p2 = "review.example.com/org/project2"
        projects = [p1, p2]

        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        event = A.getRefUpdatedEvent()
        A.setMerged()
        self.fake_gerrit.addEvent(event)
        self.waitUntilSettled()

        upstream = self.getUpstreamRepos(projects)
        states = [
            {p1: dict(commit=str(upstream[p1].commit('master')),
                      present=[A], branch='master'),
             p2: dict(commit=str(upstream[p2].commit('master')),
                      absent=[A], branch='master'),
             },
        ]

        for number, build in enumerate(self.builds):
            self.log.debug("Build parameters: %s", build.parameters)
            work = build.getWorkspaceRepos(projects)
            state = states[number]

            for project in projects:
                self.assertRepoState(work[project], state[project],
                                     project, build, number)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()
