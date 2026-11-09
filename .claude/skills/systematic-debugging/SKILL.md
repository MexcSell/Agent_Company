---
name: systematic-debugging
description: Find the cause of a fault before changing code.
---

1. Reproduce it with the smallest input.
2. Read the error and the code path; form one hypothesis.
3. Test the hypothesis (a print, a test, a bisect) before fixing.
4. Fix the cause, not the symptom; keep the reproducing test.
- After two failed attempts, step back and re-read the evidence.
