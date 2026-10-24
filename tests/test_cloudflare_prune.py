"""Offline regression tests; all tokens and API responses are synthetic."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from io import StringIO
import os
import unittest
from unittest.mock import patch
from scripts import prune_cloudflare as prune
from scripts.check_cloudflare import EXPECTED_ACCOUNT

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
TOKEN = 'synthetic-secret-do-not-log'


def dep(ident, created):
    return {'id': ident, 'created_on': created}


class PruneTests(unittest.TestCase):
    def run_main(self, project, pages):
        calls = []
        def call(method, path, token):
            calls.append((method, path))
            if method == 'DELETE':return {'success': True}
            if '/deployments?' not in path:return {'success': True, 'result': project}
            page = int(path.split('page=')[1].split('&')[0])
            return {'success': True, 'result': pages[page-1], 'result_info': {'total_pages': len(pages)}}
        with patch.dict(os.environ, {'CLOUDFLARE_API_TOKEN': TOKEN, 'CLOUDFLARE_ACCOUNT_ID': EXPECTED_ACCOUNT}), \
             patch.object(prune, 'call', side_effect=call), redirect_stdout(StringIO()) as out:
            prune.main(NOW)
        self.assertNotIn(TOKEN, out.getvalue())
        return [p.split('/deployments/')[1].split('?')[0] for m, p in calls if m == 'DELETE']

    def test_deletes_only_old_non_production_deployments_across_pages(self):
        project = {'canonical_deployment': {'id': 'prod'}, 'latest_deployment': {'id': 'prod'}}
        pages = [[dep('new', '2026-10-24T08:00:00Z'), dep('prod', '2026-10-01T00:00:00Z')],
                 [dep('old', '2026-10-23T11:00:00.123Z'), dep('undated', None)]]
        self.assertEqual(self.run_main(project, pages), ['old'])

    def test_refuses_without_known_production_deployment(self):
        with self.assertRaises(prune.PruneError):
            self.run_main({}, [[dep('old', '2026-10-01T00:00:00Z')]])

    def test_cli_withholds_details(self):
        err = StringIO()
        with patch.dict(os.environ, {'CLOUDFLARE_API_TOKEN': '', 'CLOUDFLARE_ACCOUNT_ID': EXPECTED_ACCOUNT}), \
             redirect_stderr(err):
            self.assertEqual(prune.cli(), 1)
        self.assertIn('values withheld', err.getvalue())


if __name__ == '__main__':unittest.main()
