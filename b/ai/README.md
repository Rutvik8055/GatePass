# Future camera recognition integration

The present release keeps the gate workflow deliberately manual and fast: the operator verifies the vehicle number before it becomes an entry record.

An OCR service can be attached without changing the visitor workflow:

1. Camera capture is sent to a separate protected recognition worker.
2. The worker uses OpenCV/YOLO for vehicle and plate detection, then OCR for characters.
3. Its confidence-scored number is shown in the existing Entry form for operator confirmation.
4. On approval, the regular `POST /api/visits/entry` endpoint validates duplicates, persists the visit, creates an audit record, and broadcasts the live update.

The recognition worker should never bypass the normal entry endpoint or silently create a visit at low confidence.
