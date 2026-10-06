# Scanner task

## Goal
Complete the visitor-exit scanning flow so operators can verify a QR pass with a camera scan and record the exit without re-entering visitor details.

## Scope
- Add a camera-based QR scanner on the exit screen.
- Require the operator to choose an exit gate before scanning.
- Accept the QR payload from the visitor pass and look it up against active visits.
- Validate that the matched visit is still `INSIDE` before allowing exit.
- Record the exit on the server and refresh the dashboard in real time.
- Provide a manual fallback lookup when camera scanning fails or the pass is unreadable.

## Acceptance criteria
- An operator can open the exit view, select a gate, and start the camera scanner.
- The browser requests camera permission only after the operator starts scanning.
- QR codes from the visitor pass are decoded successfully in the browser.
- A valid QR payload resolves to the active visit record.
- The exit is rejected if the pass is expired, already exited, or the record no longer matches an active visit.
- A manual entry fallback still allows the operator to find and exit an active visit.
- The UI shows clear status/error messaging and stops the camera cleanly when the operator ends the scan.

## Notes
- Use the existing `GET /api/visits/lookup/{qr_payload}` flow to validate the pass server-side.
- Keep the QR token opaque and never trust the client to decide exit eligibility.
- For browsers without native QR detection, use the jsQR fallback so the flow still works with a camera.

## Status
Remaining implementation task: scanner operational flow and validation in the exit workflow.
