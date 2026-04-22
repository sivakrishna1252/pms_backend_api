# PMS API Master Guide (Start to End)

This guide explains the complete API usage flow in simple order:
- how Admin sets up the system
- how BA manages execution
- how Employee works on tasks
- where files are uploaded
- where notifications are read

Base URL: `http://127.0.0.1:8000/api/v1`  
Swagger: `http://127.0.0.1:8000/api/docs/swagger/`  
ReDoc: `http://127.0.0.1:8000/api/docs/redoc/`

---

## 1) First Time Setup

1. Start backend (`docker compose up --build` or local runserver).
2. Open Swagger and test login.
3. Keep one Admin account ready.
4. Use Admin account to create BA and Employee users.

---

## 2) Authentication Flow (All Roles)

### Login
`POST /auth/login`

```json
{
  "email": "admin@apparatus.solutions",
  "password": "Admin@1234"
}
```

Use `data.access` token in all protected APIs:
- `Authorization: Bearer <access_token>`

### Refresh token
`POST /auth/refresh`

### Current user profile
`GET /auth/me`

### Admin forgot password (OTP flow)
- `POST /auth/admin/forgot-password/request-otp`
- `POST /auth/admin/forgot-password/verify-otp`

---

## 3) User Management (Admin Only)

### Create users (BA/Employee/Admin)
`POST /users/`

```json
{
  "first_name": "Ravi",
  "last_name": "K",
  "email": "ravi@apparatus.solutions",
  "password": "Temp@123",
  "role": "BA",
  "status": "ACTIVE"
}
```

### User CRUD
- `GET /users/`
- `GET /users/{id}/`
- `PUT /users/{id}/`
- `PATCH /users/{id}/`
- `DELETE /users/{id}/`

Password update in user update API:
- In `PUT/PATCH /users/{id}/`, Admin can send optional `password`.
- Password is updated and password-change email is sent to that user.

### Admin reset any user password
`POST /admin/reset-password`

---

## 4) Project Lifecycle

### Create project
`POST /projects/` (Admin, BA)

### Project CRUD
- `GET /projects/`
- `GET /projects/{id}/`
- `PATCH /projects/{id}/`
- `DELETE /projects/{id}/` (Admin only)

Important rule:
- Only Admin can modify project `deadline`.
- BA cannot modify project `deadline`.

### BA approval requests to Admin
- `POST /projects/{id}/request-deadline-change` (Body: `new_deadline`, `reason`)
- `POST /projects/{id}/request-delete` (Body: `reason`)

These create admin notifications and send emails to active admins.

---

## 5) Milestone Lifecycle

### Create milestone
`POST /milestones/` (Admin, BA)

```json
{
  "project": 1,
  "name": "Backend API",
  "start_date": "2026-04-17",
  "end_date": "2026-04-30",
  "status": "NOT_STARTED"
}
```

### Milestone CRUD
- `GET /milestones/`
- `GET /milestones/{id}/`
- `PATCH /milestones/{id}/`
- `DELETE /milestones/{id}/`

Rules:
- `milestone_no` is auto-generated per project.
- `end_date` cannot be modified once created.

---

## 6) Task Lifecycle and Assignment

### Create task
`POST /tasks/` (Admin, BA)

```json
{
  "project": 1,
  "milestone": 1,
  "title": "Create Auth API",
  "description": "Implement login and me",
  "assigned_to": 3,
  "status": "NOT_STARTED",
  "priority": "HIGH",
  "deadline": "2026-04-25"
}
```

### Task CRUD
- `GET /tasks/`
- `GET /tasks/{id}/`
- `PATCH /tasks/{id}/`
- `DELETE /tasks/{id}/`

### Assign/Reassign task
`POST /tasks/{id}/assign/`

```json
{
  "user_id": 25
}
```

Assignment rules:
- Admin can assign to BA or Employee.
- BA can assign only to Employee.
- For BA, selected project/milestone must be Admin-owned or BA-owned.
- Milestone must belong to selected project.

### Employee time tracking APIs
- `POST /tasks/{id}/start/`
- `POST /tasks/{id}/pause/`
- `POST /tasks/{id}/stop/`
- `GET /tasks/{id}/time-logs/`

### Employee mark status
`PATCH /tasks/{id}/status`

```json
{
  "status": "COMPLETED"
}
```

When Employee marks task `COMPLETED`:
- notification goes to task creator (`created_by`, usually BA/Admin)
- completion email goes to task creator

### Employee deadline request
`POST /tasks/{id}/request-deadline-change/`

```json
{
  "new_deadline": "2026-04-30",
  "reason": "Dependency blocked"
}
```

Only assigned employee can call this.

### My tasks endpoint (Employee-focused)
`GET /my/tasks`

---

## 7) File Upload and Document Management

Use this API for task documents:
- `POST /files/` (`multipart/form-data`)
- `GET /files/`
- `GET /files/{id}/`
- `DELETE /files/{id}/`

Access:
- Upload/update/delete file: Admin, BA only
- Read file list/details: any authenticated user

Form fields in upload:
- `file` (required, binary file)
- `project` (optional project id)
- `milestone` (optional milestone id)
- `task` (optional task id)

Rules:
- Provide exactly one link field: `project` or `milestone` or `task`.
- Use project uploads for project-level docs (SOW, BRD, contracts, etc.).
- Use milestone uploads for milestone-specific documents.
- Use task uploads for implementation/task-level documents.

---

## 8) Notifications

- `GET /notifications/`
- `GET /notifications/{id}/`
- `PATCH /notifications/{id}/read/`

Notification examples:
- task assigned
- task completed
- BA requested project deadline change/delete
- employee requested task deadline change

---

## 9) Dashboards

- `GET /admin/dashboard`
- `GET /admin/overview` (admin-focused complete overview)
- `GET /ba/dashboard`
- `GET /employee/dashboard`

Use dashboards for analytics and monitoring.

---

## 10) End-to-End Real Working Example

1. Admin login: `POST /auth/login`
2. Admin creates BA and Employee: `POST /users/`
3. Admin creates project: `POST /projects/`
4. Admin creates milestone: `POST /milestones/`
5. Admin creates task and assigns to BA: `POST /tasks/` (`assigned_to = BA`)
6. BA reassigns same task to Employee: `POST /tasks/{id}/assign/`
7. Employee views tasks: `GET /my/tasks`
8. Employee starts/pauses/stops task
9. Employee marks completed: `PATCH /tasks/{id}/status`
10. BA/Admin receives completion notification
11. Employee/BA upload attachments if needed: `POST /files/`
12. Users read notifications: `GET /notifications/`
13. Track summary in dashboards

This is the full execution cycle from planning to completion.

---

## 11) Complete Endpoint List

### Auth
- `POST /auth/login`
- `POST /auth/refresh`
- `GET /auth/me`
- `POST /auth/admin/forgot-password/request-otp`
- `POST /auth/admin/forgot-password/verify-otp`

### Admin utilities
- `POST /admin/reset-password`
- `GET /admin/dashboard`
- `GET /admin/overview`

### Role dashboards
- `GET /ba/dashboard`
- `GET /employee/dashboard`

### Users
- `POST /users/`
- `GET /users/`
- `GET /users/{id}/`
- `PUT /users/{id}/`
- `PATCH /users/{id}/`
- `DELETE /users/{id}/`

### Projects
- `POST /projects/`
- `GET /projects/`
- `GET /projects/{id}/`
- `PUT /projects/{id}/`
- `PATCH /projects/{id}/`
- `DELETE /projects/{id}/`
- `POST /projects/{id}/request-deadline-change`
- `POST /projects/{id}/request-delete`

### Milestones
- `POST /milestones/`
- `GET /milestones/`
- `GET /milestones/{id}/`
- `PUT /milestones/{id}/`
- `PATCH /milestones/{id}/`
- `DELETE /milestones/{id}/`

### Tasks
- `POST /tasks/`
- `GET /tasks/`
- `GET /tasks/{id}/`
- `PUT /tasks/{id}/`
- `PATCH /tasks/{id}/`
- `DELETE /tasks/{id}/`
- `POST /tasks/{id}/assign/`
- `PATCH /tasks/{id}/status`
- `POST /tasks/{id}/start/`
- `POST /tasks/{id}/pause/`
- `POST /tasks/{id}/stop/`
- `GET /tasks/{id}/time-logs/`
- `POST /tasks/{id}/request-deadline-change/`
- `GET /my/tasks`

### Files
- `POST /files/`
- `GET /files/`
- `GET /files/{id}/`
- `DELETE /files/{id}/`

### Notifications
- `GET /notifications/`
- `GET /notifications/{id}/`
- `PATCH /notifications/{id}/read/`

---

## 12) Key Rules Summary

- Admin can do everything in system.
- BA can manage projects/milestones/tasks in allowed scope.
- Employee works only on assigned tasks.
- Only assigned employee can start/pause/stop/request task deadline change.
- Only Admin can change project deadline.
- Milestone end date can be edited only by the milestone creator.
- Task completion notifies task creator.
- File uploads happen only in `/files/` API.

