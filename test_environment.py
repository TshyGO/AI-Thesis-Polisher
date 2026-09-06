"""Startup environment checks. Injectable, so these run on any platform."""
import sys
import unittest

from engine.environment import (blocking_problems, environment_report,
                                registered_progid, WORD_PROGID)


class EnvironmentReportTests(unittest.TestCase):
    def test_non_windows_reports_one_blocking_problem_and_stops_there(self):
        checks = environment_report(platform='darwin')
        self.assertEqual([c['name'] for c in checks], ['Windows'])
        self.assertEqual(len(blocking_problems(checks)), 1)
        self.assertIn('Windows', blocking_problems(checks)[0]['fix'])

    def test_windows_without_word_names_word_as_the_problem(self):
        checks = environment_report(platform='win32', progid=None, pywin32=True)
        problems = blocking_problems(checks)
        self.assertEqual([c['name'] for c in problems], ['Microsoft Word'])
        self.assertIn('WPS', problems[0]['fix'])

    def test_windows_without_pywin32_names_the_install_command(self):
        checks = environment_report(platform='win32', progid='Word.Application.16', pywin32=False)
        problems = blocking_problems(checks)
        self.assertEqual([c['name'] for c in problems], ['pywin32'])
        self.assertIn('pip install', problems[0]['fix'])

    def test_a_complete_environment_has_no_blocking_problem(self):
        checks = environment_report(platform='win32', progid='Word.Application.16', pywin32=True)
        self.assertEqual(blocking_problems(checks), [])
        self.assertEqual({c['name'] for c in checks}, {'Windows', 'pywin32', 'Microsoft Word'})
        self.assertEqual(checks[-1]['detail'], 'Word.Application.16')

    def test_every_check_carries_a_fix_a_user_can_act_on(self):
        for checks in (environment_report(platform='darwin'),
                       environment_report(platform='win32', progid=None, pywin32=False)):
            for check in checks:
                self.assertTrue(check['fix'].strip(), check['name'])


@unittest.skipUnless(sys.platform == 'win32', 'registry probe is Windows-only')
class RegistryProbeTests(unittest.TestCase):
    def test_an_unregistered_progid_is_not_reported_as_present(self):
        self.assertIsNone(registered_progid('Definitely.NotInstalled.Ever'))

    def test_probing_word_never_raises_and_returns_a_string_or_none(self):
        found = registered_progid(WORD_PROGID)
        self.assertTrue(found is None or isinstance(found, str))


if __name__ == '__main__':
    unittest.main()
