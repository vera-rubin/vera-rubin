"""Rebuild README.md from public GitHub records: every merged pull request by vera-rubin outside its own orgs,
one row each, biggest owners first. Runs hourly in GitHub Actions (.github/workflows/readme.yml); the workflow commits
as github-actions[bot] only when the text changed, so refreshes never count as the owner's contributions.

Standard library only. Needs GITHUB_TOKEN (the workflow's own token is enough: all data here is public).
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ME = 'vera-rubin'
OURS = ('chiplab-research', ME)
# fixes merged outside GitHub's pull request flow (Gerrit), with their public record
EXTRA_MERGED = [('ceres-solver/ceres-solver', 'https://ceres-solver-review.googlesource.com/c/ceres-solver/+/28880',
                 'Fix covariance truncation for wide Jacobians', '2026-10-16')]
API = 'https://api.github.com/'
TOKEN = os.environ.get('GITHUB_TOKEN', '')


def get(path, params=None):
    url = API + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                               'X-GitHub-Api-Version': '2022-11-28',
                                               'User-Agent': 'vera-rubin-readme',
                                               **({'Authorization': f'Bearer {TOKEN}'} if TOKEN else {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 502, 503) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            raise


def merged_items():
    excl = ' '.join(f'-org:{o}' if o != ME else f'-user:{o}' for o in OURS)
    items, page = [], 1
    while True:
        d = get('search/issues', {'q': f'is:pr author:{ME} is:merged {excl}', 'per_page': 100, 'page': page})
        items += d['items']
        if len(d['items']) < 100 or len(items) >= min(d['total_count'], 1000):
            return items
        page += 1


_owner, _stars = {}, {}


def owner_info(login):
    """The owning account's display name, follower count and type (Organization or User)."""
    if login not in _owner:
        try:
            j = get(f'users/{login}')
            _owner[login] = (j.get('name'), j.get('followers') or 0, j.get('type') or 'User')
        except urllib.error.HTTPError:
            _owner[login] = (None, 0, 'User')
    return _owner[login]


def stars(repo):
    if repo not in _stars:
        try:
            _stars[repo] = get(f'repos/{repo}').get('stargazers_count') or 0
        except urllib.error.HTTPError:
            _stars[repo] = 0
    return _stars[repo]


def label(repo, org_name, kind):
    """'Alibaba / MNN': the owner's display name in front when it adds something; just the name when it repeats the
    repo (Keras, Pillow); the login when the name is missing, too long, or the owner is a person (never a person's name)."""
    login, name = repo.split('/')
    org = (org_name or '').strip()
    org = org[4:] if org.startswith('The ') else org
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())  # noqa: E731
    if not org or len(org) > 30 or kind != 'Organization':
        org = login
    if norm(name) and norm(name) in norm(org):
        return org
    return f'{org} / {name}'


def clean_title(t):
    """Drop "[Module:Type]" and "scope(x): " prefixes; keep the case (titles often start with code)."""
    t = re.sub(r'^\s*(\[[^\]]*\]\s*|[\w./-]+(\([^)]*\))?!?:\s*)+', '', t).strip().lstrip(':;-– ').strip()
    if re.match(r'[a-z]+ ', t):
        t = t[0].upper() + t[1:]
    return t.replace('|', '\\|')[:90]


def badge(url):
    # GitHub's merged icon (Primer Octicons, MIT): light purple on dark themes, GitHub's own purple on light ones
    return (f'<a href="{url}"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/merged-dark.svg" />'
            f'<img src="assets/merged.svg" width="16" height="16" alt="merged" /></picture></a>')


def collect():
    rows = [dict(repo=i['repository_url'].split('/repos/', 1)[1], url=i['html_url'], title=i['title'],
                 at=i['closed_at']) for i in merged_items()]
    rows += [dict(repo=repo, url=url, title=title, at=day) for repo, url, title, day in EXTRA_MERGED]
    for r in rows:
        r['stars'] = stars(r['repo'])
        name, r['org_followers'], kind = owner_info(r['repo'].split('/')[0])
        r['label'] = label(r['repo'], name, kind)
    rows = sorted(rows, key=lambda r: r['at'], reverse=True)
    rows = sorted(rows, key=lambda r: (-r['org_followers'], -r['stars']))
    return rows


def readme(rows):
    projects = len({r['repo'].lower() for r in rows})
    lines = ['<div align="center">', '', '<h3>We find bugs in open-source hardware and software.</h3>', '',
             f'<p><b>{len(rows)}</b> fixes merged upstream &nbsp;·&nbsp; <b>{projects}</b> projects</p>',
             '', '</div>', '',
             '<div align="center">', '', '| Project | Fix | PR |', '|:---|:---|:---:|']
    for r in rows:
        lines.append(f"| **[{r['label']}](https://github.com/{r['repo']})** | {clean_title(r['title'])} | "
                     f"{badge(r['url'])} |")
    lines += ['', '</div>', '', '<br />', '', '<div align="center">', '',
              '<a href="https://github.com/chiplab-research"><img src="https://img.shields.io/badge/@chiplab--research-111111'
              '?style=for-the-badge&logo=github" alt="chiplab-research" /></a>',
              '<a href="https://chiplab.donmakino.com"><img src="https://img.shields.io/badge/ChipLab-111111'
              '?style=for-the-badge" alt="ChipLab" /></a>', '', '</div>', '']
    return '\n'.join(lines)


def main():
    rows = collect()
    if not rows:
        raise SystemExit('no merged pull requests found; leaving README.md as it is')
    out = Path('README.md')
    new = readme(rows)
    old = out.read_text(encoding='utf-8') if out.exists() else ''
    if new.strip() == old.strip():
        print(f'unchanged: {len(rows)} merged fixes')
        return
    out.write_text(new, encoding='utf-8', newline='\n')
    print(f'updated: {len(rows)} merged fixes')


if __name__ == '__main__':
    main()
