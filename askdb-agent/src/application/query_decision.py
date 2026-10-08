"""The single coordinator of answerability and validated memory issues."""
from dataclasses import dataclass
from typing import Literal
import re

from domain.turn_interpretation import ResolvedTurnInterpretation


@dataclass(frozen=True)
class QueryDecision:
    status: Literal['READY', 'CLARIFY', 'INTERNAL']
    clarification: str | None = None


def decide_with_issues(
    gate_output: str,
    turn: ResolvedTurnInterpretation,
    *,
    apply_presentation: bool = True,
) -> QueryDecision:
    if gate_output == 'INTERNAL':
        return QueryDecision('INTERNAL')
    required = [x for x in turn.semantics.issues if x.required]
    if required:
        issue = required[0]
        language = str(turn.presentation.language or "").casefold() if apply_presentation else ""
        prefers_english = language in {"english", "英文", "en"} if language else not re.search(
            r'[\u4e00-\u9fff]', turn.question
        )
        if prefers_english:
            return QueryDecision('CLARIFY', 'A personal scope, metric or analysis preference could not be verified. '
                                 'Please confirm it, or explicitly continue without personal memory for this turn.')
        messages = {
            'INVALID_MEMORY_PAYLOAD': '有一条个人记忆的类别与内容不一致，请在个人偏好中修正，或明确本轮不用个人记忆。',
            'CONFLICTING_DEFAULTS': '本次分析有相互冲突的个人条件或方法，请确认采用哪一项。',
            'UNSUPPORTED_CONSTRAINT': '保存的分析方法暂不能安全复用，请明确本次分析步骤，或本轮不用个人记忆。',
            'STALE_BINDING': '个人条件引用的语义版本已变化，请重新确认该条件，或本轮不用个人记忆。',
        }
        english_messages = {
            'INVALID_MEMORY_PAYLOAD': 'A personal preference has an invalid category or value. Please correct it in Personal Preferences, or continue this turn without personal memory.',
            'CONFLICTING_DEFAULTS': 'Personal conditions or methods conflict for this analysis. Which one should I use?',
            'UNSUPPORTED_CONSTRAINT': 'The saved analysis method cannot be safely reused. Please specify the steps for this analysis, or continue this turn without personal memory.',
            'STALE_BINDING': 'A saved personal condition refers to a changed semantic version. Please confirm it again, or continue this turn without personal memory.',
        }
        fallback_zh = '有个人范围或指标口径尚未确认，请补充该条件，或明确本轮不用个人记忆。'
        fallback_en = 'A personal scope or metric definition is not confirmed. Please provide it, or explicitly continue this turn without personal memory.'
        return QueryDecision('CLARIFY', (english_messages if prefers_english else messages).get(
            issue.code, fallback_en if prefers_english else fallback_zh))
    return QueryDecision('READY' if gate_output == 'READY' else 'CLARIFY')
