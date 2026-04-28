# Frontend Integration Guide - Admin and BA Dashboards

This document is a direct handoff for frontend integration of:
- Admin Dashboard
- Business Analyst (BA) Dashboard

Use this with the backend API base:
- `http://127.0.0.1:8000/api/v1`

---

## 1) Prerequisites

- Frontend must send `Authorization: Bearer <access_token>` for all protected APIs.
- Login endpoint:
  - `POST /auth/login`
- Token refresh endpoint:
  - `POST /auth/refresh`
- Recommended frontend env:
  - `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`

---

## 2) Required Dashboard APIs

### Admin
- `GET /admin/dashboard`
- `GET /work-tracking`

> Note: Backend also has `GET /admin/overview`. Current frontend code uses `/admin/dashboard`.

### BA
- `GET /ba/dashboard`
- `GET /work-tracking`

---

## 3) Standard API Envelope

All dashboard APIs return:

```json
{
  "success": true,
  "message": "Dashboard fetched.",
  "code": 200,
  "data": {}
}
```

Frontend must always read from `response.data`.

---

## 4) Admin Dashboard Contract (data payload)

`GET /admin/dashboard` -> `data`:

```json
{
  "filters": {
    "project_id": null,
    "milestone_id": null,
    "task_id": null
  },
  "overview": {
    "users_count": 10,
    "ba_count": 2,
    "employee_count": 7,
    "projects_count": 3,
    "tasks_count": 20,
    "active_timers": 1
  },
  "task_status_counts": {
    "not_started": 5,
    "in_progress": 4,
    "paused": 2,
    "completed": 7,
    "delayed": 1,
    "blocked": 1
  },
  "projects": [],
  "milestones": [],
  "tasks": [],
  "ba_summary": [],
  "employee_summary": []
}
```

### Admin UI Mapping
- Active Projects -> `overview.projects_count`
- Tasks In Progress -> `task_status_counts.in_progress`
- Completed Tasks -> `task_status_counts.completed`
- Active Employees -> `overview.employee_count`
- Project table -> `projects + milestones + tasks`
- Work tracking table -> `GET /work-tracking -> data.work_tracking`
- Recent activity -> `GET /work-tracking -> data.recent_activity`

---

## 5) BA Dashboard Contract (data payload)

`GET /ba/dashboard` includes all common dashboard fields and BA-specific metrics:

```json
{
  "overview": {
    "projects_count": 3,
    "employee_count": 7
  },
  "task_status_counts": {
    "in_progress": 4,
    "completed": 7
  },
  "tasks_created": 18,
  "tasks_completed": 7,
  "tasks_in_progress": 4,
  "tasks_delayed": 1,
  "assigned_employees": 6,
  "employee_summary": [
    {
      "id": 5,
      "first_name": "Ravi",
      "last_name": "Kumar",
      "email": "ravi@apparatus.solutions",
      "assigned_tasks": 6,
      "completed_tasks": 3,
      "in_progress_tasks": 2,
      "delayed_tasks": 1
    }
  ],
  "projects": [],
  "milestones": [],
  "tasks": [],
  "recent_activity": []
}
```

### BA UI Mapping
- Active Projects -> `overview.projects_count`
- Tasks In Progress -> `task_status_counts.in_progress` (fallback: `tasks_in_progress`)
- Completed Tasks -> `task_status_counts.completed` (fallback: `tasks_completed`)
- Active Employees -> `overview.employee_count` (fallback: `assigned_employees`)
- Project table -> `projects + milestones`
- Work tracking table -> `GET /work-tracking -> data.work_tracking`
- Recent activity -> `ba.dashboard.data.recent_activity` (fallback: `work-tracking.data.recent_activity`)

---

## 6) Work Tracking Contract

`GET /work-tracking` -> `data`:

```json
{
  "summary": {
    "records_count": 2,
    "started_count": 1,
    "paused_count": 1,
    "stopped_count": 0
  },
  "work_tracking": [
    {
      "employee_name": "Ravi Kumar",
      "task_title": "Build reporting endpoints",
      "project_name": "CRM Revamp",
      "milestone_name": "Backend APIs",
      "timer_state": "STARTED",
      "today_worked_display": "01:30:00",
      "total_time_spent_display": "03:33:20",
      "current_session_start_time": "2026-04-24T09:10:00Z",
      "current_session_display": "00:07:00"
    }
  ],
  "recent_activity": [
    {
      "action": "STARTED",
      "employee_name": "Ravi Kumar",
      "task_id": 28,
      "task_title": "Build reporting endpoints",
      "project_name": "CRM Revamp",
      "timestamp": "2026-04-24T09:10:00Z"
    }
  ]
}
```

### Timer State -> UI Badge
- `STARTED` -> Running
- `PAUSED` -> Paused
- `STOPPED` -> Stopped (or Auto-stopped label, if desired by BA UI copy)

---

## 7) Polling / Refresh Strategy

For both dashboards:
- Load dashboard + work-tracking in parallel (`Promise.all`).
- Poll every 30 seconds.
- Show non-blocking error text if API call fails.
- Keep table rendering safe with fallbacks:
  - `array ?? []`
  - `objectField ?? 0`

---

## 8) Frontend TypeScript Interfaces (recommended)

Use a shared type module (similar to `src/lib/admin-dashboard-api.ts`) with:
- `AdminDashboardPayload`
- `BADashboardPayload` (extends admin payload)
- `WorkTrackingPayload`

Important optional fields:
- `project_name`, `milestone_name` can be null/optional.
- `recent_activity` may come from BA dashboard OR work-tracking response.

---

## 9) Integration Checklist

- [ ] Login flow stores `access` and `refresh`.
- [ ] API client injects bearer token on protected routes.
- [ ] Admin page calls `GET /admin/dashboard` and `GET /work-tracking`.
- [ ] BA page calls `GET /ba/dashboard` and `GET /work-tracking`.
- [ ] KPIs mapped exactly as per section 4 and 5.
- [ ] Timer statuses mapped exactly as per section 6.
- [ ] 30-second auto refresh enabled.
- [ ] Empty state handling for all tables/lists.
- [ ] Unauthorized (401) flow refreshes token or redirects to login.

---

## 10) Cursor Prompt For Frontend Team (copy/paste)

Use this prompt directly in Cursor:

```text
Integrate Admin and Business Analyst dashboard APIs using this contract:

Admin:
- GET /api/v1/admin/dashboard
- GET /api/v1/work-tracking

BA:
- GET /api/v1/ba/dashboard
- GET /api/v1/work-tracking

Requirements:
1) Build typed API layer with interfaces for AdminDashboardPayload, BADashboardPayload, WorkTrackingPayload.
2) Load dashboard + work-tracking in parallel.
3) Poll every 30s and update KPIs, project overview table, work tracking table, and recent activity.
4) Use safe fallbacks for optional/null fields.
5) Map timer states:
   STARTED -> Running
   PAUSED -> Paused
   STOPPED -> Stopped (or Auto-stopped in BA copy)
6) Handle 401 with token refresh and redirect to login on failure.
7) Keep implementation compatible with existing Next.js app router structure.
```

---

## 11) Notes For QA

- Verify with Admin token and BA token separately.
- Confirm BA sees only BA-scoped data from backend.
- Confirm live changes in timers are reflected after polling refresh.
- Validate dashboard still renders when arrays are empty.
