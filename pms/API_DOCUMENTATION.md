# Project Management System API Documentation

Base URL: `http://127.0.0.1:8000/api/v1`

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
  "password": "123456"
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
  "role": "EMPLOYEE",
  "status": "ACTIVE"
}
```

### PATCH `/users/{id}/`
Body (partial):
```json
{
  "first_name": "Ravi Updated"
}
```

### DELETE `/users/{id}/`

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
> Note: `deadline` cannot be modified once created.

Body:
```json
{
  "name": "PMS Updated",
  "status": "ACTIVE"
}
```

### DELETE `/projects/{id}/` (Admin only)

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

### GET `/milestones/{id}/`

### PATCH `/milestones/{id}/`
> Note: `end_date` cannot be modified once created.

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

### GET `/tasks/`
Pagination query params:
- `?page=1&page_size=10`

### GET `/tasks/{id}/`
### PATCH `/tasks/{id}/`
### DELETE `/tasks/{id}/`

### POST `/tasks/{id}/assign/` (Admin, BA)
Body:
```json
{
  "user_id": 3
}
```

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
Form-data fields:
- `task` (optional, integer)
- `file` (required, file)

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

---

## 9) Dashboard APIs

### GET `/admin/dashboard`
### GET `/ba/dashboard`
### GET `/employee/dashboard`

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
- Project `deadline` is immutable after create
- Milestone `end_date` is immutable after create

---

## 11) Common Error Response

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

## 12) Quick Postman Test Order

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
