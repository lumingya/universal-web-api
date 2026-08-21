import sys
PROJECT_ROOT = r"C:\Users\QIU\Desktop\useful\projects\普遍反代\新测试版"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scratch.test_adversarial import test_results

print(f"Total tests: {len(test_results)}")
failures = [t for t in test_results if t['status'] != 'PASS']
print(f"Total failures: {len(failures)}")
for i, f in enumerate(failures, 1):
    print(f"\n--- Failure {i} ---")
    print(f"Name: {f['name']}")
    print(f"Status: {f['status']}")
    print(f"Error: {f.get('error')}")
    if 'res' in f:
        print(f"Result: {f['res']}")
