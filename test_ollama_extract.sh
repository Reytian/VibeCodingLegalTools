#!/bin/bash
# Test 2: Targeted field extraction

PROMPT='From the following client memory, extract ONLY these 4 fields and return them as a simple key: value list. Do not add any commentary.

Fields to extract:
1. Company legal name
2. USCI number
3. Legal representative (English and Chinese)
4. Preferred arbitration institution

Client memory:
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
- Primary Contact: john@acmeholdings.com'

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

echo "=== TEST 2: TARGETED EXTRACTION ==="
echo "=== TIME: ${ELAPSED}s ==="
echo ""
echo "$RESPONSE" | python3 -c "
import json, sys, re
data = json.load(sys.stdin)
content = data.get('message', {}).get('content', 'NO CONTENT')
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
