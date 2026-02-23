#!/bin/bash
# Test 1: Full template fill

PROMPT='You have the following client memory data:

# Client: Acme Holdings Ltd.
- Legal Name: Acme Holdings Limited
- Jurisdiction: British Virgin Islands
- Registered Address: Craigmuir Chambers, Road Town, Tortola, VG1110, BVI
- Authorized Signatory: John Smith, Director
- Tax ID: N/A (BVI entity)
- Subsidiary: Acme Trading (Shanghai) Co., Ltd. (上海阿克米贸易有限公司)
  - Jurisdiction: PRC
  - USCI: 91310000MA1FL8XH83
  - Registered Address: Room 1205, No. 100 Pudong South Road, Shanghai
  - Legal Representative: Zhang Wei (张伟)
- Preferred Governing Law: Hong Kong
- Preferred Arbitration: HKIAC
- Primary Contact: john@acmeholdings.com

Fill in ALL the blanks in the following contract excerpt using ONLY the data above. Do not add any information not present in the client memory. Output ONLY the filled contract text, nothing else.

EQUITY TRANSFER AGREEMENT

This Agreement is entered into by:

Transferor: [FULL LEGAL NAME], a company incorporated under the laws of [JURISDICTION], with its registered address at [ADDRESS] (the "Transferor"), represented by [AUTHORIZED PERSON];

Target Company: [SUBSIDIARY NAME] ([CHINESE NAME]), a limited liability company established under the laws of the People'\''s Republic of China, with USCI [USCI NUMBER] and registered address at [SUBSIDIARY ADDRESS], with legal representative [LEGAL REP] ([LEGAL REP CHINESE NAME]).'

# Build JSON payload using python to avoid escaping issues
PAYLOAD=$(python3 -c "
import json, sys
prompt = sys.stdin.read()
payload = {
    'model': 'qwen3:30b-a3b',
    'messages': [{'role': 'user', 'content': prompt}],
    'stream': False,
    'options': {'temperature': 0.1, 'num_ctx': 16384, 'num_predict': 4096}
}
print(json.dumps(payload))
" <<< "$PROMPT")

START=$(date +%s)
RESPONSE=$(curl -s --max-time 600 http://localhost:11434/api/chat -d "$PAYLOAD")
END=$(date +%s)
ELAPSED=$((END - START))

echo "=== TEST 1: FULL TEMPLATE FILL ==="
echo "=== TIME: ${ELAPSED}s ==="
echo ""
echo "$RESPONSE" | python3 -c "
import json, sys, re
data = json.load(sys.stdin)
content = data.get('message', {}).get('content', 'NO CONTENT')
# Strip <think>...</think> blocks
content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
print('=== MODEL OUTPUT (think blocks stripped) ===')
print(content)
print()
print('=== EVAL METRICS ===')
print(f'Total duration: {data.get(\"total_duration\", 0) / 1e9:.1f}s')
print(f'Prompt eval: {data.get(\"prompt_eval_duration\", 0) / 1e9:.1f}s')
print(f'Generation: {data.get(\"eval_duration\", 0) / 1e9:.1f}s')
print(f'Tokens generated: {data.get(\"eval_count\", 0)}')
eval_dur = data.get('eval_duration', 1)
eval_count = data.get('eval_count', 0)
if eval_dur > 0:
    print(f'Tokens/sec: {eval_count / (eval_dur / 1e9):.1f}')
"
