from control.operations import accept_operation, fix_change_boundary, load_operation, save_verified_progress
from control.recovery import resume_action
from recorder.actions import LocalDevice


def change(db, recording, new_rule, operation_id, device=None):
    op = accept_operation(db, recording, new_rule, operation_id)
    if op.boundary is not None:
        return op
    port = LocalDevice(db) if device is None else device
    if op.enabled_after is None:
        result = resume_action(port, op, 'enable')
        if result is None:
            return load_operation(db, recording, operation_id)
        op = save_verified_progress(db, recording, operation_id, result)
    if op.boundary is not None or op.enabled_after is None:
        return op
    if op.retired_through is None:
        result = resume_action(port, op, 'retire')
        if result is None:
            return load_operation(db, recording, operation_id)
        op = save_verified_progress(db, recording, operation_id, result)
    return fix_change_boundary(db, recording, operation_id, op.enabled_after)
