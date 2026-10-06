# TourGuard AI API

Every endpoint except `POST /api/auth/login` and `GET /api/health` requires `Authorization: Bearer <token>`.

## Sign in

`POST /api/auth/login`

```json
{"email":"gate@tourguard.local","password":"Gate@123"}
```

## Register entry (administrator or operator)

`POST /api/visits/entry`

```json
{"group_name":"Mehta Family","mobile":"9876543210","tourist_count":4,"entry_gate":"Main Gate","vehicle_type":"Car","vehicle_number":"MH12AB1234","vehicle_model":"Swift"}
```

The response contains `qr_payload`. TourGuard places that opaque, server-validated token in the visitor pass QR code. The scanner uses it with `GET /api/visits/lookup/{qr_payload}` and then securely registers the exit only while the visit is still `INSIDE`.

## Register exit (administrator or operator)

`POST /api/visits/{id}/exit`

```json
{"exit_gate":"Main Gate"}
```

## Reading data

- `GET /api/dashboard`
- `GET /api/visits?search=&status=&vehicle_type=&gate=&page=1&limit=10`
- `GET /api/visits/inside`
- `GET /api/visits/lookup/{vehicle-or-visitor-id-or-mobile-or-qr-token}`
- `GET /api/vehicles`, `GET /api/vehicles/{vehicle_number}`
- `GET /api/parking`, `GET /api/analytics`, `GET /api/ai/insights`
- `GET /api/reports?date_from=2026-10-01&date_to=2026-10-06`
- `GET /api/notifications`, `GET /api/stream?token={token}`

Administrative routes are `GET/POST /api/users`, `GET/POST /api/gates`, `PUT /api/parking`, and `GET/PUT /api/settings`.
