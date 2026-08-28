"""Debug script to check what DeepSeek actually returns for quiz generation."""
import os, json, sys
os.environ['ENABLE_STRUCTURED_OUTPUTS_QUIZ'] = '0'

from core.agents.quizmaster import QuizMasterAgent
from core.llm.openai_compat import get_llm_client
from core.orchestration.prompts import QUIZMASTER_SOLVE_SYSTEM_PROMPT, QUIZMASTER_PROMPT

q = QuizMasterAgent()
llm = get_llm_client()

prompt = QUIZMASTER_PROMPT.format(
    course_name='test', topic='特征值', difficulty='medium',
    context='(context)', rag_context='', history_context='',
    memory_context='', memory_ctx='', num_questions=1, question_type='综合题',
)

messages = [
    {'role': 'system', 'content': QUIZMASTER_SOLVE_SYSTEM_PROMPT},
    {'role': 'user', 'content': prompt}
]

print('=== Calling DeepSeek ===')
resp = llm.chat(messages, temperature=0.4, max_tokens=1400)
print(f'Response type: {type(resp).__name__}')
print(f'Response is None: {resp is None}')
print(f'Response length: {len(resp) if resp else 0}')
if resp:
    print(f'Response first 300 chars:')
    print(repr(resp[:300]))
else:
    print('Response is EMPTY or NONE')

print()
print('=== Trying JSON extraction ===')
try:
    result = q._extract_json_payload(resp)
    print('Parsed OK:', result['question'][:80])
except ValueError as e:
    print(f'FAILED: {e}')
    raw = str(resp or '')
    print(f'Has ```json block: {("```json" in raw)}')
    first = raw.find('{')
    last = raw.rfind('}')
    print(f'First brace at index {first}, last at index {last}')
    if first >= 0 and last > first:
        candidate = raw[first:last+1]
        print(f'Brace-range (first 150 chars): {repr(candidate[:150])}')
        try:
            json.loads(candidate)
            print('Brace-range IS valid JSON!')
        except json.JSONDecodeError as je:
            print(f'NOT valid JSON: {je}')
    # Check for error string
    if resp and 'Error calling LLM' in str(resp):
        print('LLM CALL FAILED - check API key/network')
