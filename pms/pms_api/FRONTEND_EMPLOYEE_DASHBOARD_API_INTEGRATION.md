# Frontend Integration Guide - Employee Dashboard APIs

This document is a direct handoff for frontend integration of the Employee dashboard and related task workflow APIs.

Use this backend API base:
- `http://187.127.139.247:6009/api/v1`

---

## 1) Prerequisites

- All protected APIs require:
  - `Authorization: Bearer <access_token>`
- Login:
  - `POST /auth/login`
- Refresh token:
  - `POST /auth/refresh`
- Current logged-in user:
  - `GET /auth/me`

Recommended frontend env (Next.js):
- `BACKEND_API_ORIGIN=http://187.127.139.247:6009` (used server-side to proxy `/api/v1/*`)
- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` (keeps browser calls using same-origin proxy routes)

---

## 2) Standard API Envelope

All APIs in this module return a standard envelope:

```json
{
  "success": true,
  "message": "Request successful.",
  "code": 200,
  "data": {}
}
```

Frontend should always read the useful payload from `response.data.data`.

---

## 3) Employee Dashboard APIs (Primary)

### A) Employee dashboard summary
- **Endpoint**: `GET /employee/dashboard`
- **Who can call**: Authenticated user (intended for Employee role)
- **Purpose**: KPI cards for employee homepage

`data` shape:

```json
{
  "active_task": {
    "id": 48,
    "title": "Build employee worklog UI"
  },
  "completed_tasks": 7
}
```

Notes:
- `active_task` is `null` when no task is in `IN_PROGRESS`.
- `completed_tasks` is employee-specific completed count.

---

### B) Employee task list
- **Endpoint**: `GET /my/tasks`
- **Who can call**: Authenticated user
- **Purpose**: Full assigned task list for employee page table/cards

`data` is an array of tasks (already filtered to the current employee):

```json
[
  {
    "id": 48,
    "project": 11,
    "project_name": "CRM Revamp",
    "project_document": "/media/projects/crm.docx",
    "milestone": 22,
    "milestone_name": "Employee Module",
    "milestone_document": "/media/milestones/m22.docx",
    "title": "Build employee worklog UI",
    "description": "Implement start/pause/stop controls",
    "assigned_to": 9,
    "assigned_to_name": "Ravi Kumar",
    "created_by": 3,
    "created_by_name": "BA User",
    "status": "IN_PROGRESS",
    "priority": "HIGH",
    "deadline": "2026-05-05",
    "document": "/media/tasks/task-48.docx",
    "created_at": "2026-04-28T11:10:00Z",
    "updated_at": "2026-04-28T12:45:00Z"
  }
]
```

Important:
- For Employee role, backend hides `total_time_spent_seconds` and `total_time_spent_display` in task serializer.
- Use this API as the main source for employee task table.

---

### C) Employee dashboard list fallback / advanced filters
- **Endpoint**: `GET /tasks`
- **Who can call**: Authenticated user
- **Purpose**: Same assigned tasks for Employee (backend auto-filters to current employee in queryset)

Optional use:
- If frontend needs pagination behavior from DRF viewset list endpoints.
- If frontend later wants to reuse one generic task service for all roles.

---

## 4) Employee Task Actions (Dashboard Buttons)

Use these endpoints for action buttons inside task cards/table rows.

### A) Start task timer
- **Endpoint**: `POST /tasks/{task_id}/start/`
- **Allowed only when**:
  - logged-in user role is EMPLOYEE
  - task is assigned to logged-in employee
  - employee does not already have another active timer

Success `data`:

```json
{
  "task_id": 48,
  "status": "IN_PROGRESS"
}
```

Common errors:
- `403`: `"Only assigned employee can start this task."`
- `400`: `"You already have an active timer."`

---

### B) Pause task timer
- **Endpoint**: `POST /tasks/{task_id}/pause/`
- **Allowed when**: active timer exists for this task + employee

Success `data`:

```json
{
  "task_id": 48,
  "status": "PAUSED"
}
```

Common errors:
- `400`: `"No active timer found for this task."`

---

### C) Stop task timer
- **Endpoint**: `POST /tasks/{task_id}/stop/`

Employee success `data`:

```json
{
  "task_id": 48,
  "status": "PAUSED"
}
```

Notes:
- If task is not completed yet, backend sets task status to `PAUSED` on stop.
- This API does not auto-complete tasks.

---

### D) Update task status
- **Endpoint**: `PATCH /tasks/{task_id}/status/`
- **Body**:

```json
{
  "status": "COMPLETED"
}
```

Allowed status values:
- `NOT_STARTED`
- `IN_PROGRESS`
- `PAUSED`
- `COMPLETED`
- `DELAYED`
- `BLOCKED`

Success `data`:

```json
{
  "task_id": 48,
  "task_name": "Build employee worklog UI",
  "status": "COMPLETED",
  "mail_triggered": true
}
```

Important rule:
- If active timer exists, backend rejects completion:
  - `"Stop the active timer first, then mark task as completed."`

---

### E) Request deadline change (employee -> task owner)
- **Endpoint**: `POST /tasks/{task_id}/request-deadline-change/`
- **Allowed only when**:
  - caller is assigned employee for this task

Request body:

```json
{
  "new_deadline": "2026-05-08",
  "reason": "Need additional API validation testing"
}
```

Success `data`:

```json
{
  "task_id": 48,
  "new_deadline": "2026-05-08",
  "reason": "Need additional API validation testing",
  "mail_triggered": true
}
```

---

## 5) Notifications API (Employee Bell Icon)

### A) List notifications
- **Endpoint**: `GET /notifications/`
- **Who can call**: Authenticated user
- **Behavior**: Backend returns only current user notifications

### B) Mark notification as read
- **Endpoint**: `PATCH /notifications/{id}/read/`

Use these to support:
- bell badge count
- unread/read state
- click-to-mark-read behavior

---

## 6) Suggested UI Mapping

- **Top KPI cards**
  - Active Task -> `employeeDashboard.data.active_task?.title`
  - Completed Tasks -> `employeeDashboard.data.completed_tasks`

- **Task list**
  - Source -> `GET /my/tasks`
  - Columns -> title, project_name, milestone_name, status, priority, deadline

- **Task action buttons**
  - Start -> `POST /tasks/{id}/start/`
  - Pause -> `POST /tasks/{id}/pause/`
  - Stop -> `POST /tasks/{id}/stop/`
  - Mark Completed -> `PATCH /tasks/{id}/status/` with `COMPLETED`
  - Request Deadline -> `POST /tasks/{id}/request-deadline-change/`

- **Notification bell**
  - list -> `GET /notifications/`
  - mark read -> `PATCH /notifications/{id}/read/`

---

## 7) Polling and Refresh Strategy

- Initial page load:
  - run `GET /employee/dashboard` and `GET /my/tasks` in parallel
  - run `GET /notifications/` in parallel for bell count
- Poll every 30 seconds for dashboard/task updates.
- Trigger immediate refetch after task actions (`start`, `pause`, `stop`, `status`, deadline request).
- Always use safe fallbacks:
  - arrays -> `[]`
  - objects -> `null`
  - counts -> `0`

---

## 8) Role and Permission Notes (Frontend Guardrails)

- Employee should not be shown Admin/BA-only actions.
- Hide start button if task already `IN_PROGRESS` and another task is active.
- Disable "Complete" button until timer is stopped.
- Show backend error message directly for action failures (403/400) for clarity.

---

## 9) TypeScript Interfaces (Recommended)

```ts
export type TaskStatus =
  | "NOT_STARTED"
  | "IN_PROGRESS"
  | "PAUSED"
  | "COMPLETED"
  | "DELAYED"
  | "BLOCKED";

export interface EmployeeDashboardPayload {
  active_task: { id: number; title: string } | null;
  completed_tasks: number;
}

export interface EmployeeTask {
  id: number;
  project: number;
  project_name: string;
  project_document?: string | null;
  milestone: number | null;
  milestone_name?: string | null;
  milestone_document?: string | null;
  title: string;
  description: string;
  assigned_to: number | null;
  assigned_to_name?: string | null;
  created_by: number;
  created_by_name?: string;
  status: TaskStatus;
  priority: string;
  deadline: string | null;
  document?: string | null;
  created_at: string;
  updated_at: string;
}
```

---

## 10) Integration Checklist

- [ ] Login stores `access` and `refresh`.
- [ ] API client injects bearer token in protected APIs.
- [ ] Employee page loads `GET /employee/dashboard`.
- [ ] Employee page loads `GET /my/tasks`.
- [ ] Row/card actions wired to `start`, `pause`, `stop`, `status`, deadline request APIs.
- [ ] Notification bell integrated with list + read endpoints.
- [ ] 30-second polling implemented.
- [ ] 401 handling: refresh token then retry; redirect login if refresh fails.
- [ ] UI fallbacks for empty data and null active task.

---

## 11) Cursor Prompt for Frontend Team (Copy/Paste)

```text
Integrate Employee Dashboard APIs in our Next.js frontend using this contract:

Base:
- /api/v1

Endpoints:
1) GET /employee/dashboard
2) GET /my/tasks
3) POST /tasks/{id}/start/
4) POST /tasks/{id}/pause/
5) POST /tasks/{id}/stop/
6) PATCH /tasks/{id}/status/   body: { "status": "COMPLETED" | ... }
7) POST /tasks/{id}/request-deadline-change/  body: { "new_deadline": "YYYY-MM-DD", "reason": "..." }
8) GET /notifications/
9) PATCH /notifications/{id}/read/

Requirements:
- Build strongly typed API layer and interfaces for EmployeeDashboardPayload and EmployeeTask.
- Load dashboard + tasks + notifications in parallel.
- Poll every 30 seconds and refetch immediately after task actions.
- Show backend message for 4xx errors.
- Handle 401 with token refresh flow.
- Keep UI resilient to null/empty fields.
```

---

## 12) QA Notes

- Test with Employee token only.
- Verify employee cannot start/pause/stop tasks not assigned to them.
- Verify one active timer rule is enforced.
- Verify completing task with active timer shows proper error.
- Verify notification list and mark-as-read behavior.

