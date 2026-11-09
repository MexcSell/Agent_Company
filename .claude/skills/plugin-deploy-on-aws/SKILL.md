---
name: deploy-on-aws
description: Deployment guidance for AWS (agents never deploy themselves).
---

- Agents prepare deployment plans and infrastructure code only; a person deploys.
- Least-privilege IAM, no long-lived keys in code, environments separated.
- Each plan names its rollback.
