from control.operations import ChangeOperation
from recorder.actions import ActionResult


def _expected_lane(operation, action):
    if action == 'enable':
        return operation.new_lane
    if action == 'retire':
        return operation.old_lane
    raise ValueError('unknown action kind')


def read_verified_action(device, operation: ChangeOperation, action):
    lane = _expected_lane(operation, action)
    result = device.lookup(operation.recording, operation.operation_id, action)
    if (isinstance(result, ActionResult)
        and (result.recording, result.operation_id, result.action, result.lane)
        == (operation.recording, operation.operation_id, action, lane)
        and type(result.boundary) is int and result.boundary >= 0):
        return result
    return None


def resume_action(device, operation: ChangeOperation, action):
    result = read_verified_action(device, operation, action)
    if result is not None:
        return result
    lane = _expected_lane(operation, action)
    device.request(operation.recording, operation.operation_id, action, lane)
    return read_verified_action(device, operation, action)
