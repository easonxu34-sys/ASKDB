"""The single coordinator of answerability and validated memory issues."""
from dataclasses import dataclass
from typing import Literal
import re

from domain.turn_interpretation import ResolvedTurnInterpretation


@dataclass(frozen=True)
class QueryDecision:
    status: Literal['READY', 'CLARIFY', 'INTERNAL']
    clarification: str | None = None


def decide_with_issues(gate_output: str, turn: ResolvedTurnInterpretation) -> QueryDecision:
    if gate_output == 'INTERNAL':
        return QueryDecision('INTERNAL')
    required = [x for x in turn.semantics.issues if x.required]
    if required:
        issue = required[0]
        if not re.search(r'[\u4e00-\u9fff]', turn.question):
            return QueryDecision('CLARIFY', 'A personal scope, metric or analysis preference could not be verified. '
                                 'Please confirm it, or explicitly continue without personal memory for this turn.')
        messages = {
            'INVALID_MEMORY_PAYLOAD': '有一条个人记忆的类别与内容不一致，请在个人偏好中修正，或明确本轮不用个人记忆。',
            'CONFLICTING_DEFAULTS': '本次分析有相互冲突的个人条件或方法，请确认采用哪一项。',
            'UNSUPPORTED_CONSTRAINT': '保存的分析方法暂不能安全复用，请明确本次分析步骤，或本轮不用个人记忆。',
            'STALE_BINDING': '个人条件引用的语义版本已变化，请重新确认该条件，或本轮不用个人记忆。',
        }
        return QueryDecision('CLARIFY', messages.get(issue.code,
            '有个人范围或指标口径尚未确认，请补充该条件，或明确本轮不用个人记忆。'))
    return QueryDecision('READY' if gate_output == 'READY' else 'CLARIFY')
