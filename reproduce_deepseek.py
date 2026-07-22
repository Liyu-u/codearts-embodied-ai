"""DeepSeek bug reproduction script."""
import sys, os, json, traceback, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s: %(message)s')

from robot_intent_agent.demo.web_ui import pipeline, CASES

# Use the default test case
case = CASES['易碎玻璃杯高速抓取 (安全拦截)']
instr = case['instr']
obs = case['obs']

# Use DeepSeek with a FAKE key to trigger the API path (will fail at auth, then fallback)
# The fallback should work. The crash happens INSIDE the LLM path before fallback.
api_key = os.environ.get('RIA_DEEPSEEK_API_KEY', 'sk-fake-for-test')

print(f'Instruction: {instr}')
print(f'Obs first 80 chars: {obs[:80]}')
print(f'Engine: DeepSeek-V3 (AI 推理)')
print(f'API Key present: {bool(api_key.strip())}')
print(f'Key prefix: {api_key[:10]}...')
print('='*50)

try:
    r = pipeline.run(instr, obs, 'DeepSeek-V3 (AI 推理)', api_key)
    print(f'SUCCESS: planner={r["planner_name"]}, actions={r["actions"]}')
except Exception as e:
    print('\n=== TRACEBACK ===')
    traceback.print_exc()
    print(f'\nError: {e}')
