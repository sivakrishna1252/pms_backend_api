# PMS Frontend Integration Playbook (No AI)

## Goal

Use this document to integrate frontend with current PMS backend and identify missing UI items before go-live.

This is a **backend-to-frontend mapping guide** (no AI scope).

---

## Frontend Audit Sheet (Use Before Integration)

Use this section as final audit template. For every row, mark:
- `DONE` -> implemented and matching backend
- `PARTIAL` -> implemented but mismatch exists
- `NOT DONE` -> missing in UI

| Page/Feature | API(s) | What Must Be Visible/Working in UI | Status | Notes |
|---|---|---|---|---|
| Login | `POST /auth/login` | Login success/failure handling, token store |  |  |
| Session | `POST /auth/refresh` | Silent refresh + retry on 401 |  |  |
| App Bootstrap | `GET /auth/me` | Role and profile loaded on app start |  |  |
| Users List | `GET /users/` | Table + pagination + loading/empty/error states |  |  |
| Create User | `POST /users/` | Form validation + role/status selection |  |  |
| Edit User | `GET/PUT/PATCH /users/{id}/` | Existing data load + update flow |  |  |
| Delete User | `DELETE /users/{id}/` | Confirm modal + row removal |  |  |
| Projects List | `GET /projects/` | Status/deadline/creator visible |  |  |
| Create Project | `POST /projects/` | Field validation + success redirect |  |  |
| Edit Project | `PUT/PATCH /projects/{id}/` | Role-based deadline restriction handling |  |  |
| Milestones List | `GET /milestones/` | `milestone_no`, project, status visible |  |  |
| Create Milestone | `POST /milestones/` | Correct project linkage + date validation |  |  |
| Edit Milestone | `PUT/PATCH /milestones/{id}/` | Restricted field errors handled |  |  |
| Tasks List | `GET /tasks/` | Assignee/status/priority/deadline/time shown |  |  |
| Create Task | `POST /tasks/` | Project/milestone/assignee mapping correct |  |  |
| Edit Task | `PUT/PATCH /tasks/{id}/` | Data update reflects in list/detail |  |  |
| Assign Task | `POST /tasks/{id}/assign/` | Assignee changed instantly in UI |  |  |
| Update Task Status | `PATCH /tasks/{id}/status/` | Status badge/state updates correctly |  |  |
| Time Logs | `GET /tasks/{id}/time-logs/` | Session history list renders correctly |  |  |
| Timer Start | `POST /tasks/{id}/start/` | Starts timer, status in-progress |  |  |
| Timer Pause | `POST /tasks/{id}/pause/` | Pauses timer, no pause time added |  |  |
| Timer Stop | `POST /tasks/{id}/stop/` | Stops timer, total time updates |  |  |
| My Tasks | `GET /my/tasks` | Employee sees assigned tasks only |  |  |
| Files Upload | `POST /files/` | Only `.doc/.docx/.md`, one link target |  |  |
| Files List | `GET /files/` | Correct linked name (`project/milestone/task`) |  |  |
| File Delete | `DELETE /files/{id}/` | Delete action and refresh |  |  |
| Notifications List | `GET /notifications/` | Read/unread state shown correctly |  |  |
| Mark Read | `PATCH /notifications/{id}/read/` | Badge count updates immediately |  |  |
| Admin Dashboard | `GET /admin/dashboard` | KPI cards and summaries match API |  |  |
| Admin Overview | `GET /admin/overview` | Filtered overview rendering correct |  |  |
| BA Dashboard | `GET /ba/dashboard` | Employee summary and task metrics |  |  |
| Employee Dashboard | `GET /employee/dashboard` | Today worked seconds/active task data |  |  |

---

## Must-Pass Test Scenarios

- [ ] Admin can complete Users -> Projects -> Milestones -> Tasks flow.
- [ ] BA can assign tasks and track employee progress without permission issues.
- [ ] Employee can start/pause/stop only assigned task timer.
- [ ] Timer sample check: `60s run + 10s pause + 30s run = 90s`.
- [ ] File upload rejects invalid extension and accepts valid document formats.
- [ ] Role-based menu and protected routes show/hide correctly.
- [ ] No page crashes on empty API responses.
- [ ] All critical API errors show clear user message in UI.

---

## Release Gate (No AI)

Integration handoff is approved only if:

- [ ] All rows in Frontend Audit Sheet are `DONE` (or accepted with workaround).
- [ ] No `High` severity mismatch remains open.
- [ ] Backend and Frontend leads sign off.
- [ ] QA validates Admin, BA, Employee end-to-end flows.

---

## Sign-Off

Frontend Lead: ____________________  
Backend Lead: _____________________  
QA Lead: __________________________  
Date: _____________________________

## Source of Truth

- `API_DOCUMENTATION.md`
- `API_MASTER_GUIDE.md`

Base URL:
- `http://127.0.0.1:8000/api/v1`

---

## Integration Strategy (Recommended Order)

1. Authentication and session handling
2. Role-based navigation and route guards
3. Master data screens (Users, Projects, Milestones)
4. Task management + assignment
5. Timer workflow (start/pause/stop)
6. Files and Notifications
7. Dashboards and parity validation
8. Final mismatch log and sign-off

---

## Common API Response Handling

Every API response follows this structure:

```json
{
  "success": true,
  "message": "Text",
  "code": 200,
  "data": {}
}
```

For paginated APIs, UI must support:
- `data.results`
- `meta.page`
- `meta.page_size`
- `meta.total`
- `meta.total_pages`
- `meta.next`
- `meta.previous`

---

## Role Behavior to Enforce in UI

## ADMIN
- Full management scope.
- Access to Users, Projects, Milestones, Tasks, Files, Notifications, Admin Dashboard/Overview.

## BA
- Manage BA-owned task/project scope.
- Assign employees to tasks.
- Access BA dashboard and relevant modules.

## EMPLOYEE
- Own assigned tasks only.
- Timer actions on assigned tasks only.
- My Tasks, Employee dashboard, notifications.

Frontend must hide/disable unauthorized actions (not just rely on backend 403).

---

## Module-by-Module API Usage

## 1) Authentication

Use:
- `POST /auth/login`
- `POST /auth/refresh`
- `GET /auth/me`
- `POST /auth/admin/forgot-password/request-otp`
- `POST /auth/admin/forgot-password/verify-otp`

UI requirements:
- Store and refresh JWT correctly.
- On refresh failure, logout + redirect to login.
- Build role-based UI from `/auth/me`.

---

## 2) Users (Admin)

Use:
- `GET /users/`
- `POST /users/`
- `GET /users/{id}/`
- `PUT /users/{id}/`
- `PATCH /users/{id}/`
- `DELETE /users/{id}/`

UI requirements:
- Pagination support.
- Role/status create and update.
- Handle email/domain validation errors properly.

---

## 3) Projects

Use:
- `GET /projects/`
- `POST /projects/`
- `GET /projects/{id}/`
- `PUT /projects/{id}/`
- `PATCH /projects/{id}/`
- `DELETE /projects/{id}/`

UI requirements:
- Show status, deadline, created_by.
- Validate deadline restrictions by role.
- Provide list + detail + form flow.

---

## 4) Milestones

Use:
- `GET /milestones/`
- `POST /milestones/`
- `GET /milestones/{id}/`
- `PUT /milestones/{id}/`
- `PATCH /milestones/{id}/`
- `DELETE /milestones/{id}/`

UI requirements:
- Show milestone number, project name, status.
- Respect immutable/restricted field behavior.

---

## 5) Tasks

Use:
- `GET /tasks/`
- `POST /tasks/`
- `GET /tasks/{id}/`
- `PUT /tasks/{id}/`
- `PATCH /tasks/{id}/`
- `DELETE /tasks/{id}/`
- `POST /tasks/{id}/assign/`
- `PATCH /tasks/{id}/status/`
- `GET /tasks/{id}/time-logs/`
- `POST /tasks/{id}/request-deadline-change/` (employee flow)

UI requirements:
- Filters/search/sort.
- Proper assignee/project/milestone mapping.
- Status transitions with backend validation.
- Show total tracked time (`total_time_spent_seconds`).

---

## 6) Work Tracking (Current APIs)

Use dedicated endpoint first:

- `GET /work-tracking` (Admin/BA consolidated tracking)

Optional drill-down/enrichment:

- `GET /tasks/` (status + assignee + total time)
- `GET /tasks/{id}/time-logs/` (session history)
- `GET /ba/dashboard` (employee summary in BA scope)
- `GET /admin/overview` (global summary)
- `GET /employee/dashboard` (employee self metrics)

Timer actions:
- `POST /tasks/{id}/start/`
- `POST /tasks/{id}/pause/`
- `POST /tasks/{id}/stop/`

Expected timer behavior:
- run 60s + pause 10s + run 30s = total 90s

---

## 7) My Tasks (Employee)

Use:
- `GET /my/tasks`

UI requirements:
- Show only assigned tasks.
- Provide quick timer and status actions if needed.

---

## 8) Files

Use:
- `POST /files/` (multipart/form-data)
- `GET /files/`
- `GET /files/{id}/`
- `DELETE /files/{id}/`

Rules:
- Only `.doc`, `.docx`, `.md`
- Exactly one link target: `project` or `milestone` or `task`
- Show one of:
  - `project_name`
  - `milestone_name`
  - `task_name`

---

## 9) Notifications

Use:
- `GET /notifications/`
- `PATCH /notifications/{id}/read/`

UI requirements:
- Unread badge.
- Mark-as-read updates local state.

---

## 10) Dashboards

Use:
- `GET /admin/dashboard`
- `GET /admin/overview`
- `GET /ba/dashboard`
- `GET /employee/dashboard`

UI requirements:
- Role-wise dashboard routing.
- KPI cards map exactly to API fields.
- Empty/loading/error states implemented.

---

## API-to-UI Reaction Map (What to call, where, and UI behavior)

Use this as the practical integration map for frontend implementation.

## Auth Screens
- **Login Screen**
  - API: `POST /auth/login`
  - When: On submit login form.
  - Success UI: store tokens, call `/auth/me`, route by role dashboard.
  - Error UI: show inline error ("invalid credentials"/validation).
- **Session Refresh (silent)**
  - API: `POST /auth/refresh`
  - When: access token expired (401 interceptor).
  - Success UI: retry original request automatically.
  - Error UI: clear session and redirect login.
- **Profile Bootstrap**
  - API: `GET /auth/me`
  - When: app startup and after login.
  - Success UI: set user info + role permissions.
  - Error UI: logout fallback.

## Users Module (Admin)
- **Users List Page**
  - API: `GET /users/`
  - When: page load, pagination/filter change.
  - Success UI: render table + pagination meta.
  - Error UI: error state with retry.
- **Create User Modal/Page**
  - API: `POST /users/`
  - When: submit create form.
  - Success UI: close modal, refresh list, success toast.
  - Error UI: field-level errors (email/domain/role/status).
- **Edit User Page**
  - API: `GET /users/{id}/`, `PUT/PATCH /users/{id}/`
  - When: open edit screen, submit update.
  - Success UI: update form/list state.
  - Error UI: preserve form + show validation errors.
- **Delete User Action**
  - API: `DELETE /users/{id}/`
  - When: confirm delete.
  - Success UI: remove row + toast.
  - Error UI: action-level error toast.

## Projects / Milestones / Tasks
- **Projects List**
  - API: `GET /projects/`
  - When: page load/filter change.
  - Success UI: render project cards/table.
- **Project Create/Edit**
  - API: `POST /projects/`, `PUT/PATCH /projects/{id}/`
  - When: submit form.
  - Success UI: navigate/detail refresh.
  - Error UI: show restricted deadline update errors by role.
- **Milestones List/Create/Edit**
  - API: `GET/POST /milestones/`, `PUT/PATCH /milestones/{id}/`
  - Success UI: show `milestone_no`, project mapping.
  - Error UI: immutable/restricted field error handling.
- **Tasks List**
  - API: `GET /tasks/`
  - When: load/filter/search/sort.
  - Success UI: show assignee/status/deadline/total time.
- **Task Create/Edit**
  - API: `POST /tasks/`, `PUT/PATCH /tasks/{id}/`
  - Success UI: refresh task detail/list.
- **Task Assignment**
  - API: `POST /tasks/{id}/assign/`
  - Success UI: assignee chip/name updates immediately.
  - Error UI: "Assignee not found or not allowed".
- **Task Status Update**
  - API: `PATCH /tasks/{id}/status/`
  - Success UI: status badge updates + optional notification indicator.
  - Error UI: invalid status message.

## Work Tracking / Timer
- **Start Timer**
  - API: `POST /tasks/{id}/start/`
  - When: employee clicks start on assigned task.
  - Success UI: status -> IN_PROGRESS, start live timer view.
  - Error UI: show "already active timer" or permission error.
- **Pause Timer**
  - API: `POST /tasks/{id}/pause/`
  - Success UI: status -> PAUSED, freeze timer, refresh totals.
  - Error UI: show "No active timer found for this task."
- **Stop Timer**
  - API: `POST /tasks/{id}/stop/`
  - Success UI: stop timer, show `duration_seconds` and updated `total_time_spent_seconds`.
  - Error UI: active-timer missing message.
- **Time Log History**
  - API: `GET /tasks/{id}/time-logs/`
  - When: open tracking tab/drawer.
  - Success UI: session timeline list.

## Employee Work Views
- **My Tasks Page**
  - API: `GET /my/tasks`
  - Success UI: assigned tasks only, actions based on task state.
- **Employee Dashboard**
  - API: `GET /employee/dashboard`
  - Success UI: show `today_worked_seconds`, active task, completed count.
- **BA Dashboard (Team Tracking)**
  - API: `GET /ba/dashboard`
  - Success UI: employee summary + per-employee task list.
- **Admin Overview (Org Tracking)**
  - API: `GET /admin/overview` (+ optional filters)
  - Success UI: org-wide counts, task status distribution, summaries.

## Files Module
- **Upload File**
  - API: `POST /files/` (multipart)
  - When: submit upload form.
  - Success UI: show uploaded row and linked target name.
  - Error UI: invalid file type/link rule messages.
- **Files List**
  - API: `GET /files/`
  - Success UI: show file metadata and one of `project_name`/`milestone_name`/`task_name`.
- **Delete File**
  - API: `DELETE /files/{id}/`
  - Success UI: remove row.

## Notifications
- **Notification List**
  - API: `GET /notifications/`
  - Success UI: render unread/read sections.
- **Mark as Read**
  - API: `PATCH /notifications/{id}/read/`
  - Success UI: unread badge count decrements.

---

## Integration Validation Checklist

- [ ] Login + token refresh stable.
- [ ] Role-based routes/menu correct.
- [ ] CRUD modules mapped with no field mismatch.
- [ ] Task assign/status/timer flows working.
- [ ] Files rules enforced in UI.
- [ ] Notifications fully wired.
- [ ] Dashboard numbers match backend.
- [ ] No unresolved critical API mismatch.

---

## Missing Item Handling Process

If frontend finds mismatch:

1. Capture module + screen + endpoint.
2. Tag as one:
   - `UI_MISSING`
   - `UI_BACKEND_FIELD_MISMATCH`
   - `BACKEND_GAP`
   - `VALIDATION_GAP`
3. Add expected vs actual payload sample.
4. Assign owner (FE/BE) and severity.

Use this issue row format:

| Module | Screen | Endpoint | Type | Expected | Actual | Owner | Priority | Status |
|--------|--------|----------|------|----------|--------|-------|----------|--------|

---

## Cursor Prompt for Frontend Team

Paste this into Cursor in frontend repo:

```text
Use this backend integration playbook: FRONTEND_INTEGRATION_PLAYBOOK_NO_AI.md

Goal:
1) Verify each module screen against mapped backend APIs.
2) Identify missing UI behaviors, endpoint mismatches, and payload mapping issues.
3) Implement missing frontend pieces required for parity (no AI scope).
4) Produce a final mismatch report table with:
   Module, Screen, Endpoint, Issue Type, Expected, Actual, Owner, Priority, Status.

Constraints:
- Do not add AI features.
- Keep role-based behavior strict (ADMIN/BA/EMPLOYEE).
- Follow API response envelope and pagination format exactly.
- Validate timer and file rules as documented.
```

---
