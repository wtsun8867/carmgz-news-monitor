"""Delete Cloudflare Pages deployments older than the backup window.

Every deployment keeps its own copy of dashboard.json. The current production deployment
is always kept; older ones are removed so no article copy outlives the 7-day retention by
more than the same 1-day window GitHub artifacts use. Never log secrets or response bodies.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import urllib.error
import urllib.request

if __package__:
    from .check_cloudflare import API, PROJECT, EXPECTED_ACCOUNT, NoRedirect
else:
    from check_cloudflare import API, PROJECT, EXPECTED_ACCOUNT, NoRedirect

KEEP = timedelta(hours=24)
MAX_PAGES = 40


class PruneError(RuntimeError):
    """Messages must contain only application-controlled text."""


def call(method, path, token):
    request = urllib.request.Request(API+path, method=method, headers={
        'Authorization': 'Bearer '+token, 'Accept': 'application/json',
        'User-Agent': 'CARMGZ-Cloudflare-prune'})
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, OSError, ValueError):
        raise PruneError(f'{method} transport failure; details withheld')
    with response:
        try:
            payload = json.loads(response.read(2_000_001))
        except (ValueError, OSError):
            payload = {}
    if response.code != 200 or not isinstance(payload, dict) or payload.get('success') is not True:
        raise PruneError(f'{method} deployments: HTTP {response.code}; check Pages edit permission')
    return payload


def created(item):
    try:
        return datetime.fromisoformat(str(item['created_on']).replace('Z', '+00:00'))
    except (KeyError, ValueError):
        return None


def expired(deployments, keep_ids, now):
    out = []
    for item in deployments:
        at = created(item)
        if item.get('id') in keep_ids:
            continue
        # Undated rows are kept and reported rather than guessed.
        if at is not None and now-at > KEEP:
            out.append(item['id'])
    return out


def main(now=None):
    now = now or datetime.now(timezone.utc)
    account = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
    token = os.environ.get('CLOUDFLARE_API_TOKEN', '')
    if account != EXPECTED_ACCOUNT or not token:
        raise PruneError('Cloudflare account or token missing; values withheld')
    base = f'/accounts/{account}/pages/projects/{PROJECT}'
    project = call('GET', base, token).get('result') or {}
    keep_ids = {(project.get(k) or {}).get('id') for k in ('canonical_deployment', 'latest_deployment')} - {None}
    if not keep_ids:
        raise PruneError('Current production deployment not reported; nothing deleted')
    deployments = []
    for page in range(1, MAX_PAGES+1):
        payload = call('GET', f'{base}/deployments?page={page}&per_page=25', token)
        rows = payload.get('result') or []
        deployments += [r for r in rows if isinstance(r, dict)]
        info = payload.get('result_info') or {}
        if not rows or page >= info.get('total_pages', page):
            break
    # Collect first, delete after: deleting while paging shifts later pages.
    targets = expired(deployments, keep_ids, now)
    for ident in targets:
        call('DELETE', f'{base}/deployments/{ident}?force=true', token)
    undated = sum(created(d) is None for d in deployments)
    print(f'Cloudflare deployments: {len(deployments)} listed, {len(targets)} older than {KEEP.total_seconds()/3600:.0f}h deleted, {undated} undated kept')


def cli():
    try:
        main()
    except PruneError as exc:
        print('Cloudflare prune failed: '+str(exc), file=sys.stderr)
        return 1
    except Exception:
        print('Cloudflare prune failed: unexpected response or local error; details withheld', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(cli())
