"""Optional presentation cannot displace query evidence or mandatory semantics."""
from domain.turn_interpretation import ResolvedTurnInterpretation


def presentation_fits(turn: ResolvedTurnInterpretation, remaining_tokens: int,
                      counter, tokenizer_id: str) -> bool:
    if not turn.presentation.projection():
        return True
    # Count the entire serialized turn block conservatively; required semantics
    # are already budgeted. This may omit optional presentation earlier, safely.
    return counter.count_tokens(turn.agent_context(), tokenizer_id=tokenizer_id) <= remaining_tokens
