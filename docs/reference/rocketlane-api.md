# Rocketlane REST API reference (for the onboarding integration)

Collected 2026-11-11 from the official docs: developer.rocketlane.com (stable version **1.0**; a **1.3 "Marketplace" beta** version of the docs also exists with the same endpoint list) and help.rocketlane.com. The `.md` versions of the reference pages (e.g. `https://developer.rocketlane.com/reference/create-project.md`) contain the full OpenAPI JSON, and that JSON is where the field names below come from. Anything marked **UNVERIFIED** is not on an official page.

## 1. Basics

- **Base URL:** `https://api.rocketlane.com/api/1.0`. The OpenAPI server is `https://api.rocketlane.com/api` and paths start with `/1.0/...`. Source: https://developer.rocketlane.com/docs/examples, https://developer.rocketlane.com/docs/overview
- **Auth:** send the header `api-key: <key>` (OpenAPI securityScheme `{"type":"apiKey","name":"api-key","in":"header"}`). You create keys in the app under Settings → API → "Create API key". A key has the same permissions as the user who created it, and keys can expire. Source: https://developer.rocketlane.com/docs/authentication
- JSON in, JSON out (UTF-8). Every response carries an `X-Request-Id` header, which support asks for. Methods used are GET, POST, PUT and DELETE.
- **Rate limits** (source: https://developer.rocketlane.com/docs/rate-limiting):
  | Method | Max requests/min |
  |---|---|
  | GET (only "Get all" APIs) | 60 |
  | GET, POST, PUT, DELETE | 200 |
  The docs say limits apply over minute and hour windows, but the hourly quota isn't published. A 429 response returns `{"errors":[{"errorCode":"TOO_MANY_REQUEST","errorMessage":"Rate limit exceeded"}]}` plus an `X-Retry-After` header whose value is an **epoch-millis timestamp**, not a number of seconds.
- **Errors** (source: https://developer.rocketlane.com/docs/http-status-codes): the body always has the shape `{"errors":[{errorCode, errorMessage, field?}]}`.
  ```json
  {"errors":[{"errorCode":"BAD_REQUEST","errorMessage":"Bad Request: Project name is missing","field":"projectName"}]}
  {"errors":[{"errorCode":"NOT_AUTHORIZED","errorMessage":"Not Authorized: User not authorized"}]}
  {"errors":[{"errorCode":"NOT_FOUND","errorMessage":"Not Found: Resource not found for given ID"}]}
  {"errors":[{"errorCode":"INTERNAL_SERVER_ERROR","errorMessage":"Server Error: Could not perform the requested action"}]}
  ```
  Status codes: 200 OK, 201 created, 204 deleted (no body), 400, 401, 404, 429, 500.
- **Pagination** (source: https://developer.rocketlane.com/docs/pagination): query params `pageSize` (default and maximum 100) and `pageToken`. Results come back in `data`, with a `pagination` object alongside:
  ```json
  "pagination": {"pageSize": 10, "hasMore": true, "totalRecordCount": 20,
    "nextPageToken": "78c12a42-...", "nextPage": "https://api.rocketlane.com/api/1.0/tasks?pageSize=10&pageToken=...&sortBy=taskId&sortOrder=DESC"}
  ```
  A `nextPageToken` is valid for 15 minutes. Keep paging until `hasMore` is false.
- **Search/filter syntax** (source: https://developer.rocketlane.com/docs/search): filters are query params of the form `field.op=value`, with `op` one of `eq gt lt ge le cn nc` (cn = contains, nc = not contains). Some endpoints also accept `oneOf` and `noneOf`. Other common params are `match=all|any` (default AND), `sortBy`, `sortOrder=ASC|DESC` (default DESC), `includeFields=a,b`, and `includeAllFields=true`. The docs warn that a change can take "a couple of seconds" to show up in search results.
- **Custom-field filters** (same Search page): `{type}.field.{fieldId}.{op}` where op is one of `value`, `contains`, `notContains`, `greaterThan`, `lessThan`, `greaterThanEqual`, `lessThanEqual`, `isEmpty`, `isNotEmpty`, `isNot`, `isTrue`, `isFalse`, `oneOf`, `noneOf`. Examples: `project.field.14.value=2023-12-24`, `project.field.12.contains="SearchText"`, `project.field.16.oneOf=1,3`. **UNVERIFIED:** these params appear only in the Search guide, not in the parameter list of the "Get all projects" endpoint, so test them against the live API.

## 2. Create project: `POST /projects`

Source: https://developer.rocketlane.com/reference/create-project. Returns **201** with the project object. Optional query params: `includeFields`, `includeAllFields`.

Request body (`ProjectPublicAPIRequestEntity`). Required fields are **`projectName`, `owner`, `customer`**.

| Field | Notes from the docs |
|---|---|
| `projectName` (req) | string |
| `owner` (req) | `{emailId}` or `{userId}`. The project owner "will receive project invite email based on their notification configuration". |
| `customer` (req) | `{companyName}`. The name is **case-sensitive and must match exactly**, and the customer "cannot be modified during the project's lifespan". `companyId` is marked readOnly here. |
| `autoCreateCompany` | bool. When true, an existing company is reused and otherwise a new one is created. |
| `startDate` | `YYYY-MM-DD`. **Required if `sources` is given**, otherwise optional. |
| `dueDate` | `YYYY-MM-DD`, optional. If `sources` is given, the due date is "calculated depending on the duration of the specified sources". It must be on or after `startDate`. |
| `sources` | `[{templateId (req, int), startDate (req, date), prefix?}]`. These are the templates applied at creation. |
| `assignProjectOwner` | bool. Assigns any unassigned tasks to the project owner. It is ignored if there are no `sources`. |
| `teamMembers` | `{members:[{emailId\|userId}], customers:[{emailId\|userId}], customerChampion:{emailId\|userId}}` |
| `fields` | `[{fieldId, fieldValue}]`. Custom fields; the value is a string, number or array depending on the field type. |
| `status` | `{value: <int>}` |
| `visibility` | `EVERYONE` \| `MEMBERS` |
| `placeholders` | `[{placeholderId (req), user (req)}]` |
| `externalReferenceId` | string, e.g. `"pr_8171"`. Filterable later with `externalReferenceId.eq`, which helps with idempotency. |
| other | `partners[{companyName\|companyId}]`, `annualizedRecurringRevenue`, `projectFee`, `autoAllocation`, `budgetedHours`, `financials{contractType,...}`, `currency` |

**Yes, a template is applied at creation with `sources: [{templateId, startDate}]`**, and you can pass more than one source, each with its own `prefix`. The official minimal example (https://developer.rocketlane.com/docs/examples) is:
```json
{"projectName":"Acme onboarding","customer":{"companyName":"Acme Inc"},"owner":{"emailId":"john.doe@rocketlane.com"}}
```
Example with the fields we need, built only from the schema field names above (Rocketlane does not publish this exact payload):
```json
{"projectName":"NovaCRM - Acme","owner":{"emailId":"pm@ours.com"},
 "customer":{"companyName":"Acme Inc"},"autoCreateCompany":true,
 "startDate":"2026-12-01","sources":[{"templateId":201,"startDate":"2026-12-01"}],
 "assignProjectOwner":true,
 "teamMembers":{"members":[{"emailId":"cs@ours.com"}],"customers":[{"emailId":"rachel.green@acme.com"}]},
 "fields":[{"fieldId":201,"fieldValue":"Enterprise"}],"externalReferenceId":"crm_123"}
```
Response fields (`ProjectPublicAPIResponseEntity`) include `projectId, projectName, startDate, dueDate, owner, customer{companyId,companyName,companyUrl}, teamMembers, fields, status{value,label}, sources[{templateId,templateName,startDate,prefix}], archived, visibility, createdAt, updatedAt (epoch ms), externalReferenceId`. Some fields only come back if you ask for them with `includeFields`, e.g. `sources`, `currentPhase`, `externalReferenceId`.

Related project endpoints:
- `GET /projects/{projectId}` and `PUT /projects/{projectId}` (update), `DELETE /projects/{projectId}`, `POST /projects/{projectId}/archive`
- `POST /projects/{projectId}/add-members`, body `{"members":[{emailId|userId}],"customers":[{emailId|userId}]}`. Source: https://developer.rocketlane.com/reference/add-members. There is also `remove-members`.
- `GET /projects/placeholders`, plus assign/unassign placeholders (listed in the reference, not explored here).

## 3. Import template into an existing project

`POST /projects/{projectId}/import-template` exists. Source: https://developer.rocketlane.com/reference/import-template
- The body is a **bare JSON array**, not wrapped in an object: `[{"templateId":201,"startDate":"2023-03-28","prefix":"APAC"}]`. `templateId` and `startDate` are required.
- Returns 200 with the updated project. Error codes: 400, 401, 404.

## 4. List/search projects (duplicate check): `GET /projects`

Source: https://developer.rocketlane.com/reference/get-all-projects
- **Archived projects are excluded by default**; pass `includeArchive.eq=true` to include them.
- Filters:
  - Name: `projectName.eq|cn|nc`
  - Customer: `customerId.eq|oneOf|noneOf` and `companyId.eq|oneOf|noneOf` (both are described as "customer company id")
  - Others: `teamMemberId.*`, `status.eq|oneOf|noneOf`, `externalReferenceId.eq`, `startDate.*`, `dueDate.*`, `createdAt.*` (epoch ms), `updatedAt.*`, `inferredProgress.eq`, `contractType.*`, ARR/fee/hours filters
- `sortBy` is one of `projectName, startDate, dueDate, startDateActual, dueDateActual, annualizedRecurringRevenue, projectFee`.
- **Suggested duplicate check:** look up the company with `GET /companies?companyName.eq=Acme Inc`, then call `GET /projects?projectName.eq=...&companyId.eq=<id>&includeArchive.eq=true`, or use `externalReferenceId.eq`. Companies support the filters `companyName.eq|cn|nc`, `companyUrl.*` and `accountOwner.*` (https://developer.rocketlane.com/reference/get-all-companies).
- Custom-field filter: `project.field.{fieldId}.value=...` (see section 1; **UNVERIFIED** for this endpoint).
- Field IDs come from `GET /fields?objectType.eq=PROJECT`. Other filters there: `fieldType.eq`, `enabled.eq`, `private.eq`. Response items have `fieldId`, `fieldLabel`, `objectType`, and `fieldOptions[{optionValue, optionLabel}]`. Source: https://developer.rocketlane.com/reference/get-all-fields, https://developer.rocketlane.com/docs/custom-fields

## 5. Templates

**There is no documented endpoint to list or get templates.** The reference has a "Templates" category page (https://developer.rocketlane.com/reference/templates, updated 2025-08-07), but it contains no endpoints, and neither llms.txt for 1.0 nor for 1.3 lists any template operation. The Search guide mentions "templates" in passing. A third-party tool (aident.ai) advertises "list templates" and "get template by id" actions; whether `GET /templates` exists is **UNVERIFIED**. Ways to get template IDs:
- Read the `sources[].templateId/templateName` field of an existing project (`GET /projects/{id}?includeFields=sources`).
- Take it from the template's URL in the Rocketlane app (**UNVERIFIED**).
- Store it as configuration.

## 6. Phases, tasks, comments

**Phases** (https://developer.rocketlane.com/reference/get-all-phases, https://developer.rocketlane.com/reference/create-phase):
- `GET /phases?projectId=<id>`. `projectId` is a **required** query param. Filters: `phaseName.eq|cn|nc`, `startDate.*`, `dueDate.*`.
- `POST /phases` requires `phaseName`, `project{projectId}`, `startDate` and `dueDate`. Optional: `phaseDescription`, `status{value}`, `private`.
- `GET`, `PUT` and `DELETE /phases/{phaseId}`.

**Tasks** (https://developer.rocketlane.com/reference/create-task, https://developer.rocketlane.com/reference/update-task, https://developer.rocketlane.com/reference/get-all-tasks):
- `POST /tasks` requires `taskName` and `project{projectId}`. Optional fields:
  - `taskDescription` (HTML) and `taskPrivateNote` (HTML, visible to team members only)
  - `startDate` and `dueDate` (`YYYY-MM-DD`, due date on or after start date)
  - `effortInMinutes`, `progress` (0-100), `atRisk`, `type` (`TASK|MILESTONE`)
  - `phase{phaseId|phaseName}`, `status{value:int}`, `fields[{fieldId,fieldValue}]`
  - `assignees{members:[{emailId|userId}], placeholders:[...]}` (`userId` wins if both are given)
  - `followers{members:[...]}`, `parent{taskId}` (subtask), `externalReferenceId`, `private`
  - `source{templateId,startDate}` to create the task from a task template
- `PUT /tasks/{taskId}` accepts `taskName, taskDescription, taskPrivateNote, startDate, dueDate, effortInMinutes, progress, atRisk, type, fields, status{value}, externalReferenceId, private`. Assignees, followers, dependencies and phase are changed through separate action endpoints:
  - `POST /tasks/{taskId}/add-assignees` and `/remove-assignees`, body `{members:[{emailId|userId}], placeholders:[...]}`
  - `POST /tasks/{taskId}/add-followers` and `/remove-followers`
  - `POST /tasks/{taskId}/add-dependencies` and `/remove-dependencies`
  - `POST /tasks/{taskId}/move-phase`
  - Source: https://developer.rocketlane.com/reference/add-assignee-to-task and the neighbouring pages. The `remove-followers` and `remove-dependencies` slugs come from the llms.txt page titles; their OpenAPI pages were not opened.
- `GET /tasks` filters:
  - `projectId.eq`, `phaseId.eq`, `taskName.eq|cn|nc`
  - `dueDate.lt|le|gt|ge|eq`, `startDate.*`, `progress.*`, `effortInMinutes.*`
  - `task.status.eq|oneOf|noneOf`, `project.status.*`, `assignee.userId.eq|oneOf|noneOf`, `externalReferenceId.eq`, `includeArchive.eq`
  - `sortBy` is one of `taskName, startDate, dueDate, startDateActual, dueDateActual`.
  - For overdue tasks, use `dueDate.lt=<today>` together with a status filter.
- Response fields include `taskId, taskName, startDate, dueDate, status{value,label}, progress, atRisk, phase, assignees, followers, dependencies, parent, priority, project, fields`.
- Status is an integer `value` paired with a `label` (example `{"value":2,"label":"In progress"}`). **UNVERIFIED:** no endpoint is documented that lists the available status values. Read them from existing tasks, or check whether `GET /fields?objectType.eq=TASK` exposes them.

**Comments: no endpoint.** There is no comment, conversation or message endpoint for tasks or projects in either 1.0 or 1.3. The in-app task chat exists, since the automations have a "A new message on the task" trigger and a "Send message" action, but the public API doesn't expose it. Fallbacks:
- Append to `taskDescription` or `taskPrivateNote` with `PUT /tasks/{id}`.
- Write to a NOTE-type custom field. A company version exists at `POST /companies/{companyId}/add-notes`; the docs also allow NOTE fields on PROJECT, but whether a project add-notes endpoint exists is **UNVERIFIED**.
- Create a Space Document (`/space-documents`).

## 7. Users (find by email)

`GET /users?email.eq=john.doe@acme.com`. Source: https://developer.rocketlane.com/reference/get-all-users
- Other filters:
  - `firstName.*`, `lastName.*`, `email.cn|nc`
  - `status.eq|oneOf|noneOf` (`INACTIVE|INVITED|ACTIVE|PASSIVE`)
  - `type.eq|oneOf` (`TEAM_MEMBER|PARTNER|CUSTOMER|EXTERNAL_PARTNER`)
  - `roleId.*`, `permissionId.*`, `capacityInMinutes.*`
- Response items have `userId, email, firstName, lastName, type, status, role{roleId,roleName}, company{companyId,companyName}, permission{permissionId,permissionName}, fields, capacityInMinutes, ...`.
- **Name mismatch to watch:** the user object uses **`email`**, but project and task references use **`emailId`**.
- `GET /users/{userId}` also exists. Read-only: there are no endpoints to create or invite users. You add a person to a project via `teamMembers` or `add-members` by email.

## 8. Automations (UI only, no API)

- **No automations API.** The developer reference and llms.txt list no automation endpoints. Outbound **webhooks** exist and are configured in the UI (https://developer.rocketlane.com/docs/webhooks-overview). The automations themselves also have an "HTTPS request" action (help article 67000729827).
- **The "Task becomes overdue" trigger exists.** It "runs when a task crosses its due date". Example from the docs: "when a task becomes overdue by 3 days and is marked at risk, you can notify the project owner." Source: https://help.rocketlane.com/support/solutions/articles/67000723012
- **Delay/condition "overdue by N days":** the Introduction article's own example is trigger "When any task is overdue", condition "**Exceeds 3 days**", action "**Notify project owner**". It shows escalation at 5 and 7 days through extra action blocks, and conditions can match All or Any (e.g. `At risk is true`, `Priority is high`). Source: https://help.rocketlane.com/support/solutions/articles/67000722958-introduction-to-automations. The UI field name for the N-days condition isn't shown in the text (**UNVERIFIED**). Scheduled (time-based) automations are a separate feature: https://help.rocketlane.com/support/solutions/articles/67000730109
- **Actions** (Introduction article): Notify someone, Notify via Slack, Send an email, Send message, Update assignee/status/fields/dates, Shift task dates, Request approval, Create task/subtask, Import template, Archive, Delete.
- **Recipients:** the official examples name the **Project Owner** ("Notify project owner") and the **Project Manager** ("notify Project Manager"), and the docs mention "BU head and CEO" as stakeholders who can be notified. The "Send an email" action lets you customize the recipient list (article 67000746960). **UNVERIFIED:** the full list of recipient options in "Notify someone", e.g. arbitrary roles, named users, placeholders or assignees. No official page lists them.
- **Template automations: yes.** Triggers "can be set up on project templates that will be inherited by projects during creation, or ... globally for the entire account". You set them up under Templates → Project templates → pick a task, phase or form → Automations tab (https://help.rocketlane.com/support/solutions/articles/67000722963). Global automations live under Settings → Advanced → Automations (https://help.rocketlane.com/support/solutions/articles/67000722962). When both fire, global automations run first (article 67000753078).
- **Plans:** the articles list availability on Essential, Standard, Premium and Enterprise plans. Automation pricing has its own article (67000724434), which was not read for this reference.
- **Implication:** a project created with `sources:[{templateId}]` should inherit that template's automations. That is the expected behaviour, but it is not confirmed for API-created projects (**UNVERIFIED**).
