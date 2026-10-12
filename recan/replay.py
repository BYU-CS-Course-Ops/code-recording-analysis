"""Validate recorder edits before either analysis or history matching.

Never search for a replacement elsewhere in the file: repeated source text makes
that ambiguous. Keep the last reliable document until a recorder snapshot can
restore it. Input events are retained by the caller and are never mutated.
"""

from recan.utils import splice


def validated_events(events: list[dict]) -> list[dict]:
    result = []
    document = ""
    interrupted = None

    def issue(event, index, action, message):
        marker = {
            "type": "recordingIssue", "timestamp": event["timestamp"],
            "document": event.get("document", ""),
            "event_index": index, "action": action, "message": message,
            "skipped_edits": 0,
        }
        result.append(marker)
        return marker

    for index, original in enumerate(events):
        event = original.copy()
        kind = event.get("type") or ("edit" if "newFragment" in event else "unknown")
        if kind != "edit":
            result.append(event)
            continue
        offset = event.get("offset", 0)
        old = event.get("oldFragment", "")
        new = event.get("newFragment", "")

        # The recorder represents full-document snapshots as identical old/new
        # text at offset zero. They are observations, never student paste events.
        if offset == 0 and old == new:
            if index == 0:
                document = new
                result.append(event)
            elif interrupted is not None or document != new:
                issue(event, index, "resynchronized",
                      "Reconstruction restored from a recorder snapshot.")
                document = new
                interrupted = None
                result.append({**event, "type": "documentSnapshot", "document_text": new})
            continue

        if interrupted is not None:
            interrupted["skipped_edits"] += 1
            result.append({"type": "unreliableEdit", "timestamp": event["timestamp"]})
            continue

        valid_offset = isinstance(offset, int) and 0 <= offset <= len(document)
        if valid_offset and document[offset:offset + len(old)] == old:
            if old != new:
                document = splice(document, offset, old, new)
                result.append(event)
            continue

        if valid_offset and old and new and document[offset:offset + len(new)] == new:
            marker = issue(event, index, "already_applied",
                           "Skipped a stale replacement: its new text was already present.")
            marker["skipped_edits"] = 1
            continue

        interrupted = issue(event, index, "interrupted",
                            "Recording text or offset did not match. Reconstruction and paste "
                            "analysis are paused until a full-document snapshot.")
        interrupted["skipped_edits"] = 1

    return result
