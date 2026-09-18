"""Order signed epoch snapshots without allowing an old retry to replace newer scores."""

from validator.reporting.protocol import SignedScoreReport


class SupersededReport(ValueError):
    """The epoch already contains a newer authenticated report from this validator."""

    def __init__(self, report_id: str) -> None:
        super().__init__("A newer signed epoch snapshot is already stored")
        self.report_id = report_id


def check_replacement(previous: SignedScoreReport, incoming: SignedScoreReport) -> None:
    """Allow the same snapshot or a later completed round for the same epoch.

    Raises:
        ValueError: If either signature is invalid, the contexts differ, or equal positions conflict.
        SupersededReport: If the stored snapshot is newer than the incoming report.
    """
    previous.verify()
    incoming.verify()
    old, new = previous.report, incoming.report
    if (previous.object_key, old.chain_genesis, old.netuid) != (incoming.object_key, new.chain_genesis, new.netuid):
        raise ValueError("Stored snapshot belongs to a different validator, epoch or chain")
    if previous.report_id == incoming.report_id:
        return
    old_position = (old.completed_block, old.completed_at)
    new_position = (new.completed_block, new.completed_at)
    if old_position == new_position and old.instance_id == new.instance_id:
        if old.round_id != new.round_id:
            if old.round_id > new.round_id:
                raise SupersededReport(previous.report_id)
            return
    if old_position > new_position:
        raise SupersededReport(previous.report_id)
    if old_position == new_position:
        raise ValueError("Conflicting signed snapshots at the same completion position")
