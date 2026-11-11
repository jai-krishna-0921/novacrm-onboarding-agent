"""Read-only Rocketlane account inspection (GET only). Never prints the API key."""

import sys

import httpx

from app.config import Settings

s = Settings()
if not s.rocketlane_api_key:
    sys.exit("ROCKETLANE_API_KEY is empty")
c = httpx.Client(base_url=s.rocketlane_base_url, timeout=20,
                 headers={"api-key": s.rocketlane_api_key, "accept": "application/json"})


def get_all(path: str, **params) -> list[dict]:
    out, token = [], None
    while True:
        r = c.get(path, params={**params, "pageSize": 100, **({"pageToken": token} if token else {})})
        if r.status_code != 200:
            print(f"GET {path} -> {r.status_code} {r.text[:300]}")
            return out
        body = r.json()
        out += body.get("data", [])
        pg = body.get("pagination") or {}
        if not pg.get("hasMore"):
            return out
        token = pg.get("nextPageToken")


print("== users ==")
for u in get_all("/users"):
    print(f"  {u.get('userId')}  {u.get('firstName','')} {u.get('lastName','')}  <{u.get('email')}>  "
          f"type={u.get('type')} status={u.get('status')} role={(u.get('role') or {}).get('roleName')}")

print("== companies ==")
for co in get_all("/companies"):
    print(f"  {co.get('companyId')}  {co.get('companyName')}  type={co.get('companyType')}")

print("== projects (incl. archived) ==")
templates: dict = {}
for p in get_all("/projects", includeAllFields="true", **{"includeArchive.eq": "true"}):
    srcs = p.get("sources") or []
    for src in srcs:
        templates[src.get("templateId")] = src.get("templateName")
    print(f"  {p.get('projectId')}  {p.get('projectName')!r}  customer={(p.get('customer') or {}).get('companyName')!r}  "
          f"owner={(p.get('owner') or {}).get('emailId')}  status={(p.get('status') or {}).get('label')}  "
          f"{p.get('startDate')}→{p.get('dueDate')}  archived={p.get('archived')}  "
          f"templates={[s.get('templateName') for s in srcs]}  extRef={p.get('externalReferenceId')}")

print("== templates seen via project sources ==")
for tid, name in templates.items():
    print(f"  {tid}  {name}")

print("== GET /templates (undocumented endpoint) ==")
for t in get_all("/templates"):
    print(f"  {t.get('templateId')}  {t.get('objectType'):<10} {t.get('templateName')!r}  "
          f"keys={sorted(k for k in t if k not in ('createdBy',))}")

print("== project custom fields ==")
for f in get_all("/fields", **{"objectType.eq": "PROJECT"}):
    print(f"  {f.get('fieldId')}  {f.get('fieldLabel')}  type={f.get('fieldType')}")
