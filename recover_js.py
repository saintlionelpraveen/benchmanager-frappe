import json

transcript_path = "/home/praveen/.gemini/antigravity-ide/brain/82e32f1b-53a1-4b48-a78f-e1a9cbe5132a/.system_generated/logs/transcript.jsonl"
with open(transcript_path, 'r') as f:
    lines = f.readlines()

for line in reversed(lines):
    data = json.loads(line)
    if 'tool_calls' in data:
        for tool in data['tool_calls']:
            if tool['name'] == 'write_to_file' and 'bench_dashboard.js' in tool['args'].get('TargetFile', ''):
                print(f"Found write_to_file at step {data['step_index']}")
                with open('/home/praveen/frappe-bench/apps/bench_manager/recovered_bench_dashboard.js', 'w') as out:
                    out.write(tool['args']['CodeContent'])
                exit()
            if tool['name'] == 'replace_file_content' and 'bench_dashboard.js' in tool['args'].get('TargetFile', ''):
                print(f"Found replace_file_content at step {data['step_index']}")
            if tool['name'] == 'multi_replace_file_content' and 'bench_dashboard.js' in tool['args'].get('TargetFile', ''):
                print(f"Found multi_replace_file_content at step {data['step_index']}")
