# Project Management System API Documentation

Base URL: `http://127.0.0.1:8000/api/v1`
Swagger UI: `http://127.0.0.1:8000/api/docs/swagger/`
ReDoc: `http://127.0.0.1:8000/api/docs/redoc/`

## Run With Docker

From project root (`pms/`):

```bash
docker compose up --build
```

Then access:
- API base: `http://127.0.0.1:8000/api/v1`
- Swagger: `http://127.0.0.1:8000/api/docs/swagger/`

Stop containers:
```bash
docker compose down
```

### First-time dashboard admin (optional)

Creates the sample login from the Postman section (`admin@apparatus.solutions` / `Admin@1234`) with `UserProfile` role **ADMIN**:

```bash
python manage.py bootstrap_dashboard_admin
```

From Docker (after the DB is reachable):

```bash
docker exec -it pms-web python manage.py bootstrap_dashboard_admin
```

Use `--force-password` if that user already exists and you need to reset the password.

### Production Docker (Gunicorn)

Build and run production profile:

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

View logs:
```bash
docker compose -f docker-compose.prod.yml logs -f
```

Stop production containers:
```bash
docker compose -f docker-compose.prod.yml down
```

---

## 1) Postman Setup

### Step 1: Create a collection
- Collection name: `PMS API`

### Step 2: Add common header
- `Content-Type: application/json`

### Step 3: Login and save token
Request:
- Method: `POST`
- URL: `{{base_url}}/auth/login`
- Body:
```json
{
  "email": "admin@apparatus.solutions",
  "password": "Admin@1234"
}
```

Success response:
```json
{
  "success": true,
  "message": "Login successful.",
  "code": 200,
  "data": {
    "access": "your_access_token_here",
    "refresh": "your_refresh_token_here",
    "user": {
      "id": 1,
      "first_name": "Admin",
      "last_name": "User",
      "email": "admin@apparatus.solutions",
      "role": "ADMIN",
      "status": "ACTIVE",
      "date_joined": "2026-04-17T10:00:00Z",
      "last_login": "2026-04-17T10:05:00Z"
    }
  }
}
```

Copy `data.access` and set Authorization header for protected APIs:
- `Authorization: Bearer <access_token>`  
  You can also send the access JWT alone (no `Bearer ` prefix); the API accepts both.

To get a new access token when it expires:
- `POST /auth/refresh`
- Body:
```json
{
  "refresh": "your_refresh_token_here"
}
```

### Step 4: Base URL variable
Set Postman variable:
- `base_url = http://127.0.0.1:8000/api/v1`

---

## 2) Auth APIs

### POST `/auth/login`
Body:
```json
{
  "email": "admin@apparatus.solutions",
  "password": "123456"
}
```

### GET `/auth/me`
Headers:
- `Authorization: Bearer <access_token>`

### POST `/auth/admin/forgot-password/request-otp` (Admin only flow)
Body:
```json
{
  "email": "admin@apparatus.solutions"
}
```
Behavior:
- Works only for users with role `ADMIN` and `ACTIVE` status.
- If email is not found -> `404`
- If email belongs to non-admin user -> `403`
- If admin account is inactive -> `403`
- If valid active admin email -> OTP is sent.

### POST `/auth/admin/forgot-password/verify-otp` (Admin only flow)
Body:
```json
{
  "email": "admin@apparatus.solutions",
  "otp": "123456",
  "new_password": "NewPassword@123"
}
```
If OTP is valid, admin password is updated.

Response:
```json
{
  "success": true,
  "message": "User profile fetched.",
  "code": 200,
  "data": {
    "id": 1,
    "first_name": "Admin",
    "last_name": "User",
    "email": "admin@apparatus.solutions",
    "role": "ADMIN",
    "status": "ACTIVE"
  }
}
```

---

## 3) User APIs (Admin Only)

### POST `/users/`
Body:
```json
{
  "first_name": "Ravi",
  "last_name": "K",
  "email": "ravi@apparatus.solutions",
  "password": "123456",
  "role": "EMPLOYEE",
  "status": "ACTIVE"
}
```

### GET `/users/`
Pagination query params:
- `?page=1&page_size=10`

Paginated response format:
```json
{
  "success": true,
  "message": "Data fetched successfully.",
  "code": 200,
  "data": {
    "results": []
  },
  "meta": {
    "page": 1,
    "page_size": 10,
    "total": 5,
    "total_pages": 1,
    "next": null,
    "previous": null
  }
}
```

### GET `/users/{id}/`

### PUT `/users/{id}/`
Body (full):
```json
{
  "first_name": "Ravi",
  "last_name": "K",
  "email": "ravi@apparatus.solutions",
  "password": "NewTemp@123",
  "role": "EMPLOYEE",
  "status": "ACTIVE"
}
```
Notes:
- `password` is optional in update.
- If Admin sends `password`, user password is updated and an email is sent to that user with the new password.

### PATCH `/users/{id}/`
Body (partial):
```json
{
  "first_name": "Ravi Updated",
  "password": "NewTemp@123"
}
```

### DELETE `/users/{id}/`

### POST `/admin/reset-password` (Admin only)
Body:
```json
{
  "email": "employee@apparatus.solutions",
  "new_password": "NewPassword@123"
}
```

---

## 4) Project APIs

### POST `/projects/` (Admin, BA)
Body:
```json
{
  "name": "Project Management System",
  "description": "Build employee tracking system",
  "start_date": "2026-04-17",
  "deadline": "2026-05-30",
  "status": "PLANNED"
}
```

### GET `/projects/`
Pagination query params:
- `?page=1&page_size=10`

### GET `/projects/{id}/`

### PATCH `/projects/{id}/`
> Note: only Admin can modify `deadline`. BA cannot change project deadline.

Body:
```json
{
  "name": "PMS Updated",
  "status": "ACTIVE"
}
```

### DELETE `/projects/{id}/` (Admin only)

### POST `/projects/{id}/request-deadline-change` (BA -> Admin approval request)
Body:
```json
{
  "new_deadline": "2026-06-10",
  "reason": "Client requested extension"
}
```
Creates admin notifications and sends email to active admins.

### POST `/projects/{id}/request-delete` (BA -> Admin approval request)
Body:
```json
{
  "reason": "Project merged into another project"
}
```
Creates admin notifications and sends email to active admins.

---

## 5) Milestone APIs

### POST `/milestones/` (Admin, BA)
Body:
```json
{
  "project": 1,
  "name": "Backend API Creation",
  "start_date": "2026-04-17",
  "end_date": "2026-04-30",
  "status": "NOT_STARTED"
}
```

### GET `/milestones/`
Pagination query params:
- `?page=1&page_size=10`

Milestone numbering behavior:
- API now returns `milestone_no` for each milestone.
- `milestone_no` is project-wise sequence (`1,2,3...`) so each project has its own milestone numbering.
- Global DB `id` still exists for stable references.

### GET `/milestones/{id}/`

### PATCH `/milestones/{id}/`
> Note: `end_date` can be modified only by the user who created that milestone.

Body:
```json
{
  "name": "Backend Phase 1",
  "status": "IN_PROGRESS"
}
```

### DELETE `/milestones/{id}/`

---

## 6) Task APIs

### POST `/tasks/` (Admin, BA)
Body:
```json
{
  "project": 1,
  "milestone": 1,
  "title": "Create Auth API",
  "description": "Implement login and me endpoint",
  "assigned_to": 3,
  "status": "NOT_STARTED",
  "priority": "HIGH",
  "deadline": "2026-04-25"
}
```
Assignment rules:
- Admin can assign task directly to BA or Employee using `assigned_to`.
- BA can assign task only to Employee.
- For BA, selected `project`/`milestone` must be BA-owned or Admin-owned, and milestone must belong to selected project.

### GET `/tasks/`
Pagination query params:
- `?page=1&page_size=10`

### GET `/tasks/{id}/`
### PATCH `/tasks/{id}/`
### DELETE `/tasks/{id}/`

### POST `/tasks/{id}/request-deadline-change/` (Assigned Employee -> BA/Admin)
Body:
```json
{
  "new_deadline": "2026-04-30",
  "reason": "Dependency task is blocked"
}
```
Only assigned employee can call this. Request creates notification for task creator and sends email.

### POST `/tasks/{id}/assign/` (Admin, BA)
Body:
```json
{
  "user_id": 3
}
```
Assignment rules:
- Admin can assign/reassign to BA or Employee.
- BA can assign/reassign only to Employee.

### PATCH `/tasks/{id}/status`
Body:
```json
{
  "status": "COMPLETED"
}
```

Allowed task statuses:
- `NOT_STARTED`
- `IN_PROGRESS`
- `PAUSED`
- `COMPLETED`
- `DELAYED`
- `BLOCKED`

### POST `/tasks/{id}/start/`
Body:
```json
{}
```
Response includes task status as `IN_PROGRESS`.

### POST `/tasks/{id}/pause/`
Body:
```json
{}
```
Response includes task status as `PAUSED`.

### POST `/tasks/{id}/stop/`
Body:
```json
{}
```
Response includes `duration_seconds` and cumulative `total_time_spent_seconds`.

### GET `/tasks/{id}/time-logs/`
Use this endpoint to check worked time history per task.

### Employee task lifecycle (recommended flow)
1. Employee opens assigned task from `GET /my/tasks` or `GET /tasks/{id}/`
2. Start work: `POST /tasks/{id}/start/`
3. Pause break: `POST /tasks/{id}/pause/`
4. Resume work: `POST /tasks/{id}/start/` again
5. Stop work: `POST /tasks/{id}/stop/`
6. Mark completion: `PATCH /tasks/{id}/status` with `"COMPLETED"`
7. Verify tracked time: `GET /tasks/{id}/time-logs/`

### GET `/my/tasks`

---

## 7) File APIs

### POST `/files/`
Access:
- Admin, BA only

Content-Type:
- `multipart/form-data`

Form-data fields:
- `project` (optional, integer)
- `milestone` (optional, integer)
- `task` (optional, integer)
- `file` (required, file)

Important:
- `file` must be an actual uploaded binary file. Sending a JSON string/path/URL is invalid.
- Provide exactly one purpose field: `project` or `milestone` or `task`.
- Response includes file `id`; keep it for `GET /files/{id}/` or `DELETE /files/{id}/`.

### GET `/files/`
### GET `/files/{id}/`
### DELETE `/files/{id}/`

---

## 8) Notification APIs

### GET `/notifications/`
### GET `/notifications/{id}/`

### PATCH `/notifications/{id}/read/`
Body:
```json
{}
```

Why notifications are separate from files:
- File upload stores document data for a task (`/files/`).
- Notifications store user alerts/events (`/notifications/`), such as task assigned/completed.
- These serve different frontend screens and lifecycle actions.

---

## 9) Dashboard APIs

### GET `/admin/dashboard`
### GET `/ba/dashboard`
### GET `/employee/dashboard`
### GET `/admin/overview` (Admin only)

Admin behavior update:
- `/admin/dashboard` now returns the same full payload shape as `/admin/overview` for consistency.
- Prefer `/admin/overview` for admin UI integration.

Use this endpoint for a single monitoring response that combines:
- overall platform counts
- task status distribution
- BA-wise work summary
- Employee-wise work and time summary
- Project/Milestone/Task-level activity with both ID and Name fields

`active_timers` meaning:
- Count of currently running time logs (`TimeLog` records with `end_time = null`).
- It shows how many timers are live right now across visible tasks.

BA dashboard (`/ba/dashboard`) now includes employee progress summary for BA-created tasks:
- `tasks_created`
- `tasks_completed`
- `tasks_in_progress`
- `tasks_delayed`
- `assigned_employees`
- `employee_summary[]` with each employee's:
  - `assigned_tasks`, `completed_tasks`, `in_progress_tasks`, `delayed_tasks`
  - `tasks[]` list with task id/title/status/project/milestone/deadline/time spent

Optional query params:
- `project_id` -> filter overview for one project
- `milestone_id` -> filter overview for one milestone
- `task_id` -> filter overview for one task

Sample response shape:
```json
{
  "success": true,
  "message": "Admin overview fetched.",
  "code": 200,
  "data": {
    "filters": {
      "project_id": "2",
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
    "ba_summary": [],
    "employee_summary": [],
    "projects": [],
    "milestones": [],
    "tasks": []
  }
}
```

Each dashboard response is role-based and returned in:
```json
{
  "success": true,
  "message": "Dashboard fetched.",
  "code": 200,
  "data": {}
}
```

---

## 10) Validation Rules

- Email must end with `@apparatus.solutions`
- Protected APIs require `Authorization: Bearer <access_token>`
- Only assigned employee can start/pause/stop their task
- Only Admin can modify project `deadline`
- Milestone `end_date` is immutable after create
- User creation triggers an email with login credentials to that user
- Task completion triggers an email to task creator (BA/Admin)

---

## 11) Automated Deadline Mail Job

Run this command daily (scheduler/cron):

```bash
python manage.py send_deadline_notifications
```

What it does:
- At/after 8:00 PM local time, any active task timers are auto-stopped and reminder mail is sent to those employees
- Project due tomorrow and not completed -> mail to all active Admin + BA users
- Project overdue and not completed -> status auto-set to `DELAYED` + mail to Admin + BA
- Milestone overdue and not completed -> status auto-set to `DELAYED` + mail to milestone creator
- Task overdue and not completed -> status auto-set to `DELAYED` + mail to task creator (typically BA/Admin)

---

## 12) Name Fields For Frontend

For easier UI display, APIs now include name fields along with IDs:
- Projects: `created_by_name`
- Milestones: `milestone_no`, `project_name`, `created_by_name`
- Tasks: `project_name`, `milestone_name`, `assigned_to_name`, `created_by_name` (and milestone number in dashboard payloads)

You can still send IDs in request body (`project`, `milestone`, `assigned_to`), but use these `*_name` fields in response to display readable labels in frontend tables/cards.

---

## 13) Common Error Response

```json
{
  "success": false,
  "message": "Validation failed.",
  "code": 400,
  "data": {
    "field_name": [
      "error message"
    ]
  }
}
```

---

## 14) Quick Postman Test Order

1. `POST /auth/login` (Admin user)
2. `POST /users/` (create BA/Employee)
3. `POST /projects/`
4. `POST /milestones/`
5. `POST /tasks/`
6. `POST /tasks/{id}/assign/` (optional reassignment)
7. Employee token login
8. `POST /tasks/{id}/start/` -> `pause/` -> `stop/`
9. `PATCH /tasks/{id}/status`
10. Check `GET /notifications/` and dashboards
11. `GET /work-tracking` (Admin/BA)

---

## 15) Work Tracking API (Admin/BA)

### GET `/work-tracking`
Headers:
- `Authorization: Bearer <access_token>`

Who can access:
- `ADMIN`
- `BA`

Query params (all optional):
- `employee_id`
- `project_id`
- `milestone_id`
- `task_id`
- `status`
- `only_active=true` (returns only active started timers)

What it returns:
- Employee identity (`employee_id`, `employee_name`, `employee_email`)
- Project / milestone / task details
- Task status + timer state:
  - `STARTED`
  - `PAUSED`
  - `STOPPED`
- Time tracking fields:
  - `current_session_start_time`
  - `current_session_seconds`
  - `last_session_end_time`
  - `today_worked_seconds`
  - `total_time_spent_seconds`

Response (sample):
```json
{
  "success": true,
  "message": "Work tracking fetched.",
  "code": 200,
  "data": {
    "filters": {
      "employee_id": null,
      "project_id": null,
      "milestone_id": null,
      "task_id": null,
      "status": null,
      "only_active": null
    },
    "summary": {
      "records_count": 2,
      "started_count": 1,
      "paused_count": 1,
      "stopped_count": 0
    },
    "work_tracking": [
      {
        "employee_id": 5,
        "employee_name": "Ravi Kumar",
        "employee_email": "ravi@apparatus.solutions",
        "project_id": 3,
        "project_name": "CRM Revamp",
        "milestone_id": 11,
        "milestone_no": 2,
        "milestone_name": "Backend APIs",
        "task_id": 28,
        "task_title": "Build reporting endpoints",
        "task_status": "IN_PROGRESS",
        "timer_state": "STARTED",
        "current_session_start_time": "2026-04-24T09:10:00Z",
        "current_session_seconds": 420,
        "last_session_end_time": "2026-04-24T08:30:00Z",
        "today_worked_seconds": 5400,
        "total_time_spent_seconds": 12800
      }
    ]
  }
}
```
